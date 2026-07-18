"""制片专家修正的绑定、原子性和实体归一回归测试。"""

import pytest

from movie_breakdown.application.production_correction_operations import (
    ProductionCorrectionOperationError,
)
from movie_breakdown.application.production_corrections import (
    ProductionCorrectionBindingError,
    ProductionCorrectionService,
    ProductionCorrectionTargetError,
)
from movie_breakdown.application.production_planning import ProductionPlanBuilder
from movie_breakdown.application.production_quantity_validation import (
    planned_quantity_scope_fingerprint,
)
from movie_breakdown.application.production_review import ProductionReviewService
from movie_breakdown.domain.production_correction import (
    ProductionCorrectionSet,
    ReplaceEntityRegistryCorrection,
    ReplacePlannedQuantitiesCorrection,
    ReplaceResourceClassesCorrection,
)
from movie_breakdown.domain.production_planning import (
    NormalizationBasis,
    PlannedQuantity,
    PlannedQuantityPurpose,
    ProductionPlan,
    QuantityBounds,
    ResolutionStatus,
    UnitCode,
)
from movie_breakdown.domain.production_review import (
    ProductionReviewAnswers,
    ProductionReviewerKind,
    ProductionReviewTargetKind,
    ProductionReviewVerdict,
)
from movie_breakdown.infrastructure.fingerprint import content_fingerprint
from tests.factories import make_screenplay
from tests.production_factories import make_production_breakdown


def _entity_correction_inputs() -> tuple:
    """构造一个把候选人物实体确认后回填全部出现项的修正场景。"""
    screenplay = make_screenplay()
    breakdown = make_production_breakdown(screenplay)
    plan = ProductionPlanBuilder().build(screenplay, breakdown)
    review_service = ProductionReviewService()
    report = review_service.review(screenplay, breakdown, plan)
    target = next(item for item in report.targets if item.kind == ProductionReviewTargetKind.ENTITY)
    correction_id = "correction-confirm-xiaowang"
    answers = review_service.answers_template(report)
    answers.reviewer = "AI 模拟制片专家"
    answers.reviewer_kind = ProductionReviewerKind.AI_SIMULATED
    answers.responses = [
        item.model_copy(
            update={
                "verdict": ProductionReviewVerdict.NEEDS_CORRECTION,
                "notes": "出现项均指向同一角色，但仅用于评测流程。",
                "correction_ids": [correction_id],
            }
        )
        if item.target_id == target.id
        else item
        for item in answers.responses
    ]
    replacement = [
        item.model_copy(
            update={
                "status": ResolutionStatus.CONFIRMED,
                "basis": NormalizationBasis.AI_REVIEWED,
                "notes": ["由当前评审答案确认。"],
            }
        )
        for item in plan.entities
    ]
    correction = ReplaceEntityRegistryCorrection(
        id=correction_id,
        review_target_ids=[target.id],
        expected_value_fingerprint=content_fingerprint(plan.entities),
        rationale="把同一角色的跨场出现项登记为一个连续性实体。",
        evidence=target.evidence,
        replacement=replacement,
    )
    correction_set = _correction_set(plan, report, answers, correction)
    return screenplay, breakdown, plan, answers, correction_set


def _correction_set(
    plan: ProductionPlan,
    report,
    answers: ProductionReviewAnswers,
    correction,
) -> ProductionCorrectionSet:
    """把单条测试修正与当前计划、目标集和答案全部绑定。"""
    return ProductionCorrectionSet(
        source_fingerprint=plan.source_fingerprint,
        base_plan_fingerprint=content_fingerprint(plan),
        target_set_fingerprint=report.target_set_fingerprint,
        review_answers_fingerprint=content_fingerprint(answers),
        rubric_version=report.rubric_version,
        safety_policy_version=report.safety_policy_version,
        reviewer=answers.reviewer,
        reviewer_kind=answers.reviewer_kind,
        corrections=[correction],
    )


