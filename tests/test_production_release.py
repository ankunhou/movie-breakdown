"""制片评测版与专业稳定版发布门禁测试。"""

from __future__ import annotations

from movie_breakdown.application.production_plan_validation import (
    ProductionPlanValidationService,
)
from movie_breakdown.application.production_planning import ProductionPlanBuilder
from movie_breakdown.application.production_release import ProductionReleaseService
from movie_breakdown.application.production_review import ProductionReviewService
from movie_breakdown.domain.production_common import ComplexityDimension, DayPhase
from movie_breakdown.domain.production_correction import ProductionCorrectionReceipt
from movie_breakdown.domain.production_planning import ProductionPlan, ResolutionStatus
from movie_breakdown.domain.production_release import (
    ProductionReleaseCheckCode,
    ProductionReleaseProfile,
    ProductionReleaseState,
)
from movie_breakdown.domain.production_review import (
    ProductionDimensionRating,
    ProductionReviewAnswers,
    ProductionReviewerKind,
    ProductionReviewReport,
    ProductionReviewResponse,
    ProductionReviewVerdict,
)
from movie_breakdown.domain.production_safety import (
    SafetyApproval,
    SafetyDecision,
    SafetyReviewerKind,
)
from movie_breakdown.domain.production_scene import ComplexityFactor
from movie_breakdown.infrastructure.fingerprint import content_fingerprint
from tests.factories import make_screenplay
from tests.production_factories import (
    make_production_breakdown,
    scene_evidence,
)


def _ready_context(*, professional: bool = False):
    screenplay = make_screenplay()
    breakdown = make_production_breakdown(screenplay)
    if professional:
        breakdown.scenes[2].complexity.factors.append(
            ComplexityFactor(
                dimension=ComplexityDimension.ACTION_SAFETY,
                score=4,
                rationale="车辆运动须专业协调。",
                related_requirement_ids=["element-train"],
                evidence=[scene_evidence(screenplay.scenes[2])],
            )
        )
    plan = ProductionPlanBuilder().build(screenplay, breakdown)
    _confirm_entities(plan)
    if professional:
        plan.safety_approvals = [
            SafetyApproval(
                hazard_id=hazard.id,
                scope_fingerprint=hazard.scope_fingerprint,
                reviewer=f"{role}专家",
                reviewer_role=role,
                reviewer_kind=SafetyReviewerKind.QUALIFIED_PROFESSIONAL,
                decision=SafetyDecision.APPROVED_WITH_CONTROLS,
                reason="已核对固定风险范围和现场预案。",
                required_controls=["封闭现场并按批准预案执行。"],
            )
            for hazard in plan.safety_hazards
            for role in hazard.required_reviewer_roles
        ]
    validation = ProductionPlanValidationService().validate(screenplay, breakdown, plan)
    return screenplay, breakdown, plan, validation


def _confirm_entities(plan: ProductionPlan) -> None:
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


def _complete_review(screenplay, breakdown, plan, *, reviewer_kind):
    service = ProductionReviewService()
    pending = service.review(screenplay, breakdown, plan)
    answers = ProductionReviewAnswers(
        plan_fingerprint=pending.plan_fingerprint,
        target_set_fingerprint=pending.target_set_fingerprint,
        rubric_version=pending.rubric_version,
        safety_policy_version=pending.safety_policy_version,
        reviewer="制片专家模拟"
        if reviewer_kind == ProductionReviewerKind.AI_SIMULATED
        else "王制片",
        reviewer_kind=reviewer_kind,
        reviewer_roles=["制片统筹"],
        responses=[
            ProductionReviewResponse(
                target_id=target.id,
                verdict=ProductionReviewVerdict.SUPPORTED,
                ratings=[
                    ProductionDimensionRating(dimension=dimension, score=5, comment="已核对。")
                    for dimension in target.dimensions
                ],
                notes="已逐行核对证据与执行边界。",
            )
            for target in pending.targets
        ],
    )
    return service.review(screenplay, breakdown, plan, answers)


