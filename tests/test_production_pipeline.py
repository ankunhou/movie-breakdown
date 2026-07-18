import json
from pathlib import Path

import pytest

from movie_breakdown.application.ports import ModelCallResult
from movie_breakdown.application.production_pipeline import (
    ProductionPipeline,
    ProductionPipelineConfigurationError,
)
from movie_breakdown.domain.base import StageStatus
from movie_breakdown.domain.production_catalog import ProductionBreakdown
from movie_breakdown.domain.production_run import ProductionConfig
from movie_breakdown.domain.production_scene import SceneProductionRecord
from movie_breakdown.domain.run import ProjectConfig
from movie_breakdown.domain.scene_analysis import TokenUsage
from movie_breakdown.infrastructure.fingerprint import hash_bytes
from movie_breakdown.infrastructure.llm.agno_client import ModelAnalysisError
from movie_breakdown.infrastructure.llm.production_prompts import (
    production_prompt_fingerprint,
)
from movie_breakdown.infrastructure.production_storage import ProductionStore
from movie_breakdown.infrastructure.storage import ProjectStore
from movie_breakdown.pipeline.artifacts import make_artifact
from movie_breakdown.pipeline.definitions import stage_versions
from movie_breakdown.pipeline.runtime import PipelineStageError
from tests.factories import make_screenplay
from tests.production_factories import make_production_analysis


class _FakeProductionAnalyzer:
    def __init__(self, fail_once: set[str] | None = None) -> None:
        self.fail_once = fail_once or set()
        self.calls = 0

    @property
    def production_prompt_fingerprint(self) -> str:
        return production_prompt_fingerprint()

    def analyze_scene(self, scene, config):
        self.calls += 1
        if scene.id in self.fail_once:
            self.fail_once.remove(scene.id)
            raise ModelAnalysisError(
                "模拟制片模型失败",
                TokenUsage(total_tokens=7),
                2,
            )
        return ModelCallResult(make_production_analysis(scene), TokenUsage(total_tokens=10), 1)


def _parent_store(tmp_path: Path) -> ProjectStore:
    source = tmp_path / "示例剧本.txt"
    source.write_text(
        """示例电影
车站 日 外
小王进站。
月台 日 外
小王登上月台。
列车 夜 内
小王乘车离开。
""",
        encoding="utf-8",
    )
    store = ProjectStore(tmp_path / "project")
    store.initialize(source, ProjectConfig(concurrency=2), stage_versions())
    screenplay = make_screenplay().model_copy(
        update={"source_fingerprint": hash_bytes(source.read_bytes())}
    )
    artifact = make_artifact(
        stage_name="scenes",
        cache_key="scenes-cache",
        data=screenplay,
        source_fingerprint=screenplay.source_fingerprint,
        upstream_fingerprints=["normalized"],
    )
    store.write_artifact("scenes", artifact)
    return store


def _pipeline(tmp_path: Path, analyzer=None):
    parent = _parent_store(tmp_path)
    pipeline = ProductionPipeline(ProductionStore(parent), analyzer)
    pipeline.initialize(ProductionConfig(concurrency=2))
    return parent, pipeline


def test_full_production_pipeline_cache_and_storage_isolation(tmp_path: Path) -> None:
    analyzer = _FakeProductionAnalyzer()
    parent, pipeline = _pipeline(tmp_path, analyzer)
    narrative_manifest = parent.manifest_path.read_bytes()

    first = pipeline.run()
    second = pipeline.run()

    assert first.validation.valid
    assert analyzer.calls == 3
    assert parent.manifest_path.read_bytes() == narrative_manifest
    assert set(second.manifest.stages) == {
        "production_scene_analysis",
        "production_catalog",
        "production_validation",
        "production_export",
    }
    assert all(stage.status.value == "success" for stage in second.manifest.stages.values())
    for path in first.exports.values():
        assert Path(path).is_file()
        assert "production" in Path(path).parts
    payload = json.loads(Path(first.exports["json"]).read_text("utf-8"))
    markdown = Path(first.exports["markdown"]).read_text("utf-8")
    assert payload["title"] == "示例电影"
    assert payload["catalog"]["elements"][0]["name"] == "列车"
    assert "制片元素总表" in markdown
    assert "不代表预算、排期或采购数量" in markdown
    assert "高危内容必须由有资质的专业团队另行评估和批准" in markdown


def test_resume_retries_only_failed_production_scene(tmp_path: Path) -> None:
    analyzer = _FakeProductionAnalyzer({"scene-0002"})
    _, pipeline = _pipeline(tmp_path, analyzer)

    with pytest.raises(PipelineStageError, match="1 个场景制片拆解失败"):
        pipeline.run()

    result = pipeline.run()

    assert result.validation.valid
    assert analyzer.calls == 4
    assert result.manifest.stages["production_scene_analysis"].usage.total_tokens == 37


def test_validate_and_export_do_not_require_analyzer(tmp_path: Path) -> None:
    analyzer = _FakeProductionAnalyzer()
    parent, pipeline = _pipeline(tmp_path, analyzer)
    pipeline.run()
    local = ProductionPipeline(ProductionStore(parent))

    report = local.validate_only()
    paths = local.export_only("csv")

    assert report.valid
    assert set(paths) == {"scenes_csv", "catalog_csv"}
    assert "场景ID" in Path(paths["scenes_csv"]).read_text("utf-8")
    assert "制片元素" in Path(paths["catalog_csv"]).read_text("utf-8")


