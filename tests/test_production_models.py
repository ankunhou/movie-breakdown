import pytest
from pydantic import ValidationError

from movie_breakdown.domain.base import Confidence
from movie_breakdown.domain.production_common import (
    CastAppearanceKind,
    ComplexityLevel,
    ProductionElementKind,
    QuantityBasis,
    QuantityEstimate,
    RequirementBasis,
)
from movie_breakdown.domain.production_scene import (
    CastRequirement,
    ProductionElement,
    SceneProductionComplexity,
)
from tests.factories import make_screenplay
from tests.production_factories import scene_evidence


def test_quantity_estimate_enforces_basis_specific_bounds() -> None:
    exact = QuantityEstimate(minimum=2, maximum=2, unit="把", basis=QuantityBasis.EXACT)

    assert exact.minimum == exact.maximum == 2
    with pytest.raises(ValidationError, match="相同的上下界"):
        QuantityEstimate(minimum=2, maximum=3, unit="把", basis=QuantityBasis.EXACT)
    with pytest.raises(ValidationError, match="未知数量"):
        QuantityEstimate(minimum=1, maximum=None, unit="人", basis=QuantityBasis.UNKNOWN)


def test_inferred_requirement_requires_rationale() -> None:
    scene = make_screenplay().scenes[0]

    with pytest.raises(ValidationError, match="必须填写 rationale"):
        CastRequirement(
            id="cast-xiaowang",
            character_name="小王",
            appearance_kind=CastAppearanceKind.ON_SCREEN,
            performance_notes=[],
            basis=RequirementBasis.INFERRED,
            confidence=Confidence.MEDIUM,
            evidence=[scene_evidence(scene)],
        )


def test_complexity_score_and_level_cannot_disagree() -> None:
    with pytest.raises(ValidationError, match="必须对应 high"):
        SceneProductionComplexity(
            score=4,
            level=ComplexityLevel.MEDIUM,
            factors=[],
            scheduling_notes=[],
        )


def test_other_element_keeps_open_subtype() -> None:
    scene = make_screenplay().scenes[0]

    with pytest.raises(ValidationError, match="必须填写 subtype"):
        ProductionElement(
            id="element-other",
            kind=ProductionElementKind.OTHER,
            name="特殊物件",
            description="无法归入稳定大类的物件。",
            quantity=QuantityEstimate(
                minimum=None,
                maximum=None,
                unit="件",
                basis=QuantityBasis.UNKNOWN,
            ),
            basis=RequirementBasis.EXPLICIT,
            confidence=Confidence.HIGH,
            evidence=[scene_evidence(scene)],
        )