def _check(report, code: ProductionReleaseCheckCode):
    return next(item for item in report.checks if item.code == code)


def test_ai_review_reaches_evaluation_ready_without_claiming_shoot_ready() -> None:
    screenplay, breakdown, plan, validation = _ready_context()
    review = _complete_review(
        screenplay,
        breakdown,
        plan,
        reviewer_kind=ProductionReviewerKind.AI_SIMULATED,
    )

    report = ProductionReleaseService().evaluate(
        plan,
        validation,
        review,
        ProductionReleaseProfile.EVALUATION,
    )

    assert validation.catalog_ready is True
    assert validation.shoot_ready is False
    assert report.releasable is True
    assert report.state == ProductionReleaseState.EVALUATION_READY
    assert _check(report, ProductionReleaseCheckCode.SAFETY_APPROVALS).passed
    assert any("AI 模拟专家" in item for item in report.limitations)


def test_unit_heuristic_is_diagnostic_but_unreviewed_boundary_blocks_release() -> None:
    screenplay = make_screenplay()
    breakdown = make_production_breakdown(screenplay)
    breakdown.scenes[0].setting.time_of_day = DayPhase.CONTINUOUS
    plan = ProductionPlanBuilder().build(screenplay, breakdown)
    _confirm_entities(plan)

    validation = ProductionPlanValidationService().validate(screenplay, breakdown, plan)
    review = ProductionReviewService().review(screenplay, breakdown, plan)
    release = ProductionReleaseService().evaluate(
        plan,
        validation,
        review,
        ProductionReleaseProfile.EVALUATION,
    )
    diagnostic = next(
        item for item in validation.issues if item.code == "planning.unit_suspected_undersplit"
    )
    unit_target = next(
        item
        for item in review.targets
        if item.kind.value == "shooting_unit" and item.references[0] == "scene-0001"
    )

    assert diagnostic.blocks_levels == []
    assert validation.catalog_ready is True
    assert "continuous" in unit_target.risk_reasons[0]
    assert release.releasable is False
    assert not _check(release, ProductionReleaseCheckCode.REVIEW_COMPLETION).passed


def test_ai_reviewer_cannot_sign_professional_release_even_when_shoot_ready() -> None:
    screenplay, breakdown, plan, validation = _ready_context(professional=True)
    review = _complete_review(
        screenplay,
        breakdown,
        plan,
        reviewer_kind=ProductionReviewerKind.AI_SIMULATED,
    )

    report = ProductionReleaseService().evaluate(
        plan,
        validation,
        review,
        ProductionReleaseProfile.PROFESSIONAL,
    )

    assert validation.shoot_ready is True
    assert report.state == ProductionReleaseState.BLOCKED
    assert not _check(report, ProductionReleaseCheckCode.REVIEWER_IDENTITY).passed
    assert _check(report, ProductionReleaseCheckCode.SAFETY_APPROVALS).passed


def test_human_expert_and_qualified_safety_approvals_reach_professional_stable() -> None:
    screenplay, breakdown, plan, validation = _ready_context(professional=True)
    review = _complete_review(
        screenplay,
        breakdown,
        plan,
        reviewer_kind=ProductionReviewerKind.HUMAN_EXPERT,
    )

    report = ProductionReleaseService().evaluate(
        plan,
        validation,
        review,
        ProductionReleaseProfile.PROFESSIONAL,
    )

    assert report.releasable is True
    assert report.state == ProductionReleaseState.PROFESSIONAL_STABLE
    assert len(report.checks) == len(ProductionReleaseCheckCode)
    assert all(item.passed for item in report.checks)