def test_entity_registry_correction_updates_all_occurrences_atomically() -> None:
    screenplay, breakdown, plan, answers, correction_set = _entity_correction_inputs()
    before = content_fingerprint(plan)

    corrected, receipt = ProductionCorrectionService().apply(
        screenplay,
        breakdown,
        plan,
        correction_set,
        answers,
    )

    assert content_fingerprint(plan) == before
    assert all(item.status == ResolutionStatus.CONFIRMED for item in corrected.entities)
    entity_ids = {item.id for item in corrected.entities}
    assert all(
        item.resolution_status == ResolutionStatus.CONFIRMED
        for item in corrected.occurrences
        if item.entity_id in entity_ids
    )
    assert receipt.corrected_plan_fingerprint == content_fingerprint(corrected)


def test_old_value_mismatch_rejects_whole_set_without_mutation() -> None:
    screenplay, breakdown, plan, answers, correction_set = _entity_correction_inputs()
    before = content_fingerprint(plan)
    operation = correction_set.corrections[0].model_copy(
        update={"expected_value_fingerprint": "stale"}
    )
    stale = correction_set.model_copy(update={"corrections": [operation]})

    with pytest.raises(ProductionCorrectionTargetError, match="旧值指纹"):
        ProductionCorrectionService().apply(
            screenplay,
            breakdown,
            plan,
            stale,
            answers,
        )

    assert content_fingerprint(plan) == before


def test_review_response_must_reference_correction_in_both_directions() -> None:
    screenplay, breakdown, plan, answers, correction_set = _entity_correction_inputs()
    answers.responses = [
        item.model_copy(update={"correction_ids": []}) if item.correction_ids else item
        for item in answers.responses
    ]
    correction_set.review_answers_fingerprint = content_fingerprint(answers)

    with pytest.raises(ProductionCorrectionBindingError, match="双向绑定"):
        ProductionCorrectionService().apply(
            screenplay,
            breakdown,
            plan,
            correction_set,
            answers,
        )


def test_planned_quantity_is_manual_and_bound_to_current_fact_scope() -> None:
    screenplay = make_screenplay()
    breakdown = make_production_breakdown(screenplay)
    plan = ProductionPlanBuilder().build(screenplay, breakdown)
    service = ProductionReviewService()
    report = service.review(screenplay, breakdown, plan)
    target = next(
        item for item in report.targets if item.kind == ProductionReviewTargetKind.QUANTITY
    )
    correction_id = "correction-plan-practical-train"
    answers = service.answers_template(report)
    answers.reviewer = "AI 模拟制片专家"
    answers.responses = [
        response.model_copy(
            update={
                "verdict": ProductionReviewVerdict.NEEDS_CORRECTION,
                "correction_ids": [correction_id],
            }
        )
        if response.target_id == target.id
        else response
        for response in answers.responses
    ]
    occurrence = next(
        item for item in plan.occurrences if item.id == plan.quantity_facts[0].occurrence_id
    )
    resource = next(
        item for item in plan.resource_classes if item.id == occurrence.resource_class_id
    )
    planned = PlannedQuantity(
        id="planned-practical-train",
        occurrence_id=occurrence.id,
        purpose=PlannedQuantityPurpose.PRACTICAL,
        bounds=QuantityBounds(minimum=1, maximum=1),
        unit=resource.canonical_unit,
        reviewer=answers.reviewer,
        decision_id="decision-practical-train",
        input_fingerprint=planned_quantity_scope_fingerprint(plan, occurrence.id),
        rationale="评测流程中明确一列实拍资源，不把模型估算冒充剧本事实。",
    )
    operation = ReplacePlannedQuantitiesCorrection(
        id=correction_id,
        review_target_ids=[target.id],
        expected_value_fingerprint=content_fingerprint(plan.planned_quantities),
        rationale="把制作计划数量与剧本事实分离后人工登记。",
        evidence=target.evidence,
        replacement=[planned],
    )
    correction_set = _correction_set(plan, report, answers, operation)

    corrected, _ = ProductionCorrectionService().apply(
        screenplay,
        breakdown,
        plan,
        correction_set,
        answers,
    )

    assert corrected.planned_quantities == [planned]


def test_resource_class_correction_repairs_unit_without_changing_ids() -> None:
    screenplay, breakdown, plan, answers, correction_set, resource_id = (
        _resource_correction_inputs()
    )
    before_ids = {item.id for item in plan.resource_classes}

    corrected, _ = ProductionCorrectionService().apply(
        screenplay,
        breakdown,
        plan,
        correction_set,
        answers,
    )

    assert {item.id for item in corrected.resource_classes} == before_ids
    resource = next(item for item in corrected.resource_classes if item.id == resource_id)
    assert resource.canonical_unit == UnitCode.VEHICLE


