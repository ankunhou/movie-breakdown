from pathlib import Path

import pytest

from movie_breakdown.application.pipeline import AnalysisPipeline
from movie_breakdown.application.ports import ModelCallResult
from movie_breakdown.domain.base import Confidence, StageStatus
from movie_breakdown.domain.global_analysis import EventCatalog
from movie_breakdown.domain.recovery import GlobalEvidenceRecoveryReport
from movie_breakdown.domain.run import ProjectConfig
from movie_breakdown.domain.scene_analysis import (
    Evidence,
    SceneAnalysis,
    SceneAnalysisRecord,
    TokenUsage,
)
from movie_breakdown.domain.source import SourceSpan
from movie_breakdown.infrastructure.fingerprint import content_fingerprint
from movie_breakdown.infrastructure.llm.agno_analyzer import ModelAnalysisError
from movie_breakdown.infrastructure.storage import ProjectStore
from movie_breakdown.pipeline.definitions import get_stage
from movie_breakdown.pipeline.runtime import PipelineStageError
from tests.factories import make_biographies, make_global_result


class _FakeAnalyzer:
    def __init__(
        self,
        fail_once: set[str] | None = None,
        *,
        invalid_global_once: bool = False,
        unlocatable_global_once: bool = False,
        unrecoverable_global_once: bool = False,
        invalid_evidence_once: set[str] | None = None,
    ) -> None:
        self.fail_once = fail_once or set()
        self.invalid_global_once = invalid_global_once
        self.unlocatable_global_once = unlocatable_global_once
        self.unrecoverable_global_once = unrecoverable_global_once
        self.invalid_evidence_once = invalid_evidence_once or set()
        self.scene_calls = 0
        self.global_calls = 0
        self.biography_calls = 0
        self.format_calls = 0

    @property
    def format_prompt_fingerprint(self) -> str:
        return "format-v1"

    @property
    def scene_prompt_fingerprint(self) -> str:
        return "scene-v1"

    @property
    def global_prompt_fingerprint(self) -> str:
        return "global-v1"

    @property
    def biography_prompt_fingerprint(self) -> str:
        return "biography-v1"

    def detect_format(self, document, config):
        self.format_calls += 1
        raise AssertionError("标准测试剧本不应调用格式识别模型")

    def analyze_scene(self, scene, config):
        self.scene_calls += 1
        if scene.id in self.fail_once:
            self.fail_once.remove(scene.id)
            raise ModelAnalysisError(
                "模拟单场失败",
                TokenUsage(input_tokens=4, output_tokens=3, total_tokens=7),
                2,
            )
        evidence = []
        if scene.id in self.invalid_evidence_once:
            self.invalid_evidence_once.remove(scene.id)
            evidence = [
                Evidence(
                    scene_id=scene.id,
                    source_span=SourceSpan(line_start=999, line_end=999),
                    excerpt="来自另一稿的无效证据。",
                    confidence=Confidence.HIGH,
                )
            ]
        analysis = SceneAnalysis(
            scene_id=scene.id,
            summary=scene.text.splitlines()[-1],
            character_names=["小王"],
            objectives=[],
            obstacles=[],
            core_conflict=None,
            events=[],
            state_before=[],
            state_after=[],
            revelations=[],
            suspense=[],
            foreshadowing_candidates=[],
            plot_functions=["推进旅程"],
            uncertainties=[],
            evidence=evidence,
        )
        return ModelCallResult(analysis, TokenUsage(total_tokens=10), 1)

    def analyze_global(self, screenplay, analyses, config):
        self.global_calls += 1
        result = make_global_result()
        if self.invalid_global_once:
            self.invalid_global_once = False
            result.events.events[0].participant_ids = ["char-unknown"]
        if self.unlocatable_global_once or self.unrecoverable_global_once:
            result.events.events[0].evidence = [
                Evidence(
                    scene_id="scene-0003",
                    source_span=SourceSpan(line_start=999, line_end=999),
                    excerpt="来自另一稿的无效全局证据。",
                    confidence=Confidence.HIGH,
                )
            ]
            if self.unrecoverable_global_once:
                result.events.events[0].participant_ids = ["char-unknown"]
            self.unlocatable_global_once = False
            self.unrecoverable_global_once = False
        return ModelCallResult(result, TokenUsage(total_tokens=20), 1)

    def analyze_biography(self, context, config):
        self.biography_calls += 1
        biography = make_biographies().biographies[0]
        biography.character_id = context.character.id
        return ModelCallResult(biography, TokenUsage(total_tokens=5), 1)


def _source(tmp_path: Path) -> Path:
    path = tmp_path / "示例剧本.txt"
    path.write_text(
        """示例电影
1、车站 日 外
小王进站。
2、月台 日 外
小王登上月台。
3、列车 夜 内
小王乘车离开。
""",
        encoding="utf-8",
    )
    return path


