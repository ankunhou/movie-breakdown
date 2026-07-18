"""阶段产物与用户可见导出缓存的内容完整性回归测试。"""

from pathlib import Path

from movie_breakdown.domain.base import StageStatus
from movie_breakdown.domain.production_catalog import (
    ProductionBreakdown,
    ProductionValidationReport,
)
from movie_breakdown.domain.production_run import ProductionConfig
from movie_breakdown.domain.run import ProjectConfig
from movie_breakdown.infrastructure.production_storage import ProductionStore
from movie_breakdown.infrastructure.storage import ProjectStore
from movie_breakdown.pipeline.definitions import stage_versions
from movie_breakdown.pipeline.output_stages import OutputStageService
from movie_breakdown.pipeline.production_artifacts import make_production_artifact
from movie_breakdown.pipeline.production_definitions import get_production_stage
from movie_breakdown.pipeline.production_export_stage import ProductionExportStageService
from movie_breakdown.pipeline.runtime import StageRuntime
from tests.factories import make_breakdown
from tests.production_factories import make_production_catalog, make_production_records


def _parent_store(tmp_path: Path) -> ProjectStore:
    source = tmp_path / "示例剧本.txt"
    source.write_text("示例电影\n车站 日 外\n小王进站。\n", encoding="utf-8")
    store = ProjectStore(tmp_path / "project")
    store.initialize(source, ProjectConfig(), stage_versions())
    return store


def _production_breakdown() -> ProductionBreakdown:
    records = make_production_records()
    return ProductionBreakdown(
        title="示例电影",
        source_fingerprint="source",
        scenes=[record.analysis for record in records if record.analysis is not None],
        catalog=make_production_catalog(),
        validation=ProductionValidationReport(
            valid=True,
            scene_count=3,
            analyzed_scene_count=3,
            coverage=1,
            catalog_item_count=5,
            issues=[],
        ),
    )


def test_stage_runtime_rejects_artifact_with_tampered_data(tmp_path: Path) -> None:
    production = ProductionStore(_parent_store(tmp_path))
    _, manifest = production.initialize(
        production.project_store.load_project(),
        ProductionConfig(),
    )
    artifact = make_production_artifact(
        stage_name="production_catalog",
        cache_key="catalog-cache",
        data=make_production_catalog(),
        source_fingerprint="source",
        upstream_fingerprints=["scenes"],
    )
    artifact.data.locations[0].name = "被篡改的地点"
    production.write_artifact("catalog", artifact)
    runtime = StageRuntime(
        production,
        manifest,
        stage_lookup=get_production_stage,
    )

    cached = runtime.load_cached(
        "production_catalog",
        "catalog",
        type(artifact.data),
        "catalog-cache",
    )

    record = runtime.manifest.stages["production_catalog"]
    assert cached is None
    assert record.status == StageStatus.STALE
    assert record.error is not None and "内容指纹" in record.error


def test_narrative_export_repairs_tampered_cached_content(tmp_path: Path) -> None:
    store = _parent_store(tmp_path)
    runtime = StageRuntime(store, store.load_manifest())
    service = OutputStageService(runtime)
    paths = service.export(make_breakdown(), "all")
    originals = {kind: Path(path).read_bytes() for kind, path in paths.items()}

    for kind, path in paths.items():
        Path(path).write_text("已被篡改\n", encoding="utf-8")
        service.export(make_breakdown(), "all")
        assert Path(path).read_bytes() == originals[kind]


def test_production_export_repairs_every_tampered_cached_file(tmp_path: Path) -> None:
    production = ProductionStore(_parent_store(tmp_path))
    _, manifest = production.initialize(
        production.project_store.load_project(),
        ProductionConfig(),
    )
    runtime = StageRuntime(
        production,
        manifest,
        stage_lookup=get_production_stage,
    )
    service = ProductionExportStageService(runtime)
    breakdown = _production_breakdown()
    paths = service.export(breakdown, "all")
    originals = {kind: Path(path).read_bytes() for kind, path in paths.items()}

    for kind, path in paths.items():
        Path(path).write_text("已被篡改\n", encoding="utf-8")
        service.export(breakdown, "all")
        assert Path(path).read_bytes() == originals[kind]
