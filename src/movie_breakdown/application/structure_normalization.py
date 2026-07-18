"""对模型遗漏但可由相邻场次确定的幕归属进行本地补全。"""

from __future__ import annotations

from movie_breakdown.application.structure_validation import build_ordered_act_assignment
from movie_breakdown.domain.global_analysis import StructureAnalysis
from movie_breakdown.domain.source import Scene


def fill_unassigned_act_scenes(
    structure: StructureAnalysis,
    scenes: list[Scene],
) -> StructureAnalysis:
    """把未归幕场景补入其前后已知场景共同所属的幕。

    只修复前后最近已归幕场景属于同一幕的明确内部缺口。位于两幕边界、
    剧本首尾或完全没有邻接依据的遗漏保持不变，继续交给一致性校验报告。

    Args:
        structure: 模型生成的结构分析。
        scenes: 按剧本顺序排列的完整场景。

    Returns:
        补齐明确内部缺口并按原场景顺序排序的新结构；没有可修复项时返回原值。
    """
    assignment = build_ordered_act_assignment(structure.acts, scenes)
    if assignment is None:
        return structure
    ordered_ids = [scene.id for scene in scenes]
    additions: dict[int, list[str]] = {}
    for index, scene_id in enumerate(ordered_ids):
        if scene_id in assignment:
            continue
        previous = _nearest_assignment(ordered_ids[:index], assignment, reverse=True)
        following = _nearest_assignment(ordered_ids[index + 1 :], assignment)
        if previous is None or previous != following:
            continue
        additions.setdefault(previous, []).append(scene_id)
        assignment[scene_id] = previous
    if not additions:
        return structure
    order = {scene_id: index for index, scene_id in enumerate(ordered_ids)}
    acts = [
        act.model_copy(
            update={
                "scene_ids": sorted(
                    [*act.scene_ids, *additions.get(act.act, [])],
                    key=lambda scene_id: order.get(scene_id, len(order)),
                )
            }
        )
        for act in structure.acts
    ]
    return structure.model_copy(update={"acts": acts})


def _nearest_assignment(
    scene_ids: list[str],
    assignment: dict[str, int],
    *,
    reverse: bool = False,
) -> int | None:
    """返回指定方向上最近一个已归幕场景的幕号。"""
    values = reversed(scene_ids) if reverse else scene_ids
    return next((assignment[item] for item in values if item in assignment), None)
