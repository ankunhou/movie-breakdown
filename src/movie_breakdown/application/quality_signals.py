"""从叙事拆解产物计算可解释但不冒充正确率的自动信号。"""

from __future__ import annotations

from collections.abc import Iterable

from movie_breakdown.application.biography_quality import collect_biography_signals
from movie_breakdown.application.quality_chronology import collect_chronology_signals
from movie_breakdown.domain.base import Confidence
from movie_breakdown.domain.export import NarrativeBreakdown
from movie_breakdown.domain.quality import AutomaticSignal, SignalStatus
from movie_breakdown.domain.scene_analysis import Evidence


def collect_automatic_signals(breakdown: NarrativeBreakdown) -> list[AutomaticSignal]:
    """计算证据充分度、时序一致性和结构分配等代理信号。

    Args:
        breakdown: 已通过确定性一致性校验的完整叙事拆解。

    Returns:
        带分子、分母、异常引用和局限说明的自动信号。
    """
    signals = [
        _evidence_presence(breakdown),
        _longitudinal_support(breakdown),
        *collect_biography_signals(breakdown),
        *collect_chronology_signals(breakdown),
        _dedicated_interpretation_evidence(breakdown, "theme"),
        _dedicated_interpretation_evidence(breakdown, "motif"),
        _uncertainty_share(breakdown),
        _low_confidence_share(breakdown),
    ]
    return signals


def _evidence_presence(breakdown: NarrativeBreakdown) -> AutomaticSignal:
    """统计重要结论单元是否至少携带一条直接证据。"""
    units: list[tuple[str, list[Evidence]]] = []
    for analysis in breakdown.scene_analyses:
        units.append((f"scene:{analysis.scene_id}", analysis.evidence))
        units.extend(
            (f"scene-event:{analysis.scene_id}:{index}", event.evidence)
            for index, event in enumerate(analysis.events, start=1)
        )
    units.extend((f"character:{item.id}", item.evidence) for item in breakdown.entities.characters)
    units.extend((f"location:{item.id}", item.evidence) for item in breakdown.entities.locations)
    units.extend((f"event:{item.id}", item.evidence) for item in breakdown.events.events)
    units.extend(
        (f"relation:{item.id}", item.evidence) for item in breakdown.relationships.relationships
    )
    for arc in breakdown.relationships.character_arcs:
        units.append((f"arc:{arc.character_id}", arc.evidence))
        units.extend(
            (f"arc-turn:{arc.character_id}:{index}", point.evidence)
            for index, point in enumerate(arc.turning_points, start=1)
        )
    for biography in breakdown.biographies.biographies:
        claims = [biography.summary, *biography.claims]
        units.extend(
            (
                f"biography-claim:{biography.character_id}:{claim.id}",
                claim.evidence,
            )
            for claim in claims
        )
    structure = breakdown.structure
    units.extend((f"act:{item.act}", item.evidence) for item in structure.acts)
    units.extend((f"beat:{item.id}", item.evidence) for item in structure.beats)
    units.extend((f"plot:{item.id}", item.evidence) for item in structure.plot_threads)
    units.extend((f"foreshadow:{item.id}", item.evidence) for item in structure.foreshadowing)
    units.append(("structure", structure.evidence))
    passed = sum(bool(evidence) for _, evidence in units)
    failed = [reference for reference, evidence in units if not evidence]
    return _ratio_signal(
        "evidence_presence_rate",
        "重要结论直接证据覆盖率",
        passed,
        len(units),
        failed,
        "只检查是否存在证据，不判断证据是否足以推出结论。",
    )


def _longitudinal_support(breakdown: NarrativeBreakdown) -> AutomaticSignal:
    """检查跨场发展的结论是否由至少两个场景支撑。"""
    units: list[tuple[str, list[Evidence]]] = []
    units.extend(
        (f"relation:{item.id}", item.evidence) for item in breakdown.relationships.relationships
    )
    units.extend(
        (
            f"arc:{item.character_id}",
            [
                *item.evidence,
                *(evidence for point in item.turning_points for evidence in point.evidence),
            ],
        )
        for item in breakdown.relationships.character_arcs
    )
    units.extend((f"plot:{item.id}", item.evidence) for item in breakdown.structure.plot_threads)
    units.extend(
        (f"foreshadow:{item.id}", item.evidence)
        for item in breakdown.structure.foreshadowing
        if item.status == "paid_off"
    )
    passed = sum(len({evidence.scene_id for evidence in items}) >= 2 for _, items in units)
    failed = [
        reference
        for reference, items in units
        if len({evidence.scene_id for evidence in items}) < 2
    ]
    return _ratio_signal(
        "longitudinal_multiscene_support_rate",
        "发展性结论多场景支撑率",
        passed,
        len(units),
        failed,
        "两场证据是最低可追溯要求，不代表人物弧光或关系发展判断必然正确。",
    )


