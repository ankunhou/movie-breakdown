from pathlib import Path

import pytest

from movie_breakdown.domain.base import StageStatus
from movie_breakdown.domain.export import NarrativeBreakdown
from movie_breakdown.domain.run import Artifact, ProjectConfig
from movie_breakdown.infrastructure.storage import ProjectStore
from movie_breakdown.pipeline.correction_stages import ManualCorrectionStageService
from movie_breakdown.pipeline.definitions import stage_versions
from movie_breakdown.pipeline.runtime import PipelineStageError, StageRuntime
from tests.factories import make_breakdown


def _runtime(tmp_path: Path, progress=None) -> tuple[ProjectStore, StageRuntime]:
    source = tmp_path / "示例剧本.txt"
    source.write_text("示例电影\n", encoding="utf-8")
    store = ProjectStore(tmp_path / "project")
    _, manifest = store.initialize(source, ProjectConfig(), stage_versions())
    return store, StageRuntime(store, manifest, progress)


def test_stage_without_active_corrections_succeeds_and_reuses_cache(tmp_path: Path) -> None:
    events: list[tuple[str, str]] = []
    store, runtime = _runtime(tmp_path, lambda stage, message: events.append((stage, message)))
    base = make_breakdown()
    service = ManualCorrectionStageService(runtime)

    first = service.apply(base)
    artifact_before = store.artifact_path("manual_corrections").read_bytes()
    second = service.apply(base)

    assert first == base
    assert first.correction_receipt is None
    assert second == first
    assert store.artifact_path("manual_corrections").read_bytes() == artifact_before
    assert runtime.manifest.stages["manual_corrections"].status == StageStatus.SUCCESS
    assert events[-1] == ("manual_corrections", "命中有效缓存")
    persisted = store.read_artifact("manual_corrections", NarrativeBreakdown)
    assert isinstance(persisted, Artifact)
    assert persisted.data.correction_receipt is None


def test_stage_marks_invalid_active_correction_as_failed(tmp_path: Path) -> None:
    store, runtime = _runtime(tmp_path)
    (store.root / "corrections").mkdir(parents=True)
    (store.root / "corrections" / "active.json").write_text("{}", encoding="utf-8")

    with pytest.raises(PipelineStageError, match="人工叙事修正阶段失败"):
        ManualCorrectionStageService(runtime).apply(make_breakdown())

    record = store.load_manifest().stages["manual_corrections"]
    assert record.status == StageStatus.FAILED
    assert "激活修正集合无效" in (record.error or "")
