"""拍摄单元修正后的出现项双向顺序回归测试。"""

from movie_breakdown.application.production_correction_operations import (
    ProductionCorrectionOperationApplier,
)
from movie_breakdown.application.production_planning import ProductionPlanBuilder
from movie_breakdown.domain.production_correction import ReplaceShootingUnitsCorrection
from movie_breakdown.domain.production_planning import ProductionResourceKind
from movie_breakdown.infrastructure.fingerprint import content_fingerprint
from tests.factories import make_screenplay
from tests.production_factories import make_production_breakdown


def test_unit_replacement_resynchronizes_occurrence_order() -> None:
    screenplay = make_screenplay()
    breakdown = make_production_breakdown(screenplay)
    plan = ProductionPlanBuilder().build(screenplay, breakdown)
    scene_id = "scene-0003"
    unit = next(item for item in plan.shooting_units if item.scene_id == scene_id)
    classes = {item.id: item for item in plan.resource_classes}
    movable_ids = sorted(
        (
            item.id
            for item in plan.occurrences
            if item.scene_id == scene_id
            and classes[item.resource_class_id].kind != ProductionResourceKind.LOCATION
        ),
        reverse=True,
    )
    operation = ReplaceShootingUnitsCorrection(
        id="correction-reorder-scene-3",
        review_target_ids=["review-unit-scene-3"],
        expected_value_fingerprint=content_fingerprint([unit]),
        rationale="以逆序资源输入覆盖单元，验证应用器统一回填正式出现项顺序。",
        evidence=unit.evidence,
        scene_id=scene_id,
        replacement=[unit.model_copy(update={"occurrence_ids": movable_ids})],
    )

    corrected = ProductionCorrectionOperationApplier().apply(
        plan.model_copy(deep=True),
        [operation],
        breakdown.scenes,
    )

    for corrected_unit in corrected.shooting_units:
        actual_ids = [
            item.id for item in corrected.occurrences if item.shooting_unit_id == corrected_unit.id
        ]
        assert corrected_unit.occurrence_ids == actual_ids
