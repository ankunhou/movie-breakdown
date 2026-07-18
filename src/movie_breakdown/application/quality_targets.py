"""把逐场和全局叙事结论转换为带上下文的人工评测候选。"""

from __future__ import annotations

from collections.abc import Iterable

from movie_breakdown.application.biography_quality_targets import collect_biography_review_targets
from movie_breakdown.domain.base import Confidence
from movie_breakdown.domain.export import NarrativeBreakdown
from movie_breakdown.domain.quality import (
    AutomaticSignal,
    QualityDimension,
    ReviewContext,
    ReviewTarget,
    ReviewTargetKind,
    SignalStatus,
)
from movie_breakdown.domain.scene_analysis import Evidence
from movie_breakdown.domain.source import Scene


def collect_review_candidates(
    breakdown: NarrativeBreakdown,
    signals: list[AutomaticSignal],
) -> list[ReviewTarget]:
    """把全部可评叙事结论转换为统一风险候选。

    Args:
        breakdown: 已通过确定性一致性校验的完整叙事拆解。
        signals: 用于提升异常引用优先级的自动代理信号。

    Returns:
        尚未执行数量裁剪的完整人工评测候选。
    """
    scenes = {scene.id: scene for scene in breakdown.screenplay.scenes}
    events = {event.id: event for event in breakdown.events.events}
    attention = {
        reference
        for signal in signals
        if signal.status == SignalStatus.ATTENTION
        for reference in signal.references
    }
    targets: list[ReviewTarget] = []
    for analysis in breakdown.scene_analyses:
        reasons = ["场景包含模型明确记录的不确定性"] if analysis.uncertainties else []
        targets.append(
            _target(
                f"scene-summary:{analysis.scene_id}",
                ReviewTargetKind.SCENE_SUMMARY,
                f"场景 {analysis.scene_id} 摘要",
                analysis.summary,
                [analysis.scene_id],
                analysis.evidence,
                [QualityDimension.SOURCE_FIDELITY, QualityDimension.UNCERTAINTY_CALIBRATION],
                scenes,
                attention,
                reasons,
            )
        )
    for event in breakdown.events.events:
        causes = [events[item].scene_id for item in event.cause_event_ids if item in events]
        claim = f"{event.summary}；原因事件：{', '.join(event.cause_event_ids) or '无'}"
        targets.append(
            _target(
                f"event:{event.id}",
                ReviewTargetKind.EVENT_CAUSALITY,
                f"事件因果：{event.id}",
                claim,
                [*causes, event.scene_id],
                event.evidence,
                [QualityDimension.SOURCE_FIDELITY, QualityDimension.CAUSAL_COHERENCE],
                scenes,
                attention,
            )
        )
    targets.extend(_structural_targets(breakdown, scenes, attention))
    targets.extend(_relationship_targets(breakdown, scenes, attention))
    targets.extend(collect_biography_review_targets(breakdown, scenes, attention))
    targets.extend(_interpretation_targets(breakdown, scenes, attention))
    return targets


def _structural_targets(
    breakdown: NarrativeBreakdown,
    scenes: dict[str, Scene],
    attention: set[str],
) -> list[ReviewTarget]:
    """构建幕、节拍、情节线和伏笔候选。"""
    targets = []
    for act in breakdown.structure.acts:
        targets.append(
            _target(
                f"act:{act.act}",
                ReviewTargetKind.ACT_TURNING_POINT,
                f"第 {act.act} 幕及转折点",
                f"{act.summary}；转折点：{act.turning_point or '未明确'}",
                act.scene_ids,
                act.evidence,
                [QualityDimension.EVIDENCE_SUFFICIENCY, QualityDimension.STRUCTURAL_PLAUSIBILITY],
                scenes,
                attention,
            )
        )
    for beat in breakdown.structure.beats:
        targets.append(
            _target(
                f"beat:{beat.id}",
                ReviewTargetKind.BEAT,
                f"叙事节拍：{beat.name}",
                beat.summary,
                beat.scene_ids,
                beat.evidence,
                [QualityDimension.EVIDENCE_SUFFICIENCY, QualityDimension.STRUCTURAL_PLAUSIBILITY],
                scenes,
                attention,
            )
        )
    for thread in breakdown.structure.plot_threads:
        targets.append(
            _target(
                f"plot:{thread.id}",
                ReviewTargetKind.PLOT_THREAD,
                f"情节线：{thread.name}",
                thread.summary,
                thread.scene_ids,
                thread.evidence,
                [QualityDimension.EVIDENCE_SUFFICIENCY, QualityDimension.CAUSAL_COHERENCE],
                scenes,
                attention,
            )
        )
    for item in breakdown.structure.foreshadowing:
        targets.append(
            _target(
                f"foreshadow:{item.id}",
                ReviewTargetKind.FORESHADOWING,
                f"伏笔：{item.id}",
                item.description,
                [*item.setup_scene_ids, *item.payoff_scene_ids],
                item.evidence,
                [QualityDimension.EVIDENCE_SUFFICIENCY, QualityDimension.CAUSAL_COHERENCE],
                scenes,
                attention,
            )
        )
    return targets


