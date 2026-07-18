"""制片修正与封版在大规模问题集下的诊断回归测试。"""

from movie_breakdown.application.production_corrections import _blocking_issue_summary
from movie_breakdown.application.production_release import ProductionReleaseService
from movie_breakdown.domain.base import Severity
from movie_breakdown.domain.production_planning_validation import (
    ProductionPlanningIssue,
    ProductionPlanningValidationReport,
    ProductionReadinessLevel,
)
from movie_breakdown.domain.production_release import ProductionReleaseProfile


def _issue(
    code: str,
    reference: str,
    blocks: list[ProductionReadinessLevel],
) -> ProductionPlanningIssue:
    """构造一条指定阻断层级的规划诊断。"""
    return ProductionPlanningIssue(
        severity=Severity.ERROR,
        code=code,
        message="测试诊断。",
        reference=reference,
        blocks_levels=blocks,
    )


def test_correction_error_only_summarizes_draft_blockers() -> None:
    issues = [
        *[
            _issue(
                "planning.unit_occurrences",
                f"unit-{index}",
                [ProductionReadinessLevel.DRAFT_VALID],
            )
            for index in range(3)
        ],
        _issue(
            "planning.safety_role",
            "hazard-1/armorer",
            [ProductionReadinessLevel.SHOOT_READY],
        ),
    ]

    result = _blocking_issue_summary(issues, ProductionReadinessLevel.DRAFT_VALID)

    assert result == ["planning.unit_occurrences×3"]


def test_professional_gate_preserves_more_than_five_hundred_blocking_scopes() -> None:
    issues = [
        _issue(
            "planning.safety_role",
            f"hazard-{index:04d}/safety_lead",
            [ProductionReadinessLevel.SHOOT_READY],
        )
        for index in range(600)
    ]
    validation = ProductionPlanningValidationReport(
        plan_fingerprint="plan",
        draft_valid=True,
        catalog_ready=True,
        shoot_ready=False,
        scene_count=1,
        shooting_unit_count=1,
        resource_class_count=1,
        entity_count=0,
        unresolved_entity_count=0,
        unknown_unit_count=0,
        hazard_count=600,
        qualified_approval_count=0,
        issues=issues,
    )

    professional = ProductionReleaseService._planning(
        validation,
        ProductionReleaseProfile.PROFESSIONAL,
    )
    evaluation = ProductionReleaseService._planning(
        validation,
        ProductionReleaseProfile.EVALUATION,
    )

    assert professional.passed is False
    assert len(professional.references) == 600
    assert evaluation.passed is True
    assert evaluation.references == []
