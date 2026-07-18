from pathlib import Path

import pytest

from movie_breakdown.domain.run import ProjectConfig
from movie_breakdown.infrastructure.storage import ProjectExistsError, ProjectStore
from movie_breakdown.pipeline.definitions import stage_versions


def test_initialize_and_reload_project(tmp_path: Path) -> None:
    source = tmp_path / "剧本.txt"
    source.write_text("这是一份具有足够长度的剧本测试内容。", encoding="utf-8")
    store = ProjectStore(tmp_path / "project")

    project, manifest = store.initialize(source, ProjectConfig(), stage_versions())

    assert store.load_project() == project
    assert store.load_manifest().project_id == manifest.project_id
    assert store.source_path(project).read_text("utf-8") == source.read_text("utf-8")
    assert set(manifest.stages) == set(stage_versions())


def test_refuse_to_overwrite_existing_project(tmp_path: Path) -> None:
    source = tmp_path / "剧本.txt"
    source.write_text("这是一份具有足够长度的剧本测试内容。", encoding="utf-8")
    store = ProjectStore(tmp_path / "project")
    store.initialize(source, ProjectConfig(), stage_versions())

    with pytest.raises(ProjectExistsError, match="resume"):
        store.initialize(source, ProjectConfig(), stage_versions())
