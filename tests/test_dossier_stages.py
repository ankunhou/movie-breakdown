from pathlib import Path

from movie_breakdown.domain.base import Confidence, StageStatus
from movie_breakdown.domain.character_dossier import CharacterDossierTier
from movie_breakdown.domain.global_analysis import Character
from movie_breakdown.domain.run import ProjectConfig
from movie_breakdown.infrastructure.fingerprint import content_fingerprint
from movie_breakdown.infrastructure.storage import ProjectStore
from movie_breakdown.pipeline.artifacts import make_artifact
from movie_breakdown.pipeline.definitions import stage_versions
from movie_breakdown.pipeline.dossier_stages import (
    CharacterDossierStageService,
    load_dossier_result,
)
from movie_breakdown.pipeline.narrative_stages import GlobalStageResult
from movie_breakdown.pipeline.runtime import StageRuntime
from tests.factories import make_global_result, make_screenplay


def _setup_stage(tmp_path: Path):
    source = tmp_path / "source.txt"
    source.write_text("示例剧本", encoding="utf-8")
    store = ProjectStore(tmp_path / "project")
    _, manifest = store.initialize(source, ProjectConfig(), stage_versions())
    screenplay = make_screenplay()
    screenplay_artifact = make_artifact(
        stage_name="scenes",
        cache_key="scenes-key",
        data=screenplay,
        source_fingerprint=screenplay.source_fingerprint,
        upstream_fingerprints=["normalized-fingerprint"],
    )
    global_content = make_global_result()
    global_result = GlobalStageResult(global_content, content_fingerprint(global_content))
    service = CharacterDossierStageService(StageRuntime(store, manifest))
    return service, store, screenplay_artifact, global_result


def test_dossier_stage_builds_all_characters_and_reuses_cache(tmp_path: Path) -> None:
    service, store, screenplay, global_result = _setup_stage(tmp_path)

    first = service.build(screenplay, global_result)
    second = CharacterDossierStageService(StageRuntime(store, store.load_manifest())).build(
        screenplay, global_result
    )

    assert second == first
    assert len(first.content.dossiers) == len(global_result.content.entities.characters)
    assert first.content.dossiers[0].tier == CharacterDossierTier.CORE
    assert load_dossier_result(store) == first
    assert load_dossier_result(store, ["wrong", "upstream"]) is None
    assert store.load_manifest().stages["character_dossiers"].status == StageStatus.SUCCESS


def test_dossier_stage_rebuilds_when_global_characters_change(tmp_path: Path) -> None:
    service, store, screenplay, global_result = _setup_stage(tmp_path)
    first = service.build(screenplay, global_result)
    global_result.content.entities.characters.append(
        Character(
            id="char-passenger",
            name="乘客",
            aliases=[],
            description="短暂出现的乘客。",
            first_scene_id="scene-0002",
            scene_ids=["scene-0002"],
            confidence=Confidence.MEDIUM,
            evidence=[],
        )
    )
    changed = GlobalStageResult(
        global_result.content,
        content_fingerprint(global_result.content),
    )

    second = CharacterDossierStageService(StageRuntime(store, store.load_manifest())).build(
        screenplay, changed
    )

    assert second.artifact_fingerprint != first.artifact_fingerprint
    assert [item.character_id for item in second.content.dossiers][-1] == "char-passenger"


def test_dossier_stage_accepts_empty_character_catalog(tmp_path: Path) -> None:
    service, _, screenplay, global_result = _setup_stage(tmp_path)
    global_result.content.entities.characters = []
    global_result.content.relationships.character_arcs = []
    global_result.content.relationships.relationships = []
    global_result.content.events.events = []
    empty = GlobalStageResult(
        global_result.content,
        content_fingerprint(global_result.content),
    )

    result = service.build(screenplay, empty)

    assert result.content.dossiers == []