def test_validation_reports_incomplete_records_without_model(tmp_path: Path) -> None:
    parent, pipeline = _pipeline(tmp_path, _FakeProductionAnalyzer())
    pipeline.store.write_jsonl("scene_elements", [])
    local = ProductionPipeline(ProductionStore(parent))

    report = local.validate_only()

    assert not report.valid
    assert report.coverage == 0
    assert {issue.code for issue in report.issues} >= {
        "production.catalog_missing",
        "production.scene_coverage",
    }


def test_local_commands_reject_previous_contract_and_stale_old_outputs(
    tmp_path: Path,
) -> None:
    analyzer = _FakeProductionAnalyzer()
    parent, pipeline = _pipeline(tmp_path, analyzer)
    result = pipeline.run()
    project = pipeline.store.load_project()
    changed_config = project.config.model_copy(update={"contract_version": "2.0"})
    pipeline.store.project_store.write_model(
        pipeline.store.project_path,
        project.model_copy(update={"config": changed_config}),
    )

    local = ProductionPipeline(ProductionStore(parent))
    report = local.validate_only()
    manifest = local.status()

    assert not report.valid
    assert report.coverage == 0
    assert any(issue.code == "production.record_failed" for issue in report.issues)
    assert manifest.stages["production_scene_analysis"].status == StageStatus.STALE
    assert manifest.stages["production_catalog"].status == StageStatus.STALE
    assert manifest.stages["production_validation"].status == StageStatus.FAILED
    assert manifest.stages["production_export"].status == StageStatus.STALE
    assert all(Path(path).is_file() for path in result.exports.values())
    with pytest.raises(PipelineStageError, match="不完整或已过期"):
        local.export_only("json")
    assert analyzer.calls == 3


def test_local_export_reorders_records_by_shared_screenplay(tmp_path: Path) -> None:
    analyzer = _FakeProductionAnalyzer()
    parent, pipeline = _pipeline(tmp_path, analyzer)
    pipeline.run()
    records = pipeline.store.read_jsonl("scene_elements", SceneProductionRecord)
    pipeline.store.write_jsonl("scene_elements", list(reversed(records)))

    local = ProductionPipeline(ProductionStore(parent))
    local.export_only("json")
    artifact = local.store.read_artifact("breakdown", ProductionBreakdown)

    assert [scene.scene_id for scene in artifact.data.scenes] == [
        "scene-0001",
        "scene-0002",
        "scene-0003",
    ]
    assert analyzer.calls == 3


def test_local_validation_rejects_failed_record_and_stales_downstream(
    tmp_path: Path,
) -> None:
    analyzer = _FakeProductionAnalyzer()
    parent, pipeline = _pipeline(tmp_path, analyzer)
    pipeline.run()
    records = pipeline.store.read_jsonl("scene_elements", SceneProductionRecord)
    records[1] = records[1].model_copy(
        update={"status": StageStatus.FAILED, "error": "模拟上游失败。"}
    )
    pipeline.store.write_jsonl("scene_elements", records)

    local = ProductionPipeline(ProductionStore(parent))
    report = local.validate_only()
    manifest = local.status()

    assert not report.valid
    assert report.coverage == pytest.approx(2 / 3)
    assert manifest.stages["production_scene_analysis"].status == StageStatus.STALE
    assert manifest.stages["production_catalog"].status == StageStatus.STALE
    assert manifest.stages["production_validation"].status == StageStatus.FAILED
    assert manifest.stages["production_export"].status == StageStatus.STALE
    assert analyzer.calls == 3


def test_local_validation_rejects_corrupt_jsonl_and_stales_old_outputs(
    tmp_path: Path,
) -> None:
    analyzer = _FakeProductionAnalyzer()
    parent, pipeline = _pipeline(tmp_path, analyzer)
    pipeline.run()
    (pipeline.store.artifacts_dir / "scene_elements.jsonl").write_text(
        "{broken-json\n",
        encoding="utf-8",
    )

    local = ProductionPipeline(ProductionStore(parent))
    with pytest.raises(PipelineStageError, match="无法安全读取"):
        local.validate_only()
    manifest = local.status()

    assert all(
        manifest.stages[name].status == StageStatus.STALE
        for name in (
            "production_scene_analysis",
            "production_catalog",
            "production_validation",
            "production_export",
        )
    )
    assert analyzer.calls == 3


def test_pipeline_rejects_stale_shared_scenes(tmp_path: Path) -> None:
    parent, pipeline = _pipeline(tmp_path, _FakeProductionAnalyzer())
    project = parent.load_project()
    parent.source_path(project).write_text("剧本已经修改。\n", encoding="utf-8")

    with pytest.raises(ProductionPipelineConfigurationError, match="共享场景已相对源剧本过期"):
        pipeline.run()


def test_old_production_manifest_adds_missing_stage(tmp_path: Path) -> None:
    _, pipeline = _pipeline(tmp_path, _FakeProductionAnalyzer())
    manifest = pipeline.store.load_manifest()
    manifest.stages.pop("production_export")
    pipeline.store.save_manifest(manifest)

    repaired = pipeline.status()

    assert repaired.stages["production_export"].status.value == "pending"