def test_resource_class_correction_is_discriminated_single_global_scope() -> None:
    _, _, _, _, correction_set, _ = _resource_correction_inputs()
    parsed = ProductionCorrectionSet.model_validate(correction_set.model_dump(mode="python"))
    operation = parsed.corrections[0]

    assert isinstance(operation, ReplaceResourceClassesCorrection)
    payload = parsed.model_dump(mode="python")
    payload["corrections"].append(
        operation.model_copy(update={"id": "correction-standardize-resource-unit-2"})
    )
    with pytest.raises(ValueError, match="同一作用域"):
        ProductionCorrectionSet.model_validate(payload)


def test_resource_class_correction_rejects_changed_id_set_atomically() -> None:
    screenplay, breakdown, plan, answers, correction_set, resource_id = (
        _resource_correction_inputs()
    )
    before = content_fingerprint(plan)
    operation = correction_set.corrections[0]
    replacement = [
        item.model_copy(update={"id": "resource-replaced"}) if item.id == resource_id else item
        for item in operation.replacement
    ]
    invalid = correction_set.model_copy(
        update={"corrections": [operation.model_copy(update={"replacement": replacement})]}
    )

    with pytest.raises(ProductionCorrectionOperationError, match="稳定 ID"):
        ProductionCorrectionService().apply(
            screenplay,
            breakdown,
            plan,
            invalid,
            answers,
        )

    assert content_fingerprint(plan) == before


def test_global_correction_supports_full_target_set_and_rejects_duplicates() -> None:
    _, _, _, _, correction_set = _entity_correction_inputs()
    operation = correction_set.corrections[0]
    payload = operation.model_dump(mode="python")
    payload["review_target_ids"] = [f"review-entity-{index:03d}" for index in range(262)]

    parsed = ReplaceEntityRegistryCorrection.model_validate(payload)

    assert len(parsed.review_target_ids) == 262
    payload["review_target_ids"] = ["review-entity-001", "review-entity-001"]
    with pytest.raises(ValueError, match="重复绑定"):
        ReplaceEntityRegistryCorrection.model_validate(payload)


def _resource_correction_inputs() -> tuple:
    """构造通过完整资源类别替换恢复标准单位的修正场景。"""
    screenplay = make_screenplay()
    breakdown = make_production_breakdown(screenplay)
    plan = ProductionPlanBuilder().build(screenplay, breakdown)
    fact = plan.quantity_facts[0]
    occurrence = next(item for item in plan.occurrences if item.id == fact.occurrence_id)
    resource_id = occurrence.resource_class_id
    plan.resource_classes = [
        item.model_copy(update={"canonical_unit": UnitCode.UNKNOWN})
        if item.id == resource_id
        else item
        for item in plan.resource_classes
    ]
    service = ProductionReviewService()
    report = service.review(screenplay, breakdown, plan)
    target = next(
        item for item in report.targets if item.kind == ProductionReviewTargetKind.QUANTITY
    )
    correction_id = "correction-standardize-resource-unit"
    answers = service.answers_template(report)
    answers.reviewer = "AI 模拟制片专家"
    answers.responses = [
        response.model_copy(
            update={
                "verdict": ProductionReviewVerdict.NEEDS_CORRECTION,
                "correction_ids": [correction_id],
            }
        )
        if response.target_id == target.id
        else response
        for response in answers.responses
    ]
    replacement = [
        item.model_copy(
            update={
                "canonical_unit": fact.unit,
                "basis": NormalizationBasis.AI_REVIEWED,
            }
        )
        if item.id == resource_id
        else item
        for item in plan.resource_classes
    ]
    operation = ReplaceResourceClassesCorrection(
        id=correction_id,
        review_target_ids=[target.id],
        expected_value_fingerprint=content_fingerprint(plan.resource_classes),
        rationale="把资源类别标准单位与同一出现项的剧本数量事实对齐。",
        evidence=target.evidence,
        replacement=replacement,
    )
    correction_set = _correction_set(plan, report, answers, operation)
    return screenplay, breakdown, plan, answers, correction_set, resource_id
