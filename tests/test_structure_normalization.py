from movie_breakdown.application.structure_normalization import fill_unassigned_act_scenes
from movie_breakdown.domain.global_analysis import ActAnalysis, StructureAnalysis
from movie_breakdown.domain.source import Scene, SourceSpan


def _scene(ordinal: int) -> Scene:
    return Scene(
        id=f"scene-{ordinal:04d}",
        ordinal=ordinal,
        heading=f"场景 {ordinal}",
        text=f"{ordinal}、场景\n动作。",
        source_span=SourceSpan(line_start=ordinal * 2 - 1, line_end=ordinal * 2),
        content_fingerprint=f"fingerprint-{ordinal}",
    )


def _structure() -> StructureAnalysis:
    return StructureAnalysis(
        logline="测试故事。",
        synopsis="测试三幕结构中的场景遗漏。",
        acts=[
            ActAnalysis(
                act=1,
                title="建立",
                summary="建立人物。",
                scene_ids=["scene-0001", "scene-0002"],
                turning_point=None,
                evidence=[],
            ),
            ActAnalysis(
                act=2,
                title="对抗",
                summary="人物面对冲突。",
                scene_ids=["scene-0003", "scene-0005"],
                turning_point=None,
                evidence=[],
            ),
            ActAnalysis(
                act=3,
                title="解决",
                summary="冲突得到解决。",
                scene_ids=["scene-0006"],
                turning_point=None,
                evidence=[],
            ),
        ],
        beats=[],
        plot_threads=[],
        foreshadowing=[],
        themes=[],
        motifs=[],
        pacing="稳定。",
        evidence=[],
    )


def test_fill_unassigned_act_scenes_repairs_only_clear_internal_gap() -> None:
    scenes = [_scene(index) for index in range(1, 7)]

    normalized = fill_unassigned_act_scenes(_structure(), scenes)

    assert normalized.acts[1].scene_ids == ["scene-0003", "scene-0004", "scene-0005"]


def test_fill_unassigned_act_scenes_keeps_ambiguous_boundary_gap() -> None:
    structure = _structure()
    structure.acts[1].scene_ids = ["scene-0004", "scene-0005"]
    scenes = [_scene(index) for index in range(1, 7)]

    normalized = fill_unassigned_act_scenes(structure, scenes)

    assert "scene-0003" not in normalized.acts[0].scene_ids
    assert "scene-0003" not in normalized.acts[1].scene_ids


def test_fill_unassigned_act_scenes_refuses_duplicate_assignment() -> None:
    structure = _structure()
    structure.acts[0].scene_ids.append("scene-0003")
    scenes = [_scene(index) for index in range(1, 7)]

    normalized = fill_unassigned_act_scenes(structure, scenes)

    assert normalized == structure
    assert "scene-0004" not in normalized.acts[1].scene_ids


def test_fill_unassigned_act_scenes_refuses_non_monotonic_assignment() -> None:
    structure = _structure()
    structure.acts[0].scene_ids.append("scene-0004")
    scenes = [_scene(index) for index in range(1, 7)]

    normalized = fill_unassigned_act_scenes(structure, scenes)

    assert normalized == structure
