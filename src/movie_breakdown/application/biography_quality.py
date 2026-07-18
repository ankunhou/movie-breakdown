"""人物小传声明的可追溯性、归因和推断披露代理信号。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from movie_breakdown.domain.base import Confidence
from movie_breakdown.domain.character_biography import (
    BiographyClaimBasis,
    BiographyClaimCategory,
    CharacterBiographyClaim,
)
from movie_breakdown.domain.character_dossier import CharacterDossierTier
from movie_breakdown.domain.export import NarrativeBreakdown
from movie_breakdown.domain.quality import AutomaticSignal, SignalStatus

type ClaimItem = tuple[str, CharacterBiographyClaim]

_PERSISTENT_CATEGORIES = {
    BiographyClaimCategory.OVERVIEW,
    BiographyClaimCategory.RELATIONSHIP,
    BiographyClaimCategory.BACKSTORY,
    BiographyClaimCategory.GOAL,
    BiographyClaimCategory.MOTIVATION,
    BiographyClaimCategory.BELIEF,
    BiographyClaimCategory.TRAIT,
    BiographyClaimCategory.FEAR,
    BiographyClaimCategory.SECRET,
    BiographyClaimCategory.CHANGE,
    BiographyClaimCategory.SPEECH_STYLE,
    BiographyClaimCategory.DRAMATIC_FUNCTION,
}


def collect_biography_signals(breakdown: NarrativeBreakdown) -> list[AutomaticSignal]:
    """计算人物小传声明的确定性质量代理信号。

    Args:
        breakdown: 已通过一致性校验的完整叙事拆解。

    Returns:
        不把证据存在或推断披露冒充语义正确率的自动信号。
    """
    claims = list(_claim_items(breakdown))
    reported = [item for item in claims if item[1].basis == BiographyClaimBasis.REPORTED]
    inferred = [item for item in claims if item[1].basis == BiographyClaimBasis.INFERRED]
    persistent = [item for item in inferred if item[1].category in _PERSISTENT_CATEGORIES]
    return [
        *_dossier_signals(breakdown),
        _ratio_signal(
            "biography_claim_evidence_rate",
            "人物小传声明证据可追溯率",
            [bool(item.evidence) for _, item in claims],
            claims,
            "证据存在只说明可以回查，不表示原文足以推出人物声明。",
        ),
        _ratio_signal(
            "biography_report_attribution_rate",
            "人物转述来源标注率",
            [bool(item.attribution) for _, item in reported],
            reported,
            "来源标注不能证明转述内容真实，只防止把角色说法静默当成事实。",
        ),
        _ratio_signal(
            "biography_inference_rationale_rate",
            "人物推断依据披露率",
            [bool(item.rationale) for _, item in inferred],
            inferred,
            "写出推断依据不代表推断合理，仍需人工结合人物语境判断。",
        ),
        _ratio_signal(
            "biography_persistent_inference_multiscene_rate",
            "持续性人物推断多场景支撑率",
            [len({evidence.scene_id for evidence in item.evidence}) >= 2 for _, item in persistent],
            persistent,
            "两场证据是风险筛查阈值；单场推断不一定错误，多场证据也不自动成为事实。",
        ),
        _share_signal(
            "biography_inference_share",
            "人物小传推断声明占比",
            inferred,
            claims,
            "推断占比没有统一好坏标准，仅用于决定人工抽检强度。",
        ),
        _share_signal(
            "biography_low_confidence_inference_share",
            "低置信人物推断占比",
            [item for item in inferred if item[1].confidence == Confidence.LOW],
            inferred,
            "低置信标签由模型给出，只用于风险排序，不能替代人工判断。",
        ),
    ]


def _dossier_signals(breakdown: NarrativeBreakdown) -> list[AutomaticSignal]:
    """报告全局归一人物的档案覆盖率与层级分布。"""
    character_ids = {item.id for item in breakdown.entities.characters}
    dossiers = breakdown.dossiers.dossiers
    dossier_ids = {item.character_id for item in dossiers}
    covered = character_ids.intersection(dossier_ids)
    denominator = len(character_ids)
    missing = sorted(character_ids - dossier_ids)
    extra = sorted(dossier_ids - character_ids)
    counts = Counter(item.tier for item in dossiers)
    coverage = AutomaticSignal(
        code="character_dossier_coverage_rate",
        name="已归一人物档案覆盖率",
        value=len(covered) / denominator if denominator else None,
        numerator=len(covered),
        denominator=denominator,
        status=(
            SignalStatus.NOT_APPLICABLE
            if not denominator
            else SignalStatus.GOOD
            if not missing and not extra
            else SignalStatus.ATTENTION
        ),
        message=f"{len(covered)}/{denominator} 个已归一人物具有分级档案。",
        references=[*(f"missing:{item}" for item in missing), *(f"extra:{item}" for item in extra)],
        limitation="覆盖率只针对全局归一人物目录，不证明原文中的每个称谓都已正确归一。",
    )
    distribution = AutomaticSignal(
        code="character_dossier_tier_distribution",
        name="人物档案层级分布",
        value=None,
        numerator=len(dossiers),
        denominator=len(dossiers),
        status=SignalStatus.INFO if dossiers else SignalStatus.NOT_APPLICABLE,
        message=(
            f"核心 {counts[CharacterDossierTier.CORE]}、"
            f"重要配角 {counts[CharacterDossierTier.SUPPORTING]}、"
            f"功能人物 {counts[CharacterDossierTier.FUNCTIONAL]}、"
            f"背景／索引 {counts[CharacterDossierTier.BACKGROUND]}。"
        ),
        references=[
            item.character_id for item in dossiers if item.tier == CharacterDossierTier.BACKGROUND
        ],
        limitation="层级来自可解释规则，只用于决定分析深度，不代表人物创作价值。",
    )
    return [coverage, distribution]


def _claim_items(breakdown: NarrativeBreakdown) -> Iterable[ClaimItem]:
    """按人物目录顺序遍历带全局稳定引用的人物声明。"""
    for biography in breakdown.biographies.biographies:
        claims = [biography.summary, *biography.claims]
        for claim in claims:
            yield f"biography-claim:{biography.character_id}:{claim.id}", claim


def _ratio_signal(
    code: str,
    name: str,
    checks: list[bool],
    claims: list[ClaimItem],
    limitation: str,
) -> AutomaticSignal:
    """构造通过率型人物小传代理信号。"""
    numerator = sum(checks)
    denominator = len(checks)
    failed = [
        reference for (reference, _), passed in zip(claims, checks, strict=True) if not passed
    ]
    if denominator == 0:
        status = SignalStatus.NOT_APPLICABLE
        value = None
    else:
        status = SignalStatus.GOOD if numerator == denominator else SignalStatus.ATTENTION
        value = numerator / denominator
    return AutomaticSignal(
        code=code,
        name=name,
        value=value,
        numerator=numerator,
        denominator=denominator,
        status=status,
        message=f"{numerator}/{denominator} 条适用声明满足该结构性要求。",
        references=failed,
        limitation=limitation,
    )


def _share_signal(
    code: str,
    name: str,
    selected: list[ClaimItem],
    all_claims: list[ClaimItem],
    limitation: str,
) -> AutomaticSignal:
    """构造只提供分布信息、不设好坏阈值的人物小传信号。"""
    denominator = len(all_claims)
    return AutomaticSignal(
        code=code,
        name=name,
        value=len(selected) / denominator if denominator else None,
        numerator=len(selected),
        denominator=denominator,
        status=SignalStatus.INFO if denominator else SignalStatus.NOT_APPLICABLE,
        message=f"当前共有 {len(selected)}/{denominator} 条适用声明。",
        references=[reference for reference, _ in selected],
        limitation=limitation,
    )