def _pipeline(tmp_path: Path, analyzer: _FakeAnalyzer) -> AnalysisPipeline:
    store = ProjectStore(tmp_path / "project")
    pipeline = AnalysisPipeline(store, analyzer)
    pipeline.initialize(_source(tmp_path), ProjectConfig(concurrency=2))
    return pipeline


def test_full_pipeline_and_cache_reuse(tmp_path: Path) -> None:
    analyzer = _FakeAnalyzer()
    pipeline = _pipeline(tmp_path, analyzer)

    first = pipeline.run()
    second = pipeline.run()

    assert first.validation.valid
    assert Path(first.exports["json"]).is_file()
    assert Path(first.exports["markdown"]).is_file()
    assert all(stage.status.value == "success" for stage in second.manifest.stages.values())
    assert analyzer.scene_calls == 3
    assert analyzer.global_calls == 1
    assert analyzer.biography_calls == 1
    assert analyzer.format_calls == 0


def test_content_change_only_reanalyzes_changed_scene(tmp_path: Path) -> None:
    analyzer = _FakeAnalyzer()
    pipeline = _pipeline(tmp_path, analyzer)
    pipeline.run()
    project = pipeline.store.load_project()
    copied_source = pipeline.store.source_path(project)
    copied_source.write_text(copied_source.read_text("utf-8") + "列车驶入夜色。\n", "utf-8")

    pipeline.run()

    assert analyzer.scene_calls == 4
    assert analyzer.global_calls == 2
    assert analyzer.biography_calls == 2


def test_resume_retries_only_failed_scene(tmp_path: Path) -> None:
    analyzer = _FakeAnalyzer({"scene-0002"})
    pipeline = _pipeline(tmp_path, analyzer)

    with pytest.raises(PipelineStageError, match="1 个场景分析失败"):
        pipeline.run()

    assert pipeline.status().stages["scene_analysis"].usage.total_tokens == 27

    result = pipeline.run()
    records = {
        item.scene_id: item
        for item in pipeline.store.read_jsonl("scene_analysis", SceneAnalysisRecord)
    }

    assert result.validation.valid
    assert analyzer.scene_calls == 4
    assert analyzer.global_calls == 1
    assert analyzer.biography_calls == 1
    assert records["scene-0002"].attempts == 3
    assert records["scene-0002"].usage.total_tokens == 17
    assert result.manifest.stages["scene_analysis"].usage.total_tokens == 37


def test_new_scene_result_retries_unlocatable_evidence(tmp_path: Path) -> None:
    analyzer = _FakeAnalyzer(invalid_evidence_once={"scene-0002"})
    pipeline = _pipeline(tmp_path, analyzer)

    with pytest.raises(PipelineStageError, match="1 个场景分析失败"):
        pipeline.run()

    result = pipeline.run()
    records = {
        item.scene_id: item
        for item in pipeline.store.read_jsonl("scene_analysis", SceneAnalysisRecord)
    }

    assert result.validation.valid
    assert analyzer.scene_calls == 4
    assert records["scene-0002"].attempts == 2
    assert records["scene-0002"].usage.total_tokens == 20


def test_new_global_drops_unlocatable_evidence_with_audit(tmp_path: Path) -> None:
    analyzer = _FakeAnalyzer(unlocatable_global_once=True)
    pipeline = _pipeline(tmp_path, analyzer)

    result = pipeline.run()
    report = pipeline.store.read_model(
        pipeline.store.artifact_path("global_recovery"),
        GlobalEvidenceRecoveryReport,
    )
    events = pipeline.store.read_artifact("events", EventCatalog)

    assert result.validation.valid
    assert report.recovered is True
    assert len(report.dropped_evidence) == 1
    assert report.dropped_evidence[0].scene_id == "scene-0003"
    assert events.data.events[0].evidence == []
    assert result.manifest.stages["global_analysis"].usage.total_tokens == 20


def test_failed_global_postprocessing_usage_is_accumulated(tmp_path: Path) -> None:
    analyzer = _FakeAnalyzer(unrecoverable_global_once=True)
    pipeline = _pipeline(tmp_path, analyzer)

    with pytest.raises(PipelineStageError, match="删除坏证据后"):
        pipeline.run()

    failed = pipeline.status().stages["global_analysis"]
    assert failed.status == StageStatus.FAILED
    assert failed.usage.total_tokens == 20

    result = pipeline.run()
    events = pipeline.store.read_artifact("events", EventCatalog)
    report = pipeline.store.read_model(
        pipeline.store.artifact_path("global_recovery"),
        GlobalEvidenceRecoveryReport,
    )

    assert result.validation.valid
    assert analyzer.global_calls == 2
    assert result.manifest.stages["global_analysis"].usage.total_tokens == 40
    assert events.metadata.usage.total_tokens == 40
    assert report.recovered is False


