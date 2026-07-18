"""制片规划实体、数量和高危专业审批校验。"""

from __future__ import annotations

from collections import defaultdict

from movie_breakdown.application.production_plan_validation_support import planning_issue
from movie_breakdown.application.production_quantity_validation import (
    ProductionQuantityValidator,
)
from movie_breakdown.application.production_safety import ProductionSafetyDetector
from movie_breakdown.application.production_safety_defaults import find_unsafe_defaults
from movie_breakdown.domain.production_catalog import ProductionBreakdown
from movie_breakdown.domain.production_common import ComplexityDimension
from movie_breakdown.domain.production_planning import (
    IdentityScope,
    ProductionPlan,
    ResolutionStatus,
)
from movie_breakdown.domain.production_planning_validation import (
    ProductionPlanningIssue,
    ProductionReadinessLevel,
)
from movie_breakdown.domain.production_safety import (
    CLOSED_SAFETY_DECISIONS,
    SafetyReviewerKind,
)
from movie_breakdown.domain.source import Screenplay
from movie_breakdown.infrastructure.fingerprint import content_fingerprint

_ALL_LEVELS = list(ProductionReadinessLevel)
_CATALOG_LEVELS = [
    ProductionReadinessLevel.CATALOG_READY,
    ProductionReadinessLevel.SHOOT_READY,
]
_SHOOT_LEVEL = [ProductionReadinessLevel.SHOOT_READY]


