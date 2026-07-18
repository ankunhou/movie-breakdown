"""计算制片发布门禁中不可由 AI 绕过的高危复核缺口。"""

from movie_breakdown.domain import production_safety as safety
from movie_breakdown.domain.production_planning import ProductionPlan
from movie_breakdown.domain.production_review import (
    ProductionReviewReport,
    ProductionReviewTargetKind,
    ProductionReviewVerdict,
)


def missing_hazard_review_ids(
    plan: ProductionPlan,
    review: ProductionReviewReport,
) -> list[str]:
    """返回没有唯一有效安全目标响应的风险 ID。

    Args:
        plan: 当前正式制片规划。
        review: 与当前规划指纹绑定的完整评审报告。

    Returns:
        缺少唯一 `SUPPORTED` 评审响应的风险 ID。
    """
    responses = {item.target_id: item for item in review.responses}
    targets = [
        item for item in review.targets if item.kind == ProductionReviewTargetKind.SAFETY_HAZARD
    ]
    missing = []
    for hazard in plan.safety_hazards:
        matched = [item for item in targets if hazard.id in item.references]
        response = responses.get(matched[0].id) if len(matched) == 1 else None
        if response is None or response.verdict != ProductionReviewVerdict.SUPPORTED:
            missing.append(hazard.id)
    return missing


def missing_approval_scopes(plan: ProductionPlan) -> list[str]:
    """返回缺少唯一合格批准的“风险/角色”范围。

    Args:
        plan: 含风险范围与现有专业批准的正式制片规划。

    Returns:
        缺少当前范围有效专业批准的“风险 ID/角色”列表。
    """
    missing = []
    for hazard in plan.safety_hazards:
        for role in hazard.required_reviewer_roles:
            valid = [
                item
                for item in plan.safety_approvals
                if item.hazard_id == hazard.id
                and item.reviewer_role == role
                and item.scope_fingerprint == hazard.scope_fingerprint
                and item.reviewer_kind == safety.SafetyReviewerKind.QUALIFIED_PROFESSIONAL
                and item.decision in safety.CLOSED_SAFETY_DECISIONS
            ]
            if len(valid) != 1:
                missing.append(f"{hazard.id}/{role}")
    return missing
