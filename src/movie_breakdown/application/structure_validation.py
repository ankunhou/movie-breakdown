"""三幕场景归属的确定性验证与安全补齐前置判断。"""

from __future__ import annotations

from itertools import pairwise

from movie_breakdown.domain.base import Severity
from movie_breakdown.domain.global_analysis import ActAnalysis
from movie_breakdown.domain.run import ValidationIssue
from movie_breakdown.domain.source import Scene


def build_ordered_act_assignment(
    acts: list[ActAnalysis],
    scenes: list[Scene],
) -> dict[str, int] | None:
    """在三幕归属唯一、已知且不回退时构造场景到幕号的映射。

    Args:
        acts: 模型生成的三幕结构。
        scenes: 按剧本顺序排列的完整场景。

    Returns:
        可安全用于本地补齐的场景归属；任一先决条件不满足时返回空。
    """
    if [act.act for act in acts] != [1, 2, 3]:
        return None
    known_ids = {scene.id for scene in scenes}
    assignment: dict[str, int] = {}
    for act in acts:
        for scene_id in act.scene_ids:
            if scene_id not in known_ids or scene_id in assignment:
                return None
            assignment[scene_id] = act.act
    ordered_acts = [assignment[scene.id] for scene in scenes if scene.id in assignment]
    if any(left > right for left, right in pairwise(ordered_acts)):
        return None
    return assignment


def validate_act_assignments(
    acts: list[ActAnalysis],
    scenes: dict[str, Scene],
    issues: list[ValidationIssue],
) -> None:
    """报告三幕缺失、重复归属和沿剧本顺序回退的问题。

    Args:
        acts: 待校验的三幕结构。
        scenes: 场景 ID 到原始场景的索引。
        issues: 接收新增错误的可变问题列表。
    """
    if [act.act for act in acts] != [1, 2, 3]:
        issues.append(_error("structure.acts", "三幕结构必须依次包含第一、二、三幕。"))
    assignment: dict[str, int] = {}
    duplicate_ids: set[str] = set()
    for act in acts:
        for scene_id in act.scene_ids:
            if scene_id in assignment:
                duplicate_ids.add(scene_id)
            else:
                assignment[scene_id] = act.act
    for scene_id in sorted(duplicate_ids):
        issues.append(
            _error(
                "structure.duplicate_assignment",
                "场景被重复分配到多个幕或在同一幕重复出现。",
                scene_id,
            )
        )
    for missing in sorted(set(scenes) - set(assignment)):
        issues.append(_error("structure.coverage", "场景未被任何一幕覆盖。", missing))
    ordered = [(scene_id, assignment[scene_id]) for scene_id in scenes if scene_id in assignment]
    for previous, current in pairwise(ordered):
        if previous[1] <= current[1]:
            continue
        issues.append(
            _error(
                "structure.act_order",
                "幕归属沿剧本场景顺序发生回退。",
                current[0],
            )
        )
        break


def _error(code: str, message: str, reference: str | None = None) -> ValidationIssue:
    """构造三幕归属错误。"""
    return ValidationIssue(
        severity=Severity.ERROR,
        code=code,
        message=message,
        reference=reference,
    )
