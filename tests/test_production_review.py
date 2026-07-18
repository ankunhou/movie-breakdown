"""制片规划强制专家目标与答案过期保护测试。"""

import pytest

from movie_breakdown.application.production_planning import ProductionPlanBuilder
from movie_breakdown.application.production_review import (
    ProductionReviewService,
    StaleProductionReviewAnswersError,
)
from movie_breakdown.domain.production_review import ProductionReviewTargetKind
from tests.factories import make_screenplay
from tests.production_factories import make_production_breakdown


def test_review_includes_every_unresolved_entity_unknown_quantity_and_hazard() -> None:
    screenplay = make_screenplay()
    breakdown = make_production_breakdown(screenplay)
    plan = ProductionPlanBuilder().build(screenplay, breakdown)

    report = ProductionReviewService().review(screenplay, breakdown, plan)
    kinds = {item.kind for item in report.targets}

    assert ProductionReviewTargetKind.ENTITY in kinds
    assert ProductionReviewTargetKind.QUANTITY in kinds
    assert ProductionReviewTargetKind.SAFETY_HAZARD in kinds
    assert sum(
        item.kind == ProductionReviewTargetKind.SAFETY_HAZARD for item in report.targets
    ) == len(plan.safety_hazards)
    assert report.coverage == 0
    assert report.complete is False


def test_review_includes_shooting_unit_target_for_every_scene() -> None:
    screenplay = make_screenplay()
    breakdown = make_production_breakdown(screenplay)
    plan = ProductionPlanBuilder().build(screenplay, breakdown)

    report = ProductionReviewService().review(screenplay, breakdown, plan)
    targets = [
        item for item in report.targets if item.kind == ProductionReviewTargetKind.SHOOTING_UNIT
    ]

    assert [item.references[0] for item in targets] == [scene.id for scene in screenplay.scenes]
    assert all(item.evidence for item in targets)
    assert all(
        any(
            reference.startswith(f"{target.references[0]}/unit-") for reference in target.references
        )
        for target in targets
    )
    assert targets[0].risk_reasons == [
        "逐场拍摄单元边界必须由专家确认，避免已有多单元中的过拆或漏拆。"
    ]


def test_answers_from_another_plan_are_rejected() -> None:
    screenplay = make_screenplay()
    breakdown = make_production_breakdown(screenplay)
    plan = ProductionPlanBuilder().build(screenplay, breakdown)
    service = ProductionReviewService()
    report = service.review(screenplay, breakdown, plan)
    answers = service.answers_template(report)
    answers.plan_fingerprint = "stale-plan"

    with pytest.raises(StaleProductionReviewAnswersError, match="过期"):
        service.review(screenplay, breakdown, plan, answers)


def test_answer_dimensions_must_belong_to_target() -> None:
    screenplay = make_screenplay()
    breakdown = make_production_breakdown(screenplay)
    plan = ProductionPlanBuilder().build(screenplay, breakdown)
    service = ProductionReviewService()
    report = service.review(screenplay, breakdown, plan)
    answers = service.answers_template(report)
    first = answers.responses[0]
    first.ratings = [*first.ratings, first.ratings[0]]

    with pytest.raises(StaleProductionReviewAnswersError, match="维度"):
        service.review(screenplay, breakdown, plan, answers)