def _relationship_targets(
    breakdown: NarrativeBreakdown,
    scenes: dict[str, Scene],
    attention: set[str],
) -> list[ReviewTarget]:
    """构建人物弧光和人物关系候选。"""
    targets = []
    for arc in breakdown.relationships.character_arcs:
        scene_ids = [item for point in arc.turning_points for item in point.scene_ids]
        evidence = [
            *arc.evidence,
            *(item for point in arc.turning_points for item in point.evidence),
        ]
        claim = (
            f"{arc.initial_state} → {arc.final_state}；"
            f"欲望：{arc.desire}；需要：{arc.need or '未明确'}"
        )
        targets.append(
            _target(
                f"arc:{arc.character_id}",
                ReviewTargetKind.CHARACTER_ARC,
                f"人物弧光：{arc.character_id}",
                claim,
                scene_ids,
                evidence,
                [QualityDimension.EVIDENCE_SUFFICIENCY, QualityDimension.CHARACTER_ARC_COHERENCE],
                scenes,
                attention,
            )
        )
    for relation in breakdown.relationships.relationships:
        targets.append(
            _target(
                f"relation:{relation.id}",
                ReviewTargetKind.CHARACTER_RELATION,
                f"人物关系：{relation.id}",
                relation.development,
                relation.scene_ids,
                relation.evidence,
                [QualityDimension.EVIDENCE_SUFFICIENCY, QualityDimension.CHARACTER_ARC_COHERENCE],
                scenes,
                attention,
            )
        )
    return targets


def _interpretation_targets(
    breakdown: NarrativeBreakdown,
    scenes: dict[str, Scene],
    attention: set[str],
) -> list[ReviewTarget]:
    """为主题和母题生成明确标记共享证据局限的目标。"""
    targets = []
    values = (
        (ReviewTargetKind.THEME, breakdown.structure.themes),
        (ReviewTargetKind.MOTIF, breakdown.structure.motifs),
    )
    for kind, items in values:
        for index, claim in enumerate(items, start=1):
            label = "主题" if kind == ReviewTargetKind.THEME else "母题"
            targets.append(
                _target(
                    f"{kind.value}:{index}",
                    kind,
                    f"{label}：{claim}",
                    claim,
                    [item.scene_id for item in breakdown.structure.evidence],
                    breakdown.structure.evidence,
                    [QualityDimension.EVIDENCE_SUFFICIENCY, QualityDimension.THEME_PLAUSIBILITY],
                    scenes,
                    attention,
                    ["当前 Schema 只有共享结构证据，缺少该解释的逐项证据"],
                )
            )
    return targets


def _target(
    target_id: str,
    kind: ReviewTargetKind,
    title: str,
    claim: str,
    scene_ids: list[str],
    evidence: list[Evidence],
    dimensions: list[QualityDimension],
    scenes: dict[str, Scene],
    attention: set[str],
    initial_reasons: list[str] | None = None,
) -> ReviewTarget:
    """计算候选风险并附加至多六个原文场景上下文。"""
    reasons = list(initial_reasons or [])
    if not evidence:
        reasons.append("缺少直接证据")
    if any(item.confidence == Confidence.LOW for item in evidence):
        reasons.append("包含低置信证据")
    longitudinal = kind in {
        ReviewTargetKind.CHARACTER_ARC,
        ReviewTargetKind.CHARACTER_RELATION,
        ReviewTargetKind.PLOT_THREAD,
        ReviewTargetKind.FORESHADOWING,
    }
    if longitudinal and len({item.scene_id for item in evidence}) < 2:
        reasons.append("发展性结论缺少多场景证据")
    if target_id in attention:
        reasons.append("自动代理信号发现异常")
    if kind in {
        ReviewTargetKind.ACT_TURNING_POINT,
        ReviewTargetKind.CHARACTER_ARC,
        ReviewTargetKind.THEME,
        ReviewTargetKind.MOTIF,
    }:
        reasons.append("属于需要创作判断的解释性结论")
    context_ids = list(dict.fromkeys([*(item.scene_id for item in evidence), *scene_ids]))[:6]
    contexts = [
        ReviewContext(
            scene_id=scene_id,
            heading=scenes[scene_id].heading,
            source_span=scenes[scene_id].source_span,
            text=scenes[scene_id].text,
        )
        for scene_id in context_ids
        if scene_id in scenes
    ]
    return ReviewTarget(
        id=target_id,
        kind=kind,
        title=title,
        claim=claim,
        scene_ids=list(dict.fromkeys(scene_ids)),
        dimensions=dimensions,
        evidence=evidence,
        contexts=contexts,
        risk_score=_risk_score(reasons),
        risk_reasons=list(dict.fromkeys(reasons)),
        selection_reason="risk",
    )


def _risk_score(reasons: Iterable[str]) -> int:
    """按明确风险原因计算可解释的抽样优先级。"""
    weights = {
        "缺少直接证据": 5,
        "包含低置信证据": 3,
        "发展性结论缺少多场景证据": 3,
        "自动代理信号发现异常": 4,
        "场景包含模型明确记录的不确定性": 3,
        "当前 Schema 只有共享结构证据，缺少该解释的逐项证据": 4,
        "属于需要创作判断的解释性结论": 1,
    }
    return sum(weights.get(reason, 1) for reason in set(reasons))
