from pathlib import Path

import pytest

from movie_breakdown.application.ports import ModelCallResult
from movie_breakdown.domain.base import Confidence, StageStatus
from movie_breakdown.domain.production_run import ProductionConfig
from movie_breakdown.domain.production_scene import SceneProductionRecord
from movie_breakdown.domain.run import ProjectConfig
from movie_breakdown.domain.scene_analysis import Evidence, TokenUsage
from movie_breakdown.domain.source import SourceSpan
from movie_breakdown.infrastructure.llm.agno_client import ModelAnalysisError
from movie_breakdown.infrastructure.production_storage import ProductionStore
from movie_breakdown.infrastructure.storage import ProjectStore
from movie_breakdown.pipeline.artifacts import make_artifact
from movie_breakdown.pipeline.definitions import stage_versions
from movie_breakdown.pipeline.production_definitions import get_production_stage
from movie_breakdown.pipeline.production_scene_stages import ProductionSceneStageService
from movie_breakdown.pipeline.runtime import PipelineStageError, StageRuntime
from tests.factories import make_screenplay
from tests.production_factories import make_production_analysis


class _FakeProductionAnalyzer:
    def __init__(
        self,
        fail_once: set[str] | None = None,
        invalid_evidence_once: set[str] | None = None,
        invalid_reference_once: set[str] | None = None,
        invalid_identity_once: set[str] | None = None,
    ) -> None:
        self.fail_once = fail_once or set()
        self.invalid_evidence_once = invalid_evidence_once or set()
        self.invalid_reference_once = invalid_reference_once or set()
        self.invalid_identity_once = invalid_identity_once or set()
        self.calls = 0

    @property
    def production_prompt_fingerprint(self) -> str:
        return "production-prompt-v1"

    def analyze_scene(self, scene, config):
        self.calls += 1
        if scene.id in self.fail_once:
            self.fail_once.remove(scene.id)
            raise ModelAnalysisError(
                "模拟制片模型失败",
                TokenUsage(input_tokens=4, output_tokens=3, total_tokens=7),
                2,
            )
        analysis = make_production_analysis(scene)
        if scene.id in self.invalid_evidence_once:
            self.invalid_evidence_once.remove(scene.id)
            invalid = Evidence(
                scene_id=scene.id,
                source_span=SourceSpan(line_start=999, line_end=999),
                excerpt="当前场景中不存在的证据。",
                confidence=Confidence.HIGH,
            )
            setting = analysis.setting.model_copy(update={"evidence": [invalid]})
            analysis = analysis.model_copy(update={"setting": setting})
        if scene.id in self.invalid_reference_once:
            self.invalid_reference_once.remove(scene.id)
            elements = [
                item.model_copy(update={"associated_cast_ids": ["bg-unknown"]})
                for item in analysis.elements
            ]
            analysis = analysis.model_copy(update={"elements": elements})
        if scene.id in self.invalid_identity_once:
            self.invalid_identity_once.remove(scene.id)
            setting = analysis.setting.model_copy(update={"raw_heading": f"场景：{scene.heading}"})
            analysis = analysis.model_copy(update={"scene_id": "scene-wrong", "setting": setting})
        return ModelCallResult(analysis, TokenUsage(total_tokens=10), 1)


def _parent_store(tmp_path: Path) -> ProjectStore:
    source = tmp_path / "示例剧本.txt"
    source.write_text("示例电影\n1、车站 日 外\n小王进站。\n", encoding="utf-8")
    store = ProjectStore(tmp_path / "project")
    store.initialize(source, ProjectConfig(), stage_versions())
    return store


def _service(tmp_path: Path, analyzer: _FakeProductionAnalyzer):
    parent = _parent_store(tmp_path)
    production = ProductionStore(parent)
    project, manifest = production.initialize(
        parent.load_project(),
        ProductionConfig(concurrency=2),
    )
    runtime = StageRuntime(production, manifest, stage_lookup=get_production_stage)
    screenplay = make_screenplay()
    artifact = make_artifact(
        stage_name="scenes",
        cache_key="scenes-cache",
        data=screenplay,
        source_fingerprint=screenplay.source_fingerprint,
        upstream_fingerprints=["normalized"],
    )
    return production, project, artifact, ProductionSceneStageService(runtime, analyzer)


def test_production_scene_stage_reuses_all_scene_cache(tmp_path: Path) -> None:
    analyzer = _FakeProductionAnalyzer()
    production, project, screenplay, service = _service(tmp_path, analyzer)

    first = service.analyze(project, screenplay)
    second = service.analyze(project, screenplay)

    assert analyzer.calls == 3
    assert first.artifact_fingerprint == second.artifact_fingerprint
    assert all(record.status == StageStatus.SUCCESS for record in second.records)
    assert production.load_manifest().stages["production_scene_analysis"].usage.total_tokens == 30


