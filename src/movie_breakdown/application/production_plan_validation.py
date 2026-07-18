"""组合制片规划结构、资源和专业安全分级校验。"""

from __future__ import annotations

from movie_breakdown.application.production_plan_resource_validation import (
    ProductionPlanResourceValidator,
)
from movie_breakdown.application.production_plan_structure_validation import (
    ProductionPlanStructureValidator,
)
from movie_breakdown.application.production_plan_validation_support import (
    append_duplicate_issues,
    level_is_clear,
    planning_issue,
)
from movie_breakdown.domain.production_catalog import ProductionBreakdown
from movie_breakdown.domain.production_planning import (
    ProductionPlan,
    ResolutionStatus,
    UnitCode,
)
from movie_breakdown.domain.production_planning_validation import (
    ProductionPlanningIssue,
    ProductionPlanningValidationReport,
    ProductionReadinessLevel,
)
from movie_breakdown.domain.production_safety import SafetyReviewerKind
from movie_breakdown.domain.source import Screenplay
from movie_breakdown.infrastructure.fingerprint import content_fingerprint

_ALL_LEVELS = list(ProductionReadinessLevel)


class ProductionPlanValidationService:
    """重新派生安全候选并对规划执行不可绕过的分级校验。"""

    def __init__(
        self,
        structure_validator: ProductionPlanStructureValidator | None = None,
        resource_validator: ProductionPlanResourceValidator | None = None,
    ) -> None:
        """创建组合式规划校验服务。

        Args:
            structure_validator: 可替换的拍摄单元与出现项校验器。
            resource_validator: 可替换的实体、数量与安全校验器。
        """
        self._structure = structure_validator or ProductionPlanStructureValidator()
        self._resources = resource_validator or ProductionPlanResourceValidator()

    def validate(
        self,
        screenplay: Screenplay,
        breakdown: ProductionBreakdown,
        plan: ProductionPlan,
    ) -> ProductionPlanningValidationReport:
        """执行拍摄单元、实体、数量和安全审批的完整本地校验。

        Args:
            screenplay: 当前共享剧本和原文行号。
            breakdown: 规划绑定的基础制片拆解。
            plan: 待校验的当前正式制片规划。

        Returns:
            分别给出草稿、目录和开拍准备度的确定性报告。
        """
        issues: list[ProductionPlanningIssue] = []
        self._validate_bindings(screenplay, breakdown, plan, issues)
        self._validate_unique_ids(plan, issues)
        self._structure.validate(screenplay, breakdown, plan, issues)
        self._resources.validate(screenplay, breakdown, plan, issues)
        draft_valid = level_is_clear(issues, {ProductionReadinessLevel.DRAFT_VALID})
        catalog_ready = draft_valid and level_is_clear(
            issues,
            {ProductionReadinessLevel.CATALOG_READY},
        )
        shoot_ready = catalog_ready and level_is_clear(
            issues,
            {ProductionReadinessLevel.SHOOT_READY},
        )
        return ProductionPlanningValidationReport(
            plan_fingerprint=content_fingerprint(plan),
            draft_valid=draft_valid,
            catalog_ready=catalog_ready,
            shoot_ready=shoot_ready,
            scene_count=len(screenplay.scenes),
            shooting_unit_count=len(plan.shooting_units),
            resource_class_count=len(plan.resource_classes),
            entity_count=len(plan.entities),
            unresolved_entity_count=sum(
                item.status != ResolutionStatus.CONFIRMED for item in plan.entities
            ),
            unknown_unit_count=sum(
                item.canonical_unit == UnitCode.UNKNOWN for item in plan.resource_classes
            ),
            hazard_count=len(plan.safety_hazards),
            qualified_approval_count=sum(
                item.reviewer_kind == SafetyReviewerKind.QUALIFIED_PROFESSIONAL
                for item in plan.safety_approvals
            ),
            issues=issues,
        )

    @staticmethod
    def _validate_bindings(
        screenplay: Screenplay,
        breakdown: ProductionBreakdown,
        plan: ProductionPlan,
        issues: list[ProductionPlanningIssue],
    ) -> None:
        """拒绝来源或基础拆解指纹已经过期的规划。"""
        if plan.source_fingerprint != screenplay.source_fingerprint:
            issues.append(
                planning_issue(
                    "planning.source_stale",
                    "规划来源指纹已经过期。",
                    _ALL_LEVELS,
                )
            )
        if plan.base_breakdown_fingerprint != content_fingerprint(breakdown):
            issues.append(
                planning_issue(
                    "planning.breakdown_stale",
                    "规划绑定的基础拆解已经过期。",
                    _ALL_LEVELS,
                )
            )

    @staticmethod
    def _validate_unique_ids(
        plan: ProductionPlan,
        issues: list[ProductionPlanningIssue],
    ) -> None:
        """检查全部规划对象的稳定 ID 唯一性。"""
        groups = (
            ([item.id for item in plan.shooting_units], "planning.unit_duplicate", "拍摄单元"),
            (
                [item.id for item in plan.resource_classes],
                "planning.resource_duplicate",
                "资源类别",
            ),
            ([item.id for item in plan.entities], "planning.entity_duplicate", "实体"),
            (
                [item.id for item in plan.occurrences],
                "planning.occurrence_duplicate",
                "出现项",
            ),
            (
                [item.id for item in plan.quantity_facts],
                "planning.quantity_duplicate",
                "数量事实",
            ),
            (
                [item.id for item in plan.safety_hazards],
                "planning.hazard_duplicate",
                "安全候选",
            ),
        )
        for values, code, label in groups:
            append_duplicate_issues(values, code, label, issues)
