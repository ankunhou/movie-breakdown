from movie_breakdown.application.biography_context import build_biography_contexts
from movie_breakdown.domain.base import Confidence
from movie_breakdown.domain.global_analysis import (
    ArcTurningPoint,
    Character,
    CharacterArc,
    CharacterRelation,
    StoryEvent,
)
from movie_breakdown.domain.scene_analysis import Evidence, SceneAnalysis
from movie_breakdown.domain.source import Scene, Screenplay, SourceSpan
from tests.factories import make_global_result


def _screenplay(scene_count: int = 12) -> Screenplay:
    scenes = []
    for ordinal in range(1, scene_count + 1):
        line_start = ordinal * 2 - 1
        scenes.append(
            Scene(
                id=f"scene-{ordinal:04d}",
                ordinal=ordinal,
                heading=f"地点{ordinal} 日 外",
                text=f"地点{ordinal} 日 外\n第{ordinal}场动作。",
                source_span=SourceSpan(line_start=line_start, line_end=line_start + 1),
                content_fingerprint=f"scene-fingerprint-{ordinal}",
            )
        )
    return Screenplay(title="人物小传测试", source_fingerprint="source", scenes=scenes)


def _analyses(screenplay: Screenplay) -> list[SceneAnalysis]:
    return [
        SceneAnalysis(
            scene_id=scene.id,
            summary=scene.text.splitlines()[-1],
            character_names=[],
            objectives=[],
            obstacles=[],
            core_conflict=None,
            events=[],
            state_before=[],
            state_after=[],
            revelations=[],
            suspense=[],
            foreshadowing_candidates=[],
            plot_functions=[],
            uncertainties=[],
            evidence=[],
        )
        for scene in screenplay.scenes
    ]


def _evidence(screenplay: Screenplay, scene_number: int) -> Evidence:
    scene = screenplay.scenes[scene_number - 1]
    return Evidence(
        scene_id=scene.id,
        source_span=scene.source_span,
        excerpt=scene.text,
        confidence=Confidence.HIGH,
    )


def _character(
    character_id: str,
    name: str,
    scene_numbers: list[int],
    evidence: list[Evidence] | None = None,
) -> Character:
    scene_ids = [f"scene-{number:04d}" for number in scene_numbers]
    return Character(
        id=character_id,
        name=name,
        aliases=[],
        description=f"{name}的人物描述。",
        first_scene_id=scene_ids[0],
        scene_ids=scene_ids,
        confidence=Confidence.HIGH,
        evidence=evidence or [],
    )


def _arc(character_id: str, *turning_scenes: int) -> CharacterArc:
    return CharacterArc(
        character_id=character_id,
        initial_state="初始状态",
        desire="外在欲望",
        need="内在需要",
        turning_points=[
            ArcTurningPoint(
                summary=f"第{scene_number}场发生转折",
                scene_ids=[f"scene-{scene_number:04d}"],
                evidence=[],
            )
            for scene_number in turning_scenes
        ],
        final_state="最终状态",
        evidence=[],
    )


def test_contexts_select_arc_and_top_three_characters_in_first_appearance_order() -> None:
    screenplay = _screenplay()
    character_d = _character("char-d", "丁", [4])
    character_a = _character(
        "char-a",
        "甲",
        list(range(1, 13)),
        [_evidence(screenplay, 9), _evidence(screenplay, 10)],
    )
    character_b = _character("char-b", "乙", list(range(2, 7)))
    character_c = _character("char-c", "丙", list(range(3, 6)))
    character_e = _character("char-e", "戊", [8])
    result = make_global_result()
    result.entities.characters = [
        character_d,
        character_a,
        character_b,
        character_c,
        character_e,
    ]
    result.relationships.character_arcs = [_arc("char-a", 5, 6), _arc("char-e", 8)]
    result.relationships.relationships = [
        CharacterRelation(
            id="relation-a-b",
            source_character_id="char-a",
            target_character_id="char-b",
            relation_type="同伴",
            development="甲与乙共同前进。",
            scene_ids=["scene-0007", "scene-0008"],
            evidence=[],
        )
    ]
    result.events.events = [
        StoryEvent(
            id="event-a",
            summary="甲作出选择。",
            scene_id="scene-0005",
            participant_ids=["char-a"],
            cause_event_ids=[],
            consequences=[],
            evidence=[],
        ),
        StoryEvent(
            id="event-d",
            summary="丁短暂出现。",
            scene_id="scene-0004",
            participant_ids=["char-d"],
            cause_event_ids=[],
            consequences=[],
            evidence=[],
        ),
    ]

    contexts = build_biography_contexts(screenplay, _analyses(screenplay), result)

    assert [item.character.id for item in contexts] == ["char-a", "char-b", "char-c", "char-e"]
    assert [item.id for item in contexts[0].character_index] == [
        "char-d",
        "char-a",
        "char-b",
        "char-c",
        "char-e",
    ]


def test_context_prioritizes_source_scenes_and_filters_related_artifacts() -> None:
    screenplay = _screenplay()
    character_a = _character(
        "char-a",
        "甲",
        list(range(1, 13)),
        [_evidence(screenplay, 9), _evidence(screenplay, 10)],
    )
    character_b = _character("char-b", "乙", [7, 8])
    result = make_global_result()
    result.entities.characters = [character_a, character_b]
    arc = _arc("char-a", 5, 6)
    result.relationships.character_arcs = [arc]
    relation = CharacterRelation(
        id="relation-a-b",
        source_character_id="char-a",
        target_character_id="char-b",
        relation_type="同伴",
        development="甲与乙共同前进。",
        scene_ids=["scene-0007", "scene-0008"],
        evidence=[],
    )
    result.relationships.relationships = [relation]
    event_a = StoryEvent(
        id="event-a",
        summary="甲作出选择。",
        scene_id="scene-0005",
        participant_ids=["char-a"],
        cause_event_ids=[],
        consequences=[],
        evidence=[],
    )
    event_b = event_a.model_copy(update={"id": "event-b", "participant_ids": ["char-b"]})
    result.events.events = [event_a, event_b]

    context = build_biography_contexts(screenplay, _analyses(screenplay), result)[0]

    assert [scene.id for scene in context.source_scenes] == [
        "scene-0001",
        "scene-0005",
        "scene-0006",
        "scene-0007",
        "scene-0008",
        "scene-0009",
        "scene-0010",
        "scene-0012",
    ]
    assert len(context.scene_analyses) == 12
    assert context.events == [event_a]
    assert context.relationships == [relation]
    assert context.character_arc == arc


def test_source_scene_fill_is_even_stable_and_bounded() -> None:
    screenplay = _screenplay()
    character = _character("char-a", "甲", list(range(1, 13)))
    result = make_global_result()
    result.entities.characters = [character]
    result.relationships.character_arcs = []
    result.relationships.relationships = []
    result.events.events = []

    first = build_biography_contexts(screenplay, _analyses(screenplay), result)[0]
    second = build_biography_contexts(screenplay, _analyses(screenplay), result)[0]

    expected = [
        "scene-0001",
        "scene-0002",
        "scene-0004",
        "scene-0006",
        "scene-0007",
        "scene-0009",
        "scene-0011",
        "scene-0012",
    ]
    assert [scene.id for scene in first.source_scenes] == expected
    assert first == second
    assert len(first.source_scenes) == 8
