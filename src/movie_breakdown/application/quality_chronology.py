"""叙事结构分配、因果和转折时序的自动代理信号。"""

from __future__ import annotations

from collections import Counter
from itertools import pairwise

from movie_breakdown.domain.export import NarrativeBreakdown
from movie_breakdown.domain.quality import AutomaticSignal, SignalStatus


def collect_chronology_signals(breakdown: NarrativeBreakdown) -> list[AutomaticSignal]:
    """计算幕分配、事件因果、伏笔和人物弧光的时序信号。

    Args:
        breakdown: 已通过确定性一致性校验的完整叙事拆解。

    Returns:
        一组不替代人工语义判断的时序代理信号。
    """
    return [
        _act_exclusivity(breakdown),
        _act_contiguity(breakdown),
        _act_order(breakdown),
        _causal_chronology(breakdown),
        _foreshadowing_chronology(breakdown),
        _arc_turning_order(breakdown),
    ]


def _act_exclusivity(breakdown: NarrativeBreakdown) -> AutomaticSignal:
    """检查每个场景是否恰好被分配到一幕。"""
    counts = Counter(scene_id for act in breakdown.structure.acts for scene_id in act.scene_ids)
    scene_ids = [scene.id for scene in breakdown.screenplay.scenes]
    results = [(scene_id, counts[scene_id] == 1) for scene_id in scene_ids]
    return _signal(
        "act_exclusive_assignment_rate",
        "三幕场景唯一归属率",
        results,
        "只检查场景是否重复跨幕，不判断三幕划分本身是否符合创作意图。",
    )


def _act_contiguity(breakdown: NarrativeBreakdown) -> AutomaticSignal:
    """检查每一幕包含的场景序号是否连续。"""
    ordinals = {scene.id: scene.ordinal for scene in breakdown.screenplay.scenes}
    results: list[tuple[str, bool]] = []
    for act in breakdown.structure.acts:
        values = sorted({ordinals[item] for item in act.scene_ids if item in ordinals})
        contiguous = bool(values) and values == list(range(values[0], values[-1] + 1))
        results.append((f"act:{act.act}", contiguous))
    return _signal(
        "act_contiguity_rate",
        "幕内场景连续率",
        results,
        "连续仅是经典三幕划分的结构信号，非线性叙事可能有意打破它。",
    )


def _act_order(breakdown: NarrativeBreakdown) -> AutomaticSignal:
    """检查相邻幕的场景区间是否前后有序。"""
    ordinals = {scene.id: scene.ordinal for scene in breakdown.screenplay.scenes}
    ranges = []
    for act in breakdown.structure.acts:
        values = [ordinals[item] for item in act.scene_ids if item in ordinals]
        ranges.append((act.act, min(values) if values else None, max(values) if values else None))
    results = [
        (f"act:{left[0]}->{right[0]}", bool(left[2] and right[1] and left[2] < right[1]))
        for left, right in pairwise(ranges)
    ]
    return _signal(
        "act_order_rate",
        "相邻幕时序有序率",
        results,
        "检查的是场景索引顺序，不证明转折点选择具有戏剧说服力。",
    )


def _causal_chronology(breakdown: NarrativeBreakdown) -> AutomaticSignal:
    """检查原因事件是否不晚于结果事件发生。"""
    ordinals = {scene.id: scene.ordinal for scene in breakdown.screenplay.scenes}
    events = {event.id: event for event in breakdown.events.events}
    results = []
    for effect in events.values():
        for cause_id in effect.cause_event_ids:
            cause = events.get(cause_id)
            passed = bool(
                cause and ordinals.get(cause.scene_id, 10**9) <= ordinals.get(effect.scene_id, -1)
            )
            results.append((f"event:{cause_id}->{effect.id}", passed))
    return _signal(
        "causal_chronology_rate",
        "事件因果时序一致率",
        results,
        "时序合理只是因果成立的必要条件之一，仍需人工判断因果解释。",
    )


def _foreshadowing_chronology(breakdown: NarrativeBreakdown) -> AutomaticSignal:
    """检查伏笔设置是否早于回收场景。"""
    ordinals = {scene.id: scene.ordinal for scene in breakdown.screenplay.scenes}
    results = []
    for item in breakdown.structure.foreshadowing:
        if not item.payoff_scene_ids:
            continue
        setups = [ordinals[scene_id] for scene_id in item.setup_scene_ids if scene_id in ordinals]
        payoffs = [ordinals[scene_id] for scene_id in item.payoff_scene_ids if scene_id in ordinals]
        passed = bool(setups and payoffs and min(setups) < min(payoffs))
        results.append((f"foreshadow:{item.id}", passed))
    return _signal(
        "foreshadowing_chronology_rate",
        "伏笔设置早于回收率",
        results,
        "采用首次设置与首次回收的启发式比较，不能证明作者有意设置伏笔。",
    )


def _arc_turning_order(breakdown: NarrativeBreakdown) -> AutomaticSignal:
    """检查人物弧光转折点是否按场景顺序排列。"""
    ordinals = {scene.id: scene.ordinal for scene in breakdown.screenplay.scenes}
    results = []
    for arc in breakdown.relationships.character_arcs:
        points = [
            min(ordinals[item] for item in point.scene_ids if item in ordinals)
            for point in arc.turning_points
            if any(item in ordinals for item in point.scene_ids)
        ]
        results.extend(
            (f"arc:{arc.character_id}:{index}", left <= right)
            for index, (left, right) in enumerate(pairwise(points), start=1)
        )
    return _signal(
        "arc_turning_order_rate",
        "人物弧光转折时序一致率",
        results,
        "只检查顺序，不判断欲望、需要和人物变化是否具有心理可信度。",
    )


def _signal(
    code: str,
    name: str,
    results: list[tuple[str, bool]],
    limitation: str,
) -> AutomaticSignal:
    """把一组引用及布尔结果转换为统一比例信号。"""
    numerator = sum(result for _, result in results)
    denominator = len(results)
    value = numerator / denominator if denominator else None
    status = SignalStatus.NOT_APPLICABLE
    if value is not None:
        status = SignalStatus.GOOD if value >= 0.9 else SignalStatus.ATTENTION
    return AutomaticSignal(
        code=code,
        name=name,
        value=value,
        numerator=numerator,
        denominator=denominator,
        status=status,
        message="没有可检查对象。" if value is None else f"通过 {numerator}/{denominator}。",
        references=[reference for reference, result in results if not result],
        limitation=limitation,
    )
