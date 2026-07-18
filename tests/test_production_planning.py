import pytest

from movie_breakdown.application.production_plan_validation import (
    ProductionPlanValidationService,
)
from movie_breakdown.application.production_planning import ProductionPlanBuilder
from movie_breakdown.application.production_quantities import QuantityFactBuilder, normalize_unit
from movie_breakdown.application.production_units import DeterministicShootingUnitBuilder
from movie_breakdown.domain.base import Confidence
from movie_breakdown.domain.production_common import (
    ProductionElementKind,
    QuantityBasis,
    QuantityEstimate,
    RequirementBasis,
)
from movie_breakdown.domain.production_planning import (
    ProductionResourceKind,
    QuantityProvenance,
    ResolutionStatus,
    ResourceOccurrence,
    UnitCode,
)
from movie_breakdown.domain.production_scene import ProductionElement
from movie_breakdown.domain.scene_analysis import Evidence
from movie_breakdown.domain.source import SourceSpan
from movie_breakdown.infrastructure.fingerprint import content_fingerprint
from tests.factories import make_screenplay
from tests.production_factories import (
    make_production_analysis,
    make_production_breakdown,
)


def test_plan_builder_preserves_base_and_separates_safety_gate() -> None:
    screenplay = make_screenplay()
    breakdown = make_production_breakdown(screenplay)

    plan = ProductionPlanBuilder().build(screenplay, breakdown)
    report = ProductionPlanValidationService().validate(screenplay, breakdown, plan)

    assert plan.base_breakdown_fingerprint == content_fingerprint(breakdown)
    assert report.draft_valid is True
    assert report.catalog_ready is False
    assert report.shoot_ready is False
    assert report.shooting_unit_count == len(screenplay.scenes)
    assert report.hazard_count >= 1
    assert all(item.source_requirement_id for item in plan.occurrences)


def test_confirming_candidate_entities_can_reach_catalog_ready() -> None:
    screenplay = make_screenplay()
    breakdown = make_production_breakdown(screenplay)
    plan = ProductionPlanBuilder().build(screenplay, breakdown)
    confirmed_ids = {item.id for item in plan.entities}
    plan.entities = [
        item.model_copy(update={"status": ResolutionStatus.CONFIRMED}) for item in plan.entities
    ]
    plan.occurrences = [
        item.model_copy(
            update={
                "resolution_status": (
                    ResolutionStatus.CONFIRMED
                    if item.entity_id in confirmed_ids
                    else item.resolution_status
                )
            }
        )
        for item in plan.occurrences
    ]

    report = ProductionPlanValidationService().validate(screenplay, breakdown, plan)

    assert report.catalog_ready is True, [
        (item.code, item.reference, item.blocks_levels) for item in report.issues
    ]
    assert report.shoot_ready is False


def test_removing_deterministic_hazard_blocks_draft() -> None:
    screenplay = make_screenplay()
    breakdown = make_production_breakdown(screenplay)
    plan = ProductionPlanBuilder().build(screenplay, breakdown)
    assert plan.safety_hazards
    plan.safety_hazards = []

    report = ProductionPlanValidationService().validate(screenplay, breakdown, plan)

    assert report.draft_valid is False
    assert any(item.code == "planning.hazard_missing" for item in report.issues)


def test_deterministic_unit_builder_splits_explicit_time_marker() -> None:
    screenplay = make_screenplay()
    scene = screenplay.scenes[0]
    scene.text = "车站 日 外\n小王进站。\n夜里，小王等待。"
    scene.source_span = SourceSpan(line_start=1, line_end=3)
    analysis = make_production_analysis(scene)

    units = DeterministicShootingUnitBuilder().build(scene, analysis)

    assert len(units) == 2
    assert units[0].source_span == SourceSpan(line_start=1, line_end=2)
    assert units[1].source_span == SourceSpan(line_start=3, line_end=3)
    assert units[1].time_of_day.value == "night"


def test_model_estimate_without_numeric_evidence_is_not_a_fact() -> None:
    scene = make_screenplay().scenes[0]
    evidence = Evidence(
        scene_id=scene.id,
        source_span=scene.source_span,
        excerpt=scene.text,
        confidence=Confidence.HIGH,
    )
    requirement = ProductionElement(
        id="element-crowd-copy",
        kind=ProductionElementKind.VFX,
        name="数字扩充人群",
        description="模型提出的画面扩充方案。",
        quantity=QuantityEstimate(
            minimum=800,
            maximum=2000,
            unit="人",
            basis=QuantityBasis.ESTIMATED,
        ),
        basis=RequirementBasis.EXPLICIT,
        confidence=Confidence.HIGH,
        evidence=[evidence],
    )
    occurrence = ResourceOccurrence(
        id="occurrence-estimate",
        scene_id=scene.id,
        shooting_unit_id=f"{scene.id}/unit-001",
        source_requirement_id=f"{scene.id}/{requirement.id}",
        resource_class_id="resource-vfx",
        resolution_status=ResolutionStatus.CONFIRMED,
        evidence=[evidence],
    )

    fact = QuantityFactBuilder().build(occurrence, requirement)

    assert fact.provenance == QuantityProvenance.UNKNOWN
    assert fact.bounds.minimum is None
    assert fact.raw_unit == "人"
    assert "未作为可执行" in (fact.raw_expression or "")


