"""制片数量证据、单位和状态子集语义的门禁测试。"""

from movie_breakdown.application.production_plan_validation import (
    ProductionPlanValidationService,
)
from movie_breakdown.application.production_planning import ProductionPlanBuilder
from movie_breakdown.domain.production_planning import (
    QuantityBounds,
    QuantityFact,
    QuantityProvenance,
    QuantityRole,
    UnitCode,
)
from tests.factories import make_screenplay
from tests.production_factories import make_production_breakdown


def test_explicit_quantity_requires_numbers_in_verbatim_evidence() -> None:
    screenplay = make_screenplay()
    breakdown = make_production_breakdown(screenplay)
    plan = ProductionPlanBuilder().build(screenplay, breakdown)
    original = plan.quantity_facts[0]
    plan.quantity_facts[0] = original.model_copy(
        update={
            "bounds": QuantityBounds(minimum=999, maximum=999),
            "provenance": QuantityProvenance.EXPLICIT_TEXT,
        }
    )

    report = ProductionPlanValidationService().validate(screenplay, breakdown, plan)

    assert report.draft_valid is False
    assert any(item.code == "planning.quantity_numeric_evidence" for item in report.issues)


def test_unknown_resource_unit_blocks_catalog_but_not_draft() -> None:
    screenplay = make_screenplay()
    breakdown = make_production_breakdown(screenplay)
    plan = ProductionPlanBuilder().build(screenplay, breakdown)
    target_id = plan.quantity_facts[0].occurrence_id
    occurrence = next(item for item in plan.occurrences if item.id == target_id)
    plan.quantity_facts = [
        item.model_copy(update={"unit": UnitCode.UNKNOWN})
        if item.occurrence_id == occurrence.id
        else item
        for item in plan.quantity_facts
    ]
    plan.resource_classes = [
        item.model_copy(update={"canonical_unit": UnitCode.UNKNOWN})
        if item.id == occurrence.resource_class_id
        else item
        for item in plan.resource_classes
    ]

    report = ProductionPlanValidationService().validate(screenplay, breakdown, plan)

    assert report.draft_valid is True
    assert report.catalog_ready is False
    assert any(item.code == "planning.resource_unit" for item in report.issues)


def test_quantity_fact_unit_must_match_resource_canonical_unit() -> None:
    screenplay = make_screenplay()
    breakdown = make_production_breakdown(screenplay)
    plan = ProductionPlanBuilder().build(screenplay, breakdown)
    fact = plan.quantity_facts[0]
    occurrence = next(item for item in plan.occurrences if item.id == fact.occurrence_id)
    plan.resource_classes = [
        item.model_copy(update={"canonical_unit": UnitCode.ITEM})
        if item.id == occurrence.resource_class_id
        else item
        for item in plan.resource_classes
    ]

    report = ProductionPlanValidationService().validate(screenplay, breakdown, plan)

    assert report.draft_valid is False
    assert any(item.code == "planning.quantity_resource_unit" for item in report.issues)


def test_exclusive_subset_minimums_cannot_exceed_parent_maximum() -> None:
    screenplay = make_screenplay()
    breakdown = make_production_breakdown(screenplay)
    plan = ProductionPlanBuilder().build(screenplay, breakdown)
    occurrence_id = plan.quantity_facts[0].occurrence_id
    evidence = plan.quantity_facts[0].evidence
    parent = _fact("quantity-parent", occurrence_id, 5, evidence)
    children = [
        _fact(
            f"quantity-child-{index}",
            occurrence_id,
            3,
            evidence,
            role=QuantityRole.SUBSET,
            parent_id=parent.id,
        )
        for index in range(2)
    ]
    plan.quantity_facts = [parent, *children]
    plan.occurrences = [
        item.model_copy(update={"quantity_fact_ids": [parent.id, *(c.id for c in children)]})
        if item.id == occurrence_id
        else item
        for item in plan.occurrences
    ]

    report = ProductionPlanValidationService().validate(screenplay, breakdown, plan)

    assert any(item.code == "planning.quantity_subset_overflow" for item in report.issues)


def _fact(
    identifier: str,
    occurrence_id: str,
    value: int,
    evidence,
    *,
    role: QuantityRole = QuantityRole.TOTAL,
    parent_id: str | None = None,
) -> QuantityFact:
    """构造测试用精确总量或互斥状态子集。"""
    return QuantityFact(
        id=identifier,
        occurrence_id=occurrence_id,
        bounds=QuantityBounds(minimum=value, maximum=value),
        unit=UnitCode.VEHICLE,
        raw_unit="辆",
        role=role,
        parent_quantity_id=parent_id,
        exclusive_group="outcomes" if parent_id else None,
        provenance=QuantityProvenance.EXPLICIT_TEXT,
        evidence=evidence,
    )
