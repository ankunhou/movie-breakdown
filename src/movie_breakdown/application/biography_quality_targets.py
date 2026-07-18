"""把人物小传整体与高风险声明转换为人工评测目标。"""

from __future__ import annotations

from movie_breakdown.domain.base import Confidence
from movie_breakdown.domain.character_biography import (
    BiographyClaimBasis,
    BiographyClaimCategory,
    CharacterBiography,
    CharacterBiographyClaim,
)
from movie_breakdown.domain.export import NarrativeBreakdown
from movie_breakdown.domain.global_analysis import Character
from movie_breakdown.domain.quality import (
    QualityDimension,
    ReviewContext,
    ReviewTarget,
    ReviewTargetKind,
)
from movie_breakdown.domain.scene_analysis import Evidence
from movie_breakdown.domain.source import Scene

_BASIS_LABELS = {
    BiographyClaimBasis.OBSERVED: "剧本呈现",
    BiographyClaimBasis.REPORTED: "角色转述",
    BiographyClaimBasis.INFERRED: "分析推断",
}
_INTERPRETIVE_CATEGORIES = {
    BiographyClaimCategory.OVERVIEW,
    BiographyClaimCategory.RELATIONSHIP,
    BiographyClaimCategory.BACKSTORY,
    BiographyClaimCategory.MOTIVATION,
    BiographyClaimCategory.BELIEF,
    BiographyClaimCategory.TRAIT,
    BiographyClaimCategory.FEAR,
    BiographyClaimCategory.SECRET,
    BiographyClaimCategory.CHANGE,
    BiographyClaimCategory.SPEECH_STYLE,
    BiographyClaimCategory.DRAMATIC_FUNCTION,
}


def collect_biography_review_targets(
    breakdown: NarrativeBreakdown,
    scenes: dict[str, Scene],
    attention: set[str],
) -> list[ReviewTarget]:
    """构建人物小传整体目标和需要优先核对的单条声明。

    Args:
        breakdown: 已通过确定性一致性校验的完整叙事拆解。
        scenes: 以场景 ID 为键的完整原文场景。
        attention: 自动代理信号标记为待关注的目标引用。

    Returns:
        尚未进行数量裁剪的人物小传人工评测目标。
    """
    characters = {item.id: item for item in breakdown.entities.characters}
    targets: list[ReviewTarget] = []
    for biography in breakdown.biographies.biographies:
        character = characters.get(biography.character_id)
        if character is None:
            continue
        targets.append(_biography_target(biography, character, scenes, attention))
        claims = [biography.summary, *biography.claims]
        targets.extend(
            _claim_target(biography, character, claim, scenes, attention)
            for claim in claims
            if _is_high_risk(claim)
        )
    return targets


def _biography_target(
    biography: CharacterBiography,
    character: Character,
    scenes: dict[str, Scene],
    attention: set[str],
) -> ReviewTarget:
    """构建检查整体人物形象一致性和信息边界的目标。"""
    claims = [biography.summary, *biography.claims]
    evidence = [item for claim in claims for item in claim.evidence]
    target_id = f"biography:{biography.character_id}"
    reasons = ["人物小传需要人工判断事实、转述与推断边界"]
    if any(claim.basis == BiographyClaimBasis.INFERRED for claim in claims):
        reasons.append("人物小传包含分析推断")
    if any(claim.basis == BiographyClaimBasis.REPORTED for claim in claims):
        reasons.append("人物小传包含角色转述")
    if target_id in attention:
        reasons.append("自动代理信号发现异常")
    scene_ids = _context_scene_ids(character, evidence, biography.context_scene_ids)
    return ReviewTarget(
        id=target_id,
        kind=ReviewTargetKind.CHARACTER_BIOGRAPHY,
        title=f"人物小传：{character.name}",
        claim=_biography_claim_text(biography),
        scene_ids=scene_ids,
        dimensions=[
            QualityDimension.SOURCE_FIDELITY,
            QualityDimension.CHARACTER_PORTRAIT_COHERENCE,
            QualityDimension.UNCERTAINTY_CALIBRATION,
        ],
        evidence=evidence,
        contexts=_contexts(scene_ids, scenes),
        risk_score=_risk_score(reasons),
        risk_reasons=reasons,
        selection_reason="risk",
    )