def test_unit_normalization_uses_resource_context() -> None:
    assert normalize_unit("persons", element_kind=None, name="美军") == UnitCode.PERSON
    assert (
        normalize_unit("匹", element_kind=ProductionElementKind.ANIMAL, name="老罗")
        == UnitCode.ANIMAL
    )
    assert (
        normalize_unit("辆", element_kind=ProductionElementKind.VEHICLE, name="卡车")
        == UnitCode.VEHICLE
    )
    assert (
        normalize_unit("枚", element_kind=ProductionElementKind.HAND_PROP, name="手雷")
        == UnitCode.WEAPON
    )


@pytest.mark.parametrize(
    ("kind", "raw_unit", "name", "expected"),
    [
        (ProductionElementKind.ANIMAL, "群", "军马", UnitCode.ANIMAL),
        (ProductionElementKind.VEHICLE, "群", "卡车", UnitCode.VEHICLE),
        (ProductionElementKind.COSTUME, "群", "军服", UnitCode.COSTUME),
        (ProductionElementKind.HAIR_MAKEUP, "人", "伤妆", UnitCode.SET),
        (ProductionElementKind.SET_DRESSING, "辆", "路障", UnitCode.SET),
        (ProductionElementKind.STUNT_ACTION, "人", "冲锋", UnitCode.EVENT),
        (ProductionElementKind.PRACTICAL_EFFECT, "枚", "爆炸", UnitCode.EVENT),
        (ProductionElementKind.SOUND_MUSIC, "首", "军号", UnitCode.EVENT),
        (ProductionElementKind.VFX, "镜头", "数字烟尘", UnitCode.SHOT),
        (ProductionElementKind.VFX, "人", "数字扩充", UnitCode.EVENT),
        (ProductionElementKind.SPECIAL_EQUIPMENT, "辆", "摄影车", UnitCode.DEVICE),
        (ProductionElementKind.HAND_PROP, "群", "手雷", UnitCode.WEAPON),
        (ProductionElementKind.HAND_PROP, "把", "雨伞", UnitCode.ITEM),
        (ProductionElementKind.OTHER, "枚", "手雷图案", UnitCode.ITEM),
    ],
)
def test_element_kind_has_priority_over_free_form_unit(
    kind: ProductionElementKind,
    raw_unit: str,
    name: str,
    expected: UnitCode,
) -> None:
    """验证元素类别先于模型自由单位决定标准语义。"""
    assert normalize_unit(raw_unit, element_kind=kind, name=name) == expected


def test_plan_has_location_occurrence_for_every_shooting_unit() -> None:
    screenplay = make_screenplay()
    breakdown = make_production_breakdown(screenplay)
    plan = ProductionPlanBuilder().build(screenplay, breakdown)
    classes = {item.id: item for item in plan.resource_classes}

    location_occurrences = [
        item
        for item in plan.occurrences
        if classes[item.resource_class_id].kind == ProductionResourceKind.LOCATION
    ]

    assert len(location_occurrences) == len(plan.shooting_units)
    assert {item.shooting_unit_id for item in location_occurrences} == {
        item.id for item in plan.shooting_units
    }


def test_resource_classes_split_same_name_with_incompatible_units() -> None:
    screenplay = make_screenplay()
    breakdown = make_production_breakdown(screenplay)
    for analysis, raw_unit in zip(breakdown.scenes[:2], ["组", "支"], strict=True):
        evidence = Evidence(
            scene_id=analysis.scene_id,
            source_span=screenplay.scenes[int(analysis.scene_id[-1]) - 1].source_span,
            excerpt=screenplay.scenes[int(analysis.scene_id[-1]) - 1].text,
            confidence=Confidence.HIGH,
        )
        analysis.elements.append(
            ProductionElement(
                id="element-cigarette",
                kind=ProductionElementKind.HAND_PROP,
                name="香烟",
                description="同名资源采用不同原始计量口径。",
                quantity=QuantityEstimate(
                    minimum=None,
                    maximum=None,
                    unit=raw_unit,
                    basis=QuantityBasis.UNKNOWN,
                ),
                basis=RequirementBasis.EXPLICIT,
                confidence=Confidence.HIGH,
                evidence=[evidence],
            )
        )

    plan = ProductionPlanBuilder().build(screenplay, breakdown)
    resources = [item for item in plan.resource_classes if item.canonical_name == "香烟"]
    classes = {item.id: item for item in plan.resource_classes}
    facts = {item.occurrence_id: item for item in plan.quantity_facts}
    occurrences = [
        item
        for item in plan.occurrences
        if item.resource_class_id in {resource.id for resource in resources}
    ]

    assert len(resources) == 2
    assert {item.canonical_unit for item in resources} == {UnitCode.SET, UnitCode.ITEM}
    assert all(
        facts[item.id].unit == classes[item.resource_class_id].canonical_unit
        for item in occurrences
    )