class ProductionPlanResourceValidator:
    """校验实体归一、数量语义及不可绕过的专业安全批准。"""

    def __init__(self, quantity_validator: ProductionQuantityValidator | None = None) -> None:
        """创建可替换数量校验器的资源门禁。

        Args:
            quantity_validator: 专门校验数量、单位与父子关系的服务。
        """
        self._quantities = quantity_validator or ProductionQuantityValidator()

    def validate(
        self,
        screenplay: Screenplay,
        breakdown: ProductionBreakdown,
        plan: ProductionPlan,
        issues: list[ProductionPlanningIssue],
    ) -> None:
        """把资源、数量与安全问题追加到结果列表。

        Args:
            screenplay: 用于逐字定位数量证据的当前共享剧本。
            breakdown: 提供逐场风险来源的基础拆解。
            plan: 待校验的当前规划。
            issues: 原地追加问题的结果列表。
        """
        self._validate_entities(plan, issues)
        self._quantities.validate(screenplay, plan, issues)
        self._validate_safety(breakdown, plan, issues)

    @staticmethod
    def _validate_entities(plan: ProductionPlan, issues: list[ProductionPlanningIssue]) -> None:
        """检查连续性实体覆盖、类别兼容和确认状态。"""
        classes = {item.id: item for item in plan.resource_classes}
        occurrences = {item.id: item for item in plan.occurrences}
        entities = {item.id: item for item in plan.entities}
        for entity in plan.entities:
            if not set(entity.resource_class_ids) <= set(classes):
                issues.append(
                    planning_issue(
                        "planning.entity_resource_ref",
                        "实体引用未知资源类别。",
                        _ALL_LEVELS,
                        entity.id,
                    )
                )
            for occurrence_id in entity.occurrence_ids:
                occurrence = occurrences.get(occurrence_id)
                if occurrence is None or occurrence.entity_id != entity.id:
                    issues.append(
                        planning_issue(
                            "planning.entity_occurrence_ref",
                            "实体与出现项双向引用不一致。",
                            _ALL_LEVELS,
                            entity.id,
                        )
                    )
                elif occurrence.resolution_status != entity.status:
                    issues.append(
                        planning_issue(
                            "planning.entity_status",
                            "实体与出现项的确认状态不一致。",
                            _ALL_LEVELS,
                            occurrence.id,
                        )
                    )
            if entity.status != ResolutionStatus.CONFIRMED:
                issues.append(
                    planning_issue(
                        "planning.entity_unresolved",
                        "跨场实体尚未人工确认。",
                        _CATALOG_LEVELS,
                        entity.id,
                    )
                )
        for occurrence in plan.occurrences:
            resource = classes.get(occurrence.resource_class_id)
            if (
                resource
                and resource.identity_scope != IdentityScope.FUNGIBLE
                and occurrence.entity_id not in entities
            ):
                issues.append(
                    planning_issue(
                        "planning.entity_missing",
                        "连续性出现项缺少实体。",
                        _ALL_LEVELS,
                        occurrence.id,
                    )
                )

    @staticmethod
    def _validate_safety(
        breakdown: ProductionBreakdown,
        plan: ProductionPlan,
        issues: list[ProductionPlanningIssue],
    ) -> None:
        """重建风险候选并要求全部角色给出有效专业批准。"""
        expected = ProductionSafetyDetector().detect(
            breakdown.scenes,
            plan.occurrences,
            plan.shooting_units,
        )
        actual = {item.id: item for item in plan.safety_hazards}
        for hazard in expected:
            current = actual.get(hazard.id)
            if current is None or current.scope_fingerprint != hazard.scope_fingerprint:
                issues.append(
                    planning_issue(
                        "planning.hazard_missing",
                        "确定性高危候选缺失或范围被改写。",
                        _ALL_LEVELS,
                        hazard.id,
                    )
                )
        approvals: dict[tuple[str, str], list] = defaultdict(list)
        for approval in plan.safety_approvals:
            approvals[(approval.hazard_id, approval.reviewer_role)].append(approval)
            hazard = actual.get(approval.hazard_id)
            if hazard is None or approval.scope_fingerprint != hazard.scope_fingerprint:
                issues.append(
                    planning_issue(
                        "planning.safety_approval_stale",
                        "安全复核范围指纹已经过期。",
                        _SHOOT_LEVEL,
                        approval.hazard_id,
                    )
                )
        for hazard in plan.safety_hazards:
            for role in hazard.required_reviewer_roles:
                valid = [
                    item
                    for item in approvals[(hazard.id, role)]
                    if item.reviewer_kind == SafetyReviewerKind.QUALIFIED_PROFESSIONAL
                    and item.decision in CLOSED_SAFETY_DECISIONS
                    and item.scope_fingerprint == hazard.scope_fingerprint
                ]
                if len(valid) != 1:
                    issues.append(
                        planning_issue(
                            "planning.safety_role",
                            f"缺少“{role}”的唯一有效专业批准。",
                            _SHOOT_LEVEL,
                            hazard.id,
                        )
                    )
        hazardous_scenes = {item.scene_id for item in plan.safety_hazards}
        for analysis in breakdown.scenes:
            dimensions = {factor.dimension for factor in analysis.complexity.factors}
            if (
                analysis.scene_id in hazardous_scenes
                and ComplexityDimension.ACTION_SAFETY not in dimensions
            ):
                issues.append(
                    planning_issue(
                        "planning.action_safety_missing",
                        "高危场景缺少旧版 action_safety 复杂度标签；风险仍由专业审批门禁覆盖。",
                        [],
                        analysis.scene_id,
                    )
                )
        analyses = {item.scene_id: item for item in breakdown.scenes}
        decisions: dict[str, list] = defaultdict(list)
        for decision in plan.safety_method_decisions:
            decisions[decision.scene_id].append(decision)
            analysis = analyses.get(decision.scene_id)
            if analysis is None or decision.analysis_fingerprint != content_fingerprint(analysis):
                issues.append(
                    planning_issue(
                        "planning.safety_method_stale",
                        "安全方法决策引用未知场景或已经过期。",
                        _CATALOG_LEVELS,
                        decision.id,
                    )
                )
        for scene_id, message in find_unsafe_defaults(breakdown.scenes):
            keyword = "实弹" if "实弹" in message else "活体"
            valid = [
                item
                for item in decisions[scene_id]
                if keyword in item.prohibited_method
                and item.analysis_fingerprint == content_fingerprint(analyses[scene_id])
            ]
            if len(valid) != 1:
                issues.append(
                    planning_issue(
                        "planning.unsafe_default",
                        message,
                        _CATALOG_LEVELS,
                        scene_id,
                    )
                )