def _dedicated_interpretation_evidence(
    breakdown: NarrativeBreakdown,
    kind: str,
) -> AutomaticSignal:
    """如实标记当前主题或母题缺少逐项证据字段。"""
    values = breakdown.structure.themes if kind == "theme" else breakdown.structure.motifs
    label = "主题" if kind == "theme" else "母题"
    return AutomaticSignal(
        code=f"{kind}_dedicated_evidence_rate",
        name=f"{label}逐项证据覆盖率",
        value=0.0 if values else None,
        numerator=0,
        denominator=len(values),
        status=SignalStatus.ATTENTION if values else SignalStatus.NOT_APPLICABLE,
        message=f"当前 Schema 中 {len(values)} 个{label}共用结构证据，无法逐项证明。",
        references=[f"{kind}:{index}" for index in range(1, len(values) + 1)],
        limitation=f"该信号揭示 Schema 可追溯性缺口，不表示这些{label}一定错误。",
    )


def _uncertainty_share(breakdown: NarrativeBreakdown) -> AutomaticSignal:
    """展示主动记录不确定性的场景占比。"""
    uncertain = [item.scene_id for item in breakdown.scene_analyses if item.uncertainties]
    return _info_signal(
        "scene_uncertainty_share",
        "含不确定性说明的场景占比",
        len(uncertain),
        len(breakdown.scene_analyses),
        uncertain,
        "占比高低都不直接代表质量；它用于提高这些场景的人工抽样优先级。",
    )


def _low_confidence_share(breakdown: NarrativeBreakdown) -> AutomaticSignal:
    """展示全部结论证据中低置信证据的占比。"""
    evidence = list(_all_evidence(breakdown))
    low = [
        f"evidence:{index}"
        for index, item in enumerate(evidence, start=1)
        if item.confidence == Confidence.LOW
    ]
    return _info_signal(
        "low_confidence_evidence_share",
        "低置信证据占比",
        len(low),
        len(evidence),
        low,
        "置信度由模型给出，只用于风险分层，不能作为事实正确性的独立证明。",
    )


def _all_evidence(breakdown: NarrativeBreakdown) -> Iterable[Evidence]:
    """遍历报告中可供风险统计的全部证据。"""
    for analysis in breakdown.scene_analyses:
        yield from analysis.evidence
        for event in analysis.events:
            yield from event.evidence
    for group in (
        breakdown.entities.characters,
        breakdown.entities.locations,
        breakdown.events.events,
        breakdown.relationships.relationships,
        breakdown.structure.acts,
        breakdown.structure.beats,
        breakdown.structure.plot_threads,
        breakdown.structure.foreshadowing,
    ):
        for item in group:
            yield from item.evidence
    for arc in breakdown.relationships.character_arcs:
        yield from arc.evidence
        for point in arc.turning_points:
            yield from point.evidence
    for biography in breakdown.biographies.biographies:
        yield from biography.summary.evidence
        for claim in biography.claims:
            yield from claim.evidence
        yield from biography.representative_lines
    yield from breakdown.structure.evidence


def _ratio_signal(
    code: str,
    name: str,
    numerator: int,
    denominator: int,
    references: list[str],
    limitation: str,
) -> AutomaticSignal:
    """构造具有统一阈值和不可用语义的比例信号。"""
    value = numerator / denominator if denominator else None
    status = SignalStatus.NOT_APPLICABLE
    if value is not None:
        status = SignalStatus.GOOD if value >= 0.9 else SignalStatus.ATTENTION
    message = "没有可检查对象。" if value is None else f"通过 {numerator}/{denominator}。"
    return AutomaticSignal(
        code=code,
        name=name,
        value=value,
        numerator=numerator,
        denominator=denominator,
        status=status,
        message=message,
        references=references,
        limitation=limitation,
    )


def _info_signal(
    code: str,
    name: str,
    numerator: int,
    denominator: int,
    references: list[str],
    limitation: str,
) -> AutomaticSignal:
    """构造只用于解释和抽样、不判定好坏的信息信号。"""
    value = numerator / denominator if denominator else None
    return AutomaticSignal(
        code=code,
        name=name,
        value=value,
        numerator=numerator,
        denominator=denominator,
        status=SignalStatus.INFO if denominator else SignalStatus.NOT_APPLICABLE,
        message="没有可统计对象。" if value is None else f"占比 {value:.1%}。",
        references=references,
        limitation=limitation,
    )
