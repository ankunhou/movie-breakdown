from pathlib import Path

import pytest

from movie_breakdown.domain.production_catalog import GlobalProductionCatalog
from movie_breakdown.domain.production_run import ProductionConfig
from movie_breakdown.domain.production_scene import SceneProductionRecord
from movie_breakdown.domain.run import ProjectConfig
from movie_breakdown.infrastructure.production_storage import (
    ProductionAlreadyInitializedError,
    ProductionStore,
)
from movie_breakdown.infrastructure.storage import ProjectStore
from movie_breakdown.pipeline.definitions import stage_versions
from movie_breakdown.pipeline.production_artifacts import make_production_artifact
from movie_breakdown.pipeline.production_definitions import get_production_stage
from movie_breakdown.pipeline.runtime import StageRuntime
from tests.production_factories import make_production_catalog, make_production_records


def _parent_store(tmp_path: Path) -> ProjectStore:
    source = tmp_path / "示例剧本.txt"
    source.write_text("示例电影\n1、车站 日 外\n小王进站。\n", encoding="utf-8")
    store = ProjectStore(tmp_path / "project")
    store.initialize(source, ProjectConfig(), stage_versions())
    return store


def test_production_store_has_independent_manifest_and_config(tmp_path: Path) -> None:
    parent = _parent_store(tmp_path)
    narrative_manifest_before = parent.manifest_path.read_bytes()
    production = ProductionStore(parent)

    project, manifest = production.initialize(
        parent.load_project(),
        ProductionConfig(concurrency=2),
    )

    assert project.parent_project_id == parent.load_project().id
    assert set(manifest.stages) == {
        "production_scene_analysis",
        "production_catalog",
        "production_validation",
        "production_export",
    }
    assert parent.manifest_path.read_bytes() == narrative_manifest_before
    assert production.manifest_path.is_file()
    with pytest.raises(ProductionAlreadyInitializedError, match="已经初始化"):
        production.initialize(parent.load_project(), ProductionConfig())


def test_production_store_round_trips_artifacts_and_jsonl(tmp_path: Path) -> None:
    parent = _parent_store(tmp_path)
    production = ProductionStore(parent)
    production.initialize(parent.load_project(), ProductionConfig())
    catalog = make_production_catalog()
    artifact = make_production_artifact(
        stage_name="production_catalog",
        cache_key="catalog-cache",
        data=catalog,
        source_fingerprint="source",
        upstream_fingerprints=["scenes"],
    )

    production.write_artifact("catalog", artifact)
    production.write_jsonl("scene_elements", make_production_records())

    loaded = production.read_artifact("catalog", GlobalProductionCatalog)
    records = production.read_jsonl("scene_elements", SceneProductionRecord)
    assert loaded.data == catalog
    assert len(records) == 3
    assert records[0].analysis is not None


def test_stage_runtime_accepts_production_registry(tmp_path: Path) -> None:
    parent = _parent_store(tmp_path)
    production = ProductionStore(parent)
    _, manifest = production.initialize(parent.load_project(), ProductionConfig())
    catalog = make_production_catalog()
    artifact = make_production_artifact(
        stage_name="production_catalog",
        cache_key="catalog-cache",
        data=catalog,
        source_fingerprint="source",
        upstream_fingerprints=["scenes"],
    )
    production.write_artifact("catalog", artifact)
    runtime = StageRuntime(
        production,
        manifest,
        stage_lookup=get_production_stage,
    )

    cached = runtime.load_cached(
        "production_catalog",
        "catalog",
        GlobalProductionCatalog,
        "catalog-cache",
    )

    assert cached is not None
    assert runtime.manifest.stages["production_catalog"].status.value == "success"
    assert parent.load_manifest().stages["global_analysis"].status.value == "pending"


def test_production_store_rejects_export_path_traversal(tmp_path: Path) -> None:
    production = ProductionStore(_parent_store(tmp_path))

    with pytest.raises(ValueError, match="不能包含目录"):
        production.write_export("../report.md", "内容")