def test_manifest_stage_version_is_refreshed_on_cache_hit(tmp_path: Path) -> None:
    analyzer = _FakeAnalyzer()
    pipeline = _pipeline(tmp_path, analyzer)
    pipeline.run()
    manifest = pipeline.store.load_manifest()
    manifest.stages["normalize"].version = "0.0"
    pipeline.store.save_manifest(manifest)

    result = pipeline.run()

    assert result.manifest.stages["normalize"].version == get_stage("normalize").version
    assert analyzer.scene_calls == 3
    assert analyzer.global_calls == 1
    assert analyzer.biography_calls == 1


def test_resume_regenerates_invalid_global_result(tmp_path: Path) -> None:
    analyzer = _FakeAnalyzer(invalid_global_once=True)
    pipeline = _pipeline(tmp_path, analyzer)

    with pytest.raises(PipelineStageError, match="一致性校验未通过"):
        pipeline.run()

    assert pipeline.status().stages["global_analysis"].status.value == "stale"

    result = pipeline.run()

    assert result.validation.valid
    assert analyzer.scene_calls == 3
    assert analyzer.global_calls == 2
    assert analyzer.biography_calls == 2


def test_stale_global_locally_drops_unlocatable_evidence(tmp_path: Path) -> None:
    analyzer = _FakeAnalyzer()
    pipeline = _pipeline(tmp_path, analyzer)
    pipeline.run()
    artifact = pipeline.store.read_artifact("events", EventCatalog)
    artifact.data.events[0].evidence = [
        Evidence(
            scene_id="scene-0001",
            source_span=SourceSpan(line_start=999, line_end=999),
            excerpt="来自另一稿且无法在当前剧本定位的文字。",
            confidence=Confidence.HIGH,
        )
    ]
    artifact.metadata.artifact_fingerprint = content_fingerprint(artifact.data)
    pipeline.store.write_artifact("events", artifact)
    manifest = pipeline.store.load_manifest()
    manifest.stages["global_analysis"].status = StageStatus.STALE
    pipeline.store.save_manifest(manifest)

    result = pipeline.run()
    repaired = pipeline.store.read_artifact("events", EventCatalog)

    assert result.validation.valid
    assert repaired.data.events[0].evidence == []
    assert analyzer.global_calls == 1


def test_stale_global_reruns_when_local_migration_leaves_error(tmp_path: Path) -> None:
    analyzer = _FakeAnalyzer()
    pipeline = _pipeline(tmp_path, analyzer)
    pipeline.run()
    artifact = pipeline.store.read_artifact("events", EventCatalog)
    artifact.data.events[0].participant_ids = ["char-missing"]
    artifact.data.events[0].evidence = [
        Evidence(
            scene_id="scene-0001",
            source_span=SourceSpan(line_start=999, line_end=999),
            excerpt="来自另一稿且无法在当前剧本定位的文字。",
            confidence=Confidence.HIGH,
        )
    ]
    artifact.metadata.artifact_fingerprint = content_fingerprint(artifact.data)
    pipeline.store.write_artifact("events", artifact)
    manifest = pipeline.store.load_manifest()
    manifest.stages["global_analysis"].status = StageStatus.STALE
    pipeline.store.save_manifest(manifest)

    result = pipeline.run()

    assert result.validation.valid
    assert analyzer.global_calls == 2


def test_old_manifest_only_runs_new_biography_stage(tmp_path: Path) -> None:
    analyzer = _FakeAnalyzer()
    pipeline = _pipeline(tmp_path, analyzer)
    pipeline.run()
    manifest = pipeline.store.load_manifest()
    manifest.stages.pop("character_biographies")
    pipeline.store.save_manifest(manifest)
    pipeline.store.artifact_path("biographies").unlink()
    (pipeline.store.artifacts_dir / "character_biographies.jsonl").unlink()

    migrated = pipeline.status()
    result = pipeline.run()

    assert migrated.stages["character_biographies"].status.value == "pending"
    assert result.validation.valid
    assert analyzer.scene_calls == 3
    assert analyzer.global_calls == 1
    assert analyzer.biography_calls == 2


def test_old_manifest_adds_dossiers_without_new_model_calls(tmp_path: Path) -> None:
    analyzer = _FakeAnalyzer()
    pipeline = _pipeline(tmp_path, analyzer)
    pipeline.run()
    manifest = pipeline.store.load_manifest()
    manifest.stages.pop("character_dossiers")
    pipeline.store.save_manifest(manifest)
    pipeline.store.artifact_path("character_dossiers").unlink()

    migrated = pipeline.status()
    result = pipeline.run()

    assert migrated.stages["character_dossiers"].status == StageStatus.PENDING
    assert result.validation.valid
    assert analyzer.scene_calls == 3
    assert analyzer.global_calls == 1
    assert analyzer.biography_calls == 1