def test_release_strictly_binds_plan_validation_and_review_fingerprints() -> None:
    screenplay, breakdown, plan, validation = _ready_context()
    review = _complete_review(
        screenplay,
        breakdown,
        plan,
        reviewer_kind=ProductionReviewerKind.AI_SIMULATED,
    )
    validation.plan_fingerprint = "stale-validation"
    review.plan_fingerprint = "stale-review"
    review.target_set_fingerprint = "stale-target-set"

    report = ProductionReleaseService().evaluate(
        plan,
        validation,
        review,
        ProductionReleaseProfile.EVALUATION,
    )

    check = _check(report, ProductionReleaseCheckCode.PLAN_FINGERPRINT)
    assert check.passed is False
    assert check.references == ["review", "validation"]
    assert not _check(report, ProductionReleaseCheckCode.TARGET_SET_FINGERPRINT).passed


def test_declared_corrections_require_receipt_bound_to_current_plan() -> None:
    screenplay, breakdown, plan, validation = _ready_context()
    review = _complete_review(
        screenplay,
        breakdown,
        plan,
        reviewer_kind=ProductionReviewerKind.AI_SIMULATED,
    )
    review.responses[0].correction_ids.append("correction-001")

    blocked = ProductionReleaseService().evaluate(
        plan,
        validation,
        review,
        ProductionReleaseProfile.EVALUATION,
    )
    receipt = _receipt(plan, review)
    released = ProductionReleaseService().evaluate(
        plan,
        validation,
        review,
        ProductionReleaseProfile.EVALUATION,
        receipt,
    )

    assert not _check(blocked, ProductionReleaseCheckCode.CORRECTION_RECEIPT).passed
    assert _check(released, ProductionReleaseCheckCode.CORRECTION_RECEIPT).passed
    assert released.releasable is True


def _receipt(plan: ProductionPlan, review: ProductionReviewReport) -> ProductionCorrectionReceipt:
    return ProductionCorrectionReceipt(
        source_fingerprint=plan.source_fingerprint,
        base_plan_fingerprint="base-plan",
        corrected_plan_fingerprint=content_fingerprint(plan),
        target_set_fingerprint=review.target_set_fingerprint,
        correction_set_fingerprint="correction-set",
        review_answers_fingerprint="answers",
        rubric_version=review.rubric_version,
        safety_policy_version=review.safety_policy_version,
        reviewer=review.reviewer,
        reviewer_kind=review.reviewer_kind,
        applied_correction_ids=["correction-001"],
        applied_count=1,
    )


def test_professional_gate_independently_rejects_missing_role_approval() -> None:
    screenplay, breakdown, plan, _ = _ready_context(professional=True)
    plan.safety_approvals.pop()
    validation = ProductionPlanValidationService().validate(screenplay, breakdown, plan)
    review = _complete_review(
        screenplay,
        breakdown,
        plan,
        reviewer_kind=ProductionReviewerKind.HUMAN_EXPERT,
    )

    report = ProductionReleaseService().evaluate(
        plan,
        validation,
        review,
        ProductionReleaseProfile.PROFESSIONAL,
    )

    safety = _check(report, ProductionReleaseCheckCode.SAFETY_APPROVALS)
    assert safety.passed is False
    assert any("/" in item for item in safety.references)


def test_safety_target_cannot_be_closed_by_accepting_risk() -> None:
    screenplay, breakdown, plan, validation = _ready_context()
    review = _complete_review(
        screenplay,
        breakdown,
        plan,
        reviewer_kind=ProductionReviewerKind.AI_SIMULATED,
    )
    hazard_ids = {item.id for item in review.targets if item.kind.value == "safety_hazard"}
    for response in review.responses:
        if response.target_id in hazard_ids:
            response.verdict = ProductionReviewVerdict.ACCEPTED_RISK
            response.notes = "接受风险。"

    report = ProductionReleaseService().evaluate(
        plan,
        validation,
        review,
        ProductionReleaseProfile.EVALUATION,
    )

    assert report.releasable is False
    assert not _check(report, ProductionReleaseCheckCode.REVIEW_VERDICTS).passed
    assert not _check(report, ProductionReleaseCheckCode.SAFETY_APPROVALS).passed
