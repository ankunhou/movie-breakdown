import pytest
from pydantic import ValidationError

from movie_breakdown.application.character_dossiers import RuleBasedCharacterDossierStrategy
from movie_breakdown.domain.base import Confidence
from movie_breakdown.domain.character_dossier import (
    CharacterDossierCatalog,
    CharacterDossierTier,
)
from movie_breakdown.domain.global_analysis import (
    ArcTurningPoint,
    Character,
    CharacterArc,
    CharacterRelation,
    StoryEvent,
)
from movie_breakdown.domain.source import Scene, Screenplay, SourceSpan
from tests.factories import make_global_result


def _screenplay() -> Screenplay:
    scenes = [
        Scene(
            id=f"scene-{index:04d}",
            ordinal=index,
            heading=f"地点{index} 日 外",
            text=f"地点{index} 日 外\n动作{index}。",
            source_span=SourceSpan(line_start=index * 2 - 1, line_end=index * 2),
            content_fingerprint=f"fingerprint-{index}",
        )
        for index in range(1, 11)
    ]
    return Screenplay(title="人物分级测试", source_fingerprint="source", scenes=scenes)


def _character(character_id: str, name: str, scenes: list[int]) -> Character:
    scene_ids = [f"scene-{index:04d}" for index in scenes]
    return Character(
        id=character_id,
        name=name,
        aliases=[],
        description=f"{name}的人物描述。",
        first_scene_id=next(iter(scene_ids), "scene-missing"),
        scene_ids=scene_ids,
        confidence=Confidence.HIGH,
        evidence=[],
    )


def test_strategy_builds_dossier_for_every_character_with_stable_tiers() -> None:
    screenplay = _screenplay()
    characters = [
        _character("char-a", "甲", [1]),
        _character("char-b", "乙", list(range(1, 11))),
        _character("char-c", "丙", list(range(1, 10))),
        _character("char-d", "丁", list(range(1, 9))),
        _character("char-e", "戊", [4, 5]),
        _character("char-f", "己", [5, 6]),
        _character("char-g", "庚", [7]),
        _character("char-h", "辛", [99]),
    ]
    result = make_global_result()
    result.entities.characters = characters
    result.relationships.character_arcs = [
        CharacterArc(
            character_id="char-a",
            initial_state="起点",
            desire="目标",
            need=None,
            turning_points=[ArcTurningPoint(summary="转折", scene_ids=["scene-0001"], evidence=[])],
            final_state="终点",
            evidence=[],
        )
    ]
    result.relationships.relationships = [
        CharacterRelation(
            id="relation-a-e",
            source_character_id="char-a",
            target_character_id="char-e",
            relation_type="亲属",
            development="甲与戊保持联系。",
            scene_ids=["scene-0004"],
            evidence=[],
        )
    ]
    result.events.events = [
        StoryEvent(
            id="event-f",
            summary="己执行任务。",
            scene_id="scene-0005",
            participant_ids=["char-f"],
            cause_event_ids=[],
            consequences=[],
            evidence=[],
        )
    ]

    catalog = RuleBasedCharacterDossierStrategy().build(screenplay, result)

    assert [item.character_id for item in catalog.dossiers] == [item.id for item in characters]
    tiers = {item.character_id: item.tier for item in catalog.dossiers}
    assert tiers == {
        "char-a": CharacterDossierTier.CORE,
        "char-b": CharacterDossierTier.CORE,
        "char-c": CharacterDossierTier.CORE,
        "char-d": CharacterDossierTier.CORE,
        "char-e": CharacterDossierTier.SUPPORTING,
        "char-f": CharacterDossierTier.FUNCTIONAL,
        "char-g": CharacterDossierTier.FUNCTIONAL,
        "char-h": CharacterDossierTier.BACKGROUND,
    }
    background = catalog.dossiers[-1]
    assert background.scene_ids == []
    assert background.first_scene_id is None


def test_strategy_uses_valid_unique_scenes_and_stable_frequency_ties() -> None:
    screenplay = _screenplay()
    result = make_global_result()
    result.entities.characters = [
        _character("char-late", "后出场", [3, 4, 4, 99]),
        _character("char-first", "先出场", [1, 2]),
        _character("char-middle", "中间出场", [2, 3]),
        _character("char-rest", "其他", [5]),
    ]
    result.relationships.character_arcs = []
    result.relationships.relationships = []
    result.events.events = []

    catalog = RuleBasedCharacterDossierStrategy().build(screenplay, result)
    dossiers = {item.character_id: item for item in catalog.dossiers}

    assert dossiers["char-first"].signals.top_frequency_rank == 1
    assert dossiers["char-middle"].signals.top_frequency_rank == 2
    assert dossiers["char-late"].signals.top_frequency_rank == 3
    assert dossiers["char-late"].scene_ids == ["scene-0003", "scene-0004"]
    assert dossiers["char-rest"].tier == CharacterDossierTier.FUNCTIONAL


def test_dossier_models_reject_count_mismatch_and_duplicate_characters() -> None:
    screenplay = _screenplay()
    result = make_global_result()
    dossier = RuleBasedCharacterDossierStrategy().build(screenplay, result).dossiers[0]

    with pytest.raises(ValidationError, match="统计信号与引用数量不一致"):
        dossier.model_copy(
            update={
                "signals": dossier.signals.model_copy(update={"scene_count": 99}),
            }
        ).model_validate(
            dossier.model_copy(
                update={
                    "signals": dossier.signals.model_copy(update={"scene_count": 99}),
                }
            ).model_dump()
        )

    with pytest.raises(ValidationError, match="character_id 必须唯一"):
        CharacterDossierCatalog(
            policy_version="test-v1",
            scene_recurring_threshold=5,
            event_recurring_threshold=5,
            dossiers=[dossier, dossier],
        )


def test_dossier_catalog_round_trips_through_json() -> None:
    catalog = RuleBasedCharacterDossierStrategy().build(_screenplay(), make_global_result())

    restored = CharacterDossierCatalog.model_validate_json(catalog.model_dump_json())

    assert restored == catalog


def test_character_without_valid_scene_is_not_promoted_by_top_frequency_slots() -> None:
    result = make_global_result()
    result.entities.characters = [_character("char-missing", "无场景人物", [99])]
    result.events.events = []
    result.relationships.relationships = []
    result.relationships.character_arcs = []

    dossier = RuleBasedCharacterDossierStrategy().build(_screenplay(), result).dossiers[0]

    assert dossier.tier == CharacterDossierTier.BACKGROUND
    assert dossier.signals.top_frequency_rank is None