def _claim_target(
    biography: CharacterBiography,
    character: Character,
    claim: CharacterBiographyClaim,
    scenes: dict[str, Scene],
    attention: set[str],
) -> ReviewTarget:
    """构建单条人物声明的证据、归因和推断校准目标。"""
    target_id = f"biography-claim:{biography.character_id}:{claim.id}"
    reasons = _claim_risk_reasons(claim)
    if target_id in attention:
        reasons.append("自动代理信号发现异常")
    scene_ids = _context_scene_ids(character, claim.evidence, biography.context_scene_ids)
    dimensions = [
        QualityDimension.SOURCE_FIDELITY,
        QualityDimension.EVIDENCE_SUFFICIENCY,
        QualityDimension.UNCERTAINTY_CALIBRATION,
    ]
    if claim.basis == BiographyClaimBasis.INFERRED:
        dimensions.append(QualityDimension.CHARACTER_PORTRAIT_COHERENCE)
    return ReviewTarget(
        id=target_id,
        kind=ReviewTargetKind.CHARACTER_BIOGRAPHY_CLAIM,
        title=f"人物声明：{character.name} · {claim.category.value}",
        claim=_claim_text(claim),
        scene_ids=scene_ids,
        dimensions=dimensions,
        evidence=claim.evidence,
        contexts=_contexts(scene_ids, scenes),
        risk_score=_risk_score(reasons),
        risk_reasons=reasons,
        selection_reason="risk",
    )


def _is_high_risk(claim: CharacterBiographyClaim) -> bool:
    """判断声明是否需要在整体人物小传之外单独抽检。"""
    return bool(
        claim.basis != BiographyClaimBasis.OBSERVED
        or claim.confidence == Confidence.LOW
        or claim.category in _INTERPRETIVE_CATEGORIES
    )


def _claim_risk_reasons(claim: CharacterBiographyClaim) -> list[str]:
    """给单条人物声明生成可解释风险原因。"""
    reasons: list[str] = []
    if claim.basis == BiographyClaimBasis.INFERRED:
        reasons.append("人物声明属于分析推断")
    if claim.basis == BiographyClaimBasis.REPORTED:
        reasons.append("人物声明来自角色或文本转述")
    if claim.category in _INTERPRETIVE_CATEGORIES:
        reasons.append("属于需要人物理解的解释性分类")
    if (
        claim.basis == BiographyClaimBasis.INFERRED
        and claim.category in _INTERPRETIVE_CATEGORIES
        and len({item.scene_id for item in claim.evidence}) < 2
    ):
        reasons.append("持续性人物推断只有单场证据")
    if claim.confidence == Confidence.LOW:
        reasons.append("人物声明置信度较低")
    return reasons


def _biography_claim_text(biography: CharacterBiography) -> str:
    """按依据类型清楚拼接整体人物小传待判断内容。"""
    claims = [biography.summary, *biography.claims]
    parts = [f"{_BASIS_LABELS[item.basis]}：{item.statement}" for item in claims]
    if biography.unknowns:
        parts.append(f"未知：{', '.join(item.value for item in biography.unknowns)}")
    return "；".join(parts)


def _claim_text(claim: CharacterBiographyClaim) -> str:
    """拼接单条声明的依据类型、归属、理由和备选解释。"""
    parts = [f"{_BASIS_LABELS[claim.basis]}：{claim.statement}"]
    if claim.attribution:
        parts.append(f"信息来源：{claim.attribution}")
    if claim.rationale:
        parts.append(f"推断依据：{claim.rationale}")
    if claim.alternatives:
        parts.append(f"其他可能：{'；'.join(claim.alternatives)}")
    return "；".join(parts)


def _context_scene_ids(
    character: Character,
    evidence: list[Evidence],
    context_scene_ids: list[str],
) -> list[str]:
    """按证据、首中尾出场和小传上下文选择最多六个场景。"""
    appearances = character.scene_ids
    anchors = []
    if appearances:
        anchors = [appearances[0], appearances[len(appearances) // 2], appearances[-1]]
    values = [*(item.scene_id for item in evidence), *anchors, *context_scene_ids]
    return list(dict.fromkeys(values))[:6]


def _contexts(scene_ids: list[str], scenes: dict[str, Scene]) -> list[ReviewContext]:
    """把有效场景引用转换为完整人工评审上下文。"""
    return [
        ReviewContext(
            scene_id=scene_id,
            heading=scenes[scene_id].heading,
            source_span=scenes[scene_id].source_span,
            text=scenes[scene_id].text,
        )
        for scene_id in scene_ids
        if scene_id in scenes
    ]


def _risk_score(reasons: list[str]) -> int:
    """按人物小传特有风险原因计算抽样优先级。"""
    weights = {
        "人物小传需要人工判断事实、转述与推断边界": 2,
        "人物小传包含分析推断": 3,
        "人物小传包含角色转述": 2,
        "人物声明属于分析推断": 5,
        "人物声明来自角色或文本转述": 3,
        "持续性人物推断只有单场证据": 4,
        "人物声明置信度较低": 3,
        "属于需要人物理解的解释性分类": 2,
        "自动代理信号发现异常": 4,
    }
    return sum(weights.get(reason, 1) for reason in set(reasons))