def test_production_scene_stage_retries_only_failed_scene(tmp_path: Path) -> None:
    analyzer = _FakeProductionAnalyzer(fail_once={"scene-0002"})
    production, project, screenplay, service = _service(tmp_path, analyzer)

    with pytest.raises(PipelineStageError, match="1 个场景制片拆解失败"):
        service.analyze(project, screenplay)

    assert production.load_manifest().stages["production_scene_analysis"].usage.total_tokens == 27

    result = service.analyze(project, screenplay)
    records = {
        record.scene_id: record
        for record in production.read_jsonl("scene_elements", SceneProductionRecord)
    }

    assert analyzer.calls == 4
    assert all(record.status == StageStatus.SUCCESS for record in result.records)
    assert records["scene-0002"].attempts == 3
    assert records["scene-0002"].usage.total_tokens == 17
    assert production.load_manifest().stages["production_scene_analysis"].usage.total_tokens == 37


def test_production_scene_stage_counts_postprocess_failure(tmp_path: Path) -> None:
    analyzer = _FakeProductionAnalyzer(invalid_evidence_once={"scene-0002"})
    production, project, screenplay, service = _service(tmp_path, analyzer)

    with pytest.raises(PipelineStageError, match="1 个场景制片拆解失败"):
        service.analyze(project, screenplay)

    result = service.analyze(project, screenplay)
    records = {
        record.scene_id: record
        for record in production.read_jsonl("scene_elements", SceneProductionRecord)
    }

    assert all(record.status == StageStatus.SUCCESS for record in result.records)
    assert analyzer.calls == 4
    assert records["scene-0002"].attempts == 2
    assert records["scene-0002"].usage.total_tokens == 20
    assert records["scene-0002"].analysis is not None


def test_production_scene_stage_recovers_dangling_optional_reference(tmp_path: Path) -> None:
    analyzer = _FakeProductionAnalyzer(invalid_reference_once={"scene-0003"})
    _, project, screenplay, service = _service(tmp_path, analyzer)

    result = service.analyze(project, screenplay)
    analysis = result.records[-1].analysis

    assert analysis is not None
    assert analysis.elements[0].associated_cast_ids == []
    assert analysis.uncertainties[-1].subject == "结构化引用待人工确认"
    assert "bg-unknown" in analysis.uncertainties[-1].description


def test_production_scene_stage_recovers_scoped_identity(tmp_path: Path) -> None:
    analyzer = _FakeProductionAnalyzer(invalid_identity_once={"scene-0002"})
    _, project, screenplay, service = _service(tmp_path, analyzer)

    result = service.analyze(project, screenplay)
    analysis = result.records[1].analysis

    assert analysis is not None
    assert analysis.scene_id == "scene-0002"
    assert analysis.setting.raw_heading == screenplay.data.scenes[1].heading


def test_production_scene_stage_invalidates_cache_when_line_numbers_move(
    tmp_path: Path,
) -> None:
    analyzer = _FakeProductionAnalyzer()
    _, project, screenplay, service = _service(tmp_path, analyzer)
    service.analyze(project, screenplay)
    first_scene = screenplay.data.scenes[0]
    moved_scene = first_scene.model_copy(
        update={"source_span": SourceSpan(line_start=101, line_end=102)}
    )
    moved_data = screenplay.data.model_copy(
        update={"scenes": [moved_scene, *screenplay.data.scenes[1:]]}
    )
    moved_artifact = make_artifact(
        stage_name="scenes",
        cache_key="moved-scenes-cache",
        data=moved_data,
        source_fingerprint=moved_data.source_fingerprint,
        upstream_fingerprints=["normalized-moved"],
    )

    result = service.analyze(project, moved_artifact)

    assert analyzer.calls == 4
    assert result.records[0].analysis is not None
    assert result.records[0].analysis.setting.evidence[0].source_span.line_start == 101


def test_production_scene_stage_removes_stale_jsonl_records(tmp_path: Path) -> None:
    analyzer = _FakeProductionAnalyzer()
    production, project, screenplay, service = _service(tmp_path, analyzer)
    result = service.analyze(project, screenplay)
    stale = result.records[0].model_copy(update={"scene_id": "scene-deleted"})
    production.write_jsonl("scene_elements", [*result.records, stale])

    service.analyze(project, screenplay)
    cleaned = production.read_jsonl("scene_elements", SceneProductionRecord)

    assert analyzer.calls == 3
    assert [record.scene_id for record in cleaned] == [
        "scene-0001",
        "scene-0002",
        "scene-0003",
    ]


def test_production_scene_stage_locally_recovers_failed_saved_content(
    tmp_path: Path,
) -> None:
    analyzer = _FakeProductionAnalyzer()
    production, project, screenplay, service = _service(tmp_path, analyzer)
    result = service.analyze(project, screenplay)
    first = result.records[0]
    assert first.analysis is not None
    evidence = first.analysis.setting.evidence[0]
    numbered = evidence.model_copy(update={"excerpt": f"1: {evidence.excerpt}"})
    setting = first.analysis.setting.model_copy(update={"evidence": [numbered]})
    raw_analysis = first.analysis.model_copy(update={"setting": setting})
    failed = first.model_copy(
        update={
            "status": StageStatus.FAILED,
            "analysis": raw_analysis,
            "error": "旧后处理无法识别编号前缀。",
        }
    )
    production.write_jsonl("scene_elements", [failed, *result.records[1:]])

    recovered = service.analyze(project, screenplay)

    assert analyzer.calls == 3
    assert recovered.records[0].status == StageStatus.SUCCESS
    assert recovered.records[0].error is None
    assert recovered.records[0].analysis is not None
    assert recovered.records[0].analysis.setting.evidence[0].excerpt == evidence.excerpt
