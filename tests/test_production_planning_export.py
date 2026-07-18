import csv
import io
import json

import pytest

from movie_breakdown.application.production_planning import ProductionPlanBuilder
from movie_breakdown.application.production_planning_export import (
    InvalidProductionPlanningExportError,
    ProductionPlanningExportService,
)
from movie_breakdown.domain.base import Severity
from movie_breakdown.domain.production_planning import (
    PlannedQuantity,
    PlannedQuantityPurpose,
    ProductionPlan,
    QuantityBounds,
    QuantityFact,
    QuantityProvenance,
    QuantityRole,
)
from movie_breakdown.domain.production_planning_validation import (
    ProductionPlanningIssue,
    ProductionPlanningValidationReport,
    ProductionReadinessLevel,
)
from movie_breakdown.domain.production_safety import (
    SafetyApproval,
    SafetyDecision,
    SafetyReviewerKind,
)
from movie_breakdown.infrastructure.fingerprint import content_fingerprint
from tests.factories import make_screenplay
from tests.production_factories import make_production_breakdown


def test_planning_export_contains_all_fixed_files_and_readiness() -> None:
    plan, validation = _make_export_case()

    contents = ProductionPlanningExportService().render_contents(plan, validation)

    assert set(contents) == {
        "planning.json",
        "planning-report.md",
        "shooting_units.csv",
        "resources.csv",
        "occurrences.csv",
        "quantities.csv",
        "safety.csv",
        "issues.csv",
    }
    assert all(content.endswith("\n") for content in contents.values())
    payload = json.loads(contents["planning.json"])
    assert payload["plan"]["source_fingerprint"] == plan.source_fingerprint
    assert payload["validation"]["draft_valid"] is True
    assert payload["validation"]["catalog_ready"] is False
    assert payload["validation"]["shoot_ready"] is False
    markdown = contents["planning-report.md"]
    assert "`draft_valid`（结构草稿） | 通过" in markdown
    assert "`catalog_ready`（资源目录） | 阻断" in markdown
    assert "`shoot_ready`（拍摄准备） | 阻断" in markdown
    assert "至少 1（上界未知）" in markdown
    assert "`subset` / `per_member`" in markdown


def test_planning_csv_keeps_entity_quantity_and_safety_semantics() -> None:
    plan, validation = _make_export_case()

    contents = ProductionPlanningExportService().render_contents(plan, validation)

    resources = _dict_rows(contents["resources.csv"])
    entity_row = next(row for row in resources if row["实体ID"])
    assert entity_row["实体状态"] == "unresolved"
    assert entity_row["实体归一依据"] == "deterministic"

    quantities = _dict_rows(contents["quantities.csv"])
    child = next(row for row in quantities if row["数量ID"] == "quantity-test-subset")
    assert child["上界"] == "未知"
    assert child["上界语义"] == "开放上界（至少）"
    assert child["数量角色"] == "subset"
    assert child["父数量ID"]
    planned = next(row for row in quantities if row["数量ID"] == "planned-test-backup")
    assert planned["记录类型"] == "人工计划"
    assert planned["计划用途"] == "backup"
    assert planned["复核人"] == "测试制片主任"

    safety = _dict_rows(contents["safety.csv"])
    approved = next(row for row in safety if row["审批人"] == "测试安全主管")
    hazard = plan.safety_hazards[0]
    assert approved["必需审批角色"] == "、".join(hazard.required_reviewer_roles)
    assert approved["审批角色"] == hazard.required_reviewer_roles[0]
    assert approved["审批人类别"] == "qualified_professional"
    assert approved["审批范围匹配"] == "true"

    issues = _dict_rows(contents["issues.csv"])
    assert issues[0]["代码"] == "planning.entity_unresolved"
    assert issues[0]["阻断层级"] == "catalog_ready、shoot_ready"


def test_planning_export_is_deterministic_and_rejects_stale_validation() -> None:
    plan, validation = _make_export_case()
    service = ProductionPlanningExportService()

    first = service.render_contents(plan, validation)
    second = service.render_contents(plan, validation)

    assert first == second
    stale = validation.model_copy(update={"plan_fingerprint": "stale-fingerprint"})
    with pytest.raises(
        InvalidProductionPlanningExportError,
        match="必须重新校验",
    ):
        service.render_contents(plan, stale)


def _make_export_case() -> tuple[ProductionPlan, ProductionPlanningValidationReport]:
    screenplay = make_screenplay()
    breakdown = make_production_breakdown(screenplay)
    plan = ProductionPlanBuilder().build(screenplay, breakdown)
    parent = plan.quantity_facts[0]
    child = QuantityFact(
        id="quantity-test-subset",
        occurrence_id=parent.occurrence_id,
        bounds=QuantityBounds(minimum=1, maximum=None),
        unit=parent.unit,
        raw_unit=parent.raw_unit,
        raw_expression="至少一项处于备用状态",
        role=QuantityRole.SUBSET,
        parent_quantity_id=parent.id,
        state="备用",
        provenance=QuantityProvenance.EXPLICIT_TEXT,
        evidence=parent.evidence,
    )
    plan.quantity_facts = [*plan.quantity_facts, child]
    plan.occurrences = [
        item.model_copy(update={"quantity_fact_ids": [*item.quantity_fact_ids, child.id]})
        if item.id == child.occurrence_id
        else item
        for item in plan.occurrences
    ]
    plan.planned_quantities = [
        PlannedQuantity(
            id="planned-test-backup",
            occurrence_id=child.occurrence_id,
            purpose=PlannedQuantityPurpose.BACKUP,
            bounds=QuantityBounds(minimum=2, maximum=None),
            unit=child.unit,
            reviewer="测试制片主任",
            decision_id="decision-test-backup",
            input_fingerprint=content_fingerprint(child),
            rationale="现场损耗上界需要勘景后决定。",
        )
    ]
    hazard = plan.safety_hazards[0]
    plan.safety_approvals = [
        SafetyApproval(
            hazard_id=hazard.id,
            scope_fingerprint=hazard.scope_fingerprint,
            reviewer="测试安全主管",
            reviewer_role=hazard.required_reviewer_roles[0],
            reviewer_kind=SafetyReviewerKind.QUALIFIED_PROFESSIONAL,
            decision=SafetyDecision.APPROVED_WITH_CONTROLS,
            reason="限定封闭区域并按排练方案执行。",
            required_controls=["执行前安全会议"],
        )
    ]
    issue = ProductionPlanningIssue(
        severity=Severity.WARNING,
        code="planning.entity_unresolved",
        message="跨场实体尚未人工确认。",
        reference=plan.entities[0].id,
        blocks_levels=[
            ProductionReadinessLevel.CATALOG_READY,
            ProductionReadinessLevel.SHOOT_READY,
        ],
    )
    return plan, ProductionPlanningValidationReport(
        plan_fingerprint=content_fingerprint(plan),
        draft_valid=True,
        catalog_ready=False,
        shoot_ready=False,
        scene_count=len(screenplay.scenes),
        shooting_unit_count=len(plan.shooting_units),
        resource_class_count=len(plan.resource_classes),
        entity_count=len(plan.entities),
        unresolved_entity_count=len(plan.entities),
        unknown_unit_count=0,
        hazard_count=len(plan.safety_hazards),
        qualified_approval_count=1,
        issues=[issue],
    )


def _dict_rows(content: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(content)))
