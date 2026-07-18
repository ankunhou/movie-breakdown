import pytest
from pydantic import ValidationError

from movie_breakdown.domain.production_common import ProductionElementKind
from movie_breakdown.domain.production_planning import (
    NormalizationBasis,
    ProductionResourceClass,
    ProductionResourceKind,
    QuantityBounds,
    QuantityFact,
    QuantityProvenance,
    QuantityRole,
    UnitCode,
)
from movie_breakdown.domain.production_safety import (
    SafetyApproval,
    SafetyDecision,
    SafetyReviewerKind,
)
from tests.factories import make_screenplay
from tests.production_factories import scene_evidence


def test_quantity_bounds_rejects_only_maximum_and_reverse_order() -> None:
    with pytest.raises(ValidationError, match="不能只提供上界"):
        QuantityBounds(maximum=3)
    with pytest.raises(ValidationError, match="上界不能小于下界"):
        QuantityBounds(minimum=4, maximum=3)


def test_quantity_subset_requires_parent_and_unknown_has_no_bounds() -> None:
    scene = make_screenplay().scenes[0]
    common = {
        "id": "quantity-001",
        "occurrence_id": "occurrence-001",
        "bounds": QuantityBounds(minimum=3, maximum=3),
        "unit": UnitCode.PERSON,
        "raw_unit": "人",
        "role": QuantityRole.SUBSET,
        "provenance": QuantityProvenance.EXPLICIT_TEXT,
        "evidence": [scene_evidence(scene)],
    }

    with pytest.raises(ValidationError, match="parent_quantity_id"):
        QuantityFact(**common)
    with pytest.raises(ValidationError, match="未知来源数量"):
        QuantityFact(
            **{
                **common,
                "role": QuantityRole.TOTAL,
                "provenance": QuantityProvenance.UNKNOWN,
            }
        )


def test_derived_quantity_requires_source_ids() -> None:
    scene = make_screenplay().scenes[0]

    with pytest.raises(ValidationError, match="derived_from_ids"):
        QuantityFact(
            id="quantity-derived",
            occurrence_id="occurrence-001",
            bounds=QuantityBounds(minimum=6, maximum=6),
            unit=UnitCode.PERSON,
            raw_unit="人",
            role=QuantityRole.TOTAL,
            provenance=QuantityProvenance.DETERMINISTIC_DERIVED,
            evidence=[scene_evidence(scene)],
        )


def test_only_element_resource_accepts_element_kind() -> None:
    with pytest.raises(ValidationError, match="只有 element"):
        ProductionResourceClass(
            id="resource-cast",
            kind=ProductionResourceKind.CAST,
            element_kind=ProductionElementKind.COSTUME,
            canonical_name="小王",
            canonical_unit=UnitCode.PERSON,
            identity_scope="continuity",
            basis=NormalizationBasis.DETERMINISTIC,
        )


def test_unqualified_reviewer_cannot_approve_hazard() -> None:
    with pytest.raises(ValidationError, match="合格专业人员"):
        SafetyApproval(
            hazard_id="hazard-001",
            scope_fingerprint="scope",
            reviewer="AI 模拟动作指导",
            reviewer_role="动作指导",
            reviewer_kind=SafetyReviewerKind.AI_SIMULATED,
            decision=SafetyDecision.APPROVED,
            reason="模拟抽检认为可以拍摄。",
        )


def test_ai_reviewer_cannot_mark_hazard_not_applicable() -> None:
    """验证 AI 不能用“不适用”绕过固定高危范围。"""
    with pytest.raises(ValidationError, match="合格专业人员"):
        SafetyApproval(
            hazard_id="hazard-001",
            scope_fingerprint="scope",
            reviewer="AI 模拟动作指导",
            reviewer_role="动作指导",
            reviewer_kind=SafetyReviewerKind.AI_SIMULATED,
            decision=SafetyDecision.NOT_APPLICABLE,
            reason="模拟判断该范围不适用。",
        )


def test_simulated_reviewer_can_require_professional_review() -> None:
    approval = SafetyApproval(
        hazard_id="hazard-001",
        scope_fingerprint="scope",
        reviewer="AI 模拟动作指导",
        reviewer_role="动作指导",
        reviewer_kind=SafetyReviewerKind.AI_SIMULATED,
        decision=SafetyDecision.REQUIRES_PROFESSIONAL_REVIEW,
        reason="需要真实专业团队制定方案并批准。",
    )

    assert approval.decision == SafetyDecision.REQUIRES_PROFESSIONAL_REVIEW
