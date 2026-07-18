"""制片安全诊断与合格专业范围关闭的闭环测试。"""

from movie_breakdown.application.production_plan_validation import (
    ProductionPlanValidationService,
)
from movie_breakdown.application.production_planning import ProductionPlanBuilder
from movie_breakdown.application.production_release import ProductionReleaseService
from movie_breakdown.application.production_review import ProductionReviewService
from movie_breakdown.domain.production_planning import ProductionPlan, ResolutionStatus
from movie_breakdown.domain.production_release import (
    ProductionReleaseProfile,
    ProductionReleaseState,
)
from movie_breakdown.domain.production_review import (
    ProductionDimensionRating,
    ProductionReviewAnswers,
    ProductionReviewerKind,
    ProductionReviewResponse,
    ProductionReviewVerdict,
)
from movie_breakdown.domain.production_safety import (
    SafetyApproval,
    SafetyDecision,
    SafetyReviewerKind,
)
from tests.factories import make_screenplay
from tests.production_factories import make_production_breakdown


def test_missing_legacy_action_safety_factor_is_nonblocking_diagnostic() -> None:
    """验证旧复杂度标签缺失不覆盖新的逐风险专业批准门禁。"""
    screenplay = make_screenplay()
    breakdown = make_production_breakdown(screenplay)
    plan = _confirmed_plan(screenplay, breakdown)
    plan.safety_approvals = _approvals(plan, SafetyDecision.APPROVED)

    validation = ProductionPlanValidationService().validate(screenplay, breakdown, plan)
    diagnostic = next(
        item for item in validation.issues if item.code == "planning.action_safety_missing"
    )

    assert diagnostic.blocks_levels == []
    assert validation.shoot_ready is True


def test_qualified_not_applicable_closes_validation_and_professional_release() -> None:
    """验证合格专业人员可对固定范围作不适用结论并通过两层门禁。"""
    screenplay = make_screenplay()
    breakdown = make_production_breakdown(screenplay)
    plan = _confirmed_plan(screenplay, breakdown)
    plan.safety_approvals = _approvals(plan, SafetyDecision.NOT_APPLICABLE)
    validation = ProductionPlanValidationService().validate(screenplay, breakdown, plan)
    review = _complete_human_review(screenplay, breakdown, plan)

    release = ProductionReleaseService().evaluate(
        plan,
        validation,
        review,
        ProductionReleaseProfile.PROFESSIONAL,
    )

    assert validation.shoot_ready is True
    assert release.releasable is True
    assert release.state == ProductionReleaseState.PROFESSIONAL_STABLE


def _confirmed_plan(screenplay, breakdown) -> ProductionPlan:
    """构造已经确认全部连续性实体的测试规划。"""
    plan = ProductionPlanBuilder().build(screenplay, breakdown)
    plan.entities = [
        item.model_copy(update={"status": ResolutionStatus.CONFIRMED}) for item in plan.entities
    ]
    identifiers = {item.id for item in plan.entities}
    plan.occurrences = [
        item.model_copy(
            update={
                "resolution_status": (
                    ResolutionStatus.CONFIRMED
                    if item.entity_id in identifiers
                    else item.resolution_status
                )
            }
        )
        for item in plan.occurrences
    ]
    return plan


def _approvals(
    plan: ProductionPlan,
    decision: SafetyDecision,
) -> list[SafetyApproval]:
    """为每个固定风险范围和必需角色构造合格专业决定。"""
    return [
        SafetyApproval(
            hazard_id=hazard.id,
            scope_fingerprint=hazard.scope_fingerprint,
            reviewer=f"{role}专家",
            reviewer_role=role,
            reviewer_kind=SafetyReviewerKind.QUALIFIED_PROFESSIONAL,
            decision=decision,
            reason="已核对固定范围，确认无需采用该高危实现。",
        )
        for hazard in plan.safety_hazards
        for role in hazard.required_reviewer_roles
    ]


def _complete_human_review(screenplay, breakdown, plan):
    """生成完整真人专家评审报告供专业发布门禁使用。"""
    service = ProductionReviewService()
    pending = service.review(screenplay, breakdown, plan)
    answers = ProductionReviewAnswers(
        plan_fingerprint=pending.plan_fingerprint,
        target_set_fingerprint=pending.target_set_fingerprint,
        rubric_version=pending.rubric_version,
        safety_policy_version=pending.safety_policy_version,
        reviewer="王制片",
        reviewer_kind=ProductionReviewerKind.HUMAN_EXPERT,
        reviewer_roles=["制片统筹"],
        responses=[
            ProductionReviewResponse(
                target_id=target.id,
                verdict=ProductionReviewVerdict.SUPPORTED,
                ratings=[
                    ProductionDimensionRating(
                        dimension=dimension,
                        score=5,
                        comment="已核对。",
                    )
                    for dimension in target.dimensions
                ],
                notes="已逐项核对固定风险范围。",
            )
            for target in pending.targets
        ],
    )
    return service.review(screenplay, breakdown, plan, answers)
