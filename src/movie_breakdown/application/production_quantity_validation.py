"""校验制片数量事实、标准单位、父子关系和人工计划边界。"""

from __future__ import annotations

from collections import defaultdict

from movie_breakdown.application.production_plan_validation_support import (
    evidence_is_located,
    planning_issue,
)
from movie_breakdown.application.production_quantities import (
    quantity_values_are_supported,
)
from movie_breakdown.domain.production_planning import (
    ProductionPlan,
    QuantityProvenance,
    QuantityRole,
    UnitCode,
)
from movie_breakdown.domain.production_planning_validation import (
    ProductionPlanningIssue,
    ProductionReadinessLevel,
)
from movie_breakdown.domain.source import Screenplay
from movie_breakdown.infrastructure.fingerprint import content_fingerprint

_ALL_LEVELS = list(ProductionReadinessLevel)
_CATALOG_LEVELS = [
    ProductionReadinessLevel.CATALOG_READY,
    ProductionReadinessLevel.SHOOT_READY,
]


class ProductionQuantityValidator:
    """确定性验证数量引用、单位、证据和不可相加的子集语义。"""

    def validate(
        self,
        screenplay: Screenplay,
        plan: ProductionPlan,
        issues: list[ProductionPlanningIssue],
    ) -> None:
        """把全部数量与单位问题追加到规划报告。

        Args:
            screenplay: 用于逐字定位数量证据的当前剧本。
            plan: 待校验的完整制片规划。
            issues: 原地追加问题的规划问题列表。
        """
        self._validate_resource_units(plan, issues)
        self._validate_facts(screenplay, plan, issues)
        self._validate_subset_groups(plan, issues)
        self._validate_planned_quantities(plan, issues)

    @staticmethod
    def _validate_resource_units(
        plan: ProductionPlan,
        issues: list[ProductionPlanningIssue],
    ) -> None:
        """阻断仍携带未知标准单位的资源类别。"""
        for resource in plan.resource_classes:
            if resource.canonical_unit == UnitCode.UNKNOWN:
                issues.append(
                    planning_issue(
                        "planning.resource_unit",
                        "资源类别的标准单位尚未确认。",
                        _CATALOG_LEVELS,
                        resource.id,
                    )
                )

    def _validate_facts(
        self,
        screenplay: Screenplay,
        plan: ProductionPlan,
        issues: list[ProductionPlanningIssue],
    ) -> None:
        """检查事实双向引用、逐字证据、父项和派生来源。"""
        scenes = {item.id: item for item in screenplay.scenes}
        facts = {item.id: item for item in plan.quantity_facts}
        occurrences = {item.id: item for item in plan.occurrences}
        classes = {item.id: item for item in plan.resource_classes}
        declared: dict[str, set[str]] = defaultdict(set)
        for fact in plan.quantity_facts:
            declared[fact.occurrence_id].add(fact.id)
            occurrence = occurrences.get(fact.occurrence_id)
            if occurrence is None or fact.id not in occurrence.quantity_fact_ids:
                self._append(
                    "planning.quantity_ref", "数量事实与出现项双向引用不一致。", fact.id, issues
                )
                continue
            resource = classes.get(occurrence.resource_class_id)
            if resource is None or fact.unit != resource.canonical_unit:
                self._append(
                    "planning.quantity_resource_unit",
                    "数量事实单位与对应资源类别的标准单位不一致。",
                    fact.id,
                    issues,
                )
            if fact.unit == UnitCode.UNKNOWN:
                issues.append(
                    planning_issue(
                        "planning.quantity_unit",
                        "数量单位尚未标准化。",
                        _CATALOG_LEVELS,
                        fact.id,
                    )
                )
            scene = scenes.get(occurrence.scene_id)
            if scene is None or any(
                not evidence_is_located(scene, evidence) for evidence in fact.evidence
            ):
                self._append(
                    "planning.quantity_evidence",
                    "数量事实证据无法在所属场景逐字定位。",
                    fact.id,
                    issues,
                )
            if fact.provenance == QuantityProvenance.EXPLICIT_TEXT and not (
                quantity_values_are_supported(
                    fact.bounds.minimum,
                    fact.bounds.maximum,
                    fact.evidence,
                )
            ):
                self._append(
                    "planning.quantity_numeric_evidence",
                    "显式数量上下界没有得到逐字证据中的数字支持。",
                    fact.id,
                    issues,
                )
            self._validate_relations(fact, facts, occurrences, issues)
        for occurrence in plan.occurrences:
            if set(occurrence.quantity_fact_ids) != declared[occurrence.id]:
                self._append(
                    "planning.quantity_occurrence_ref",
                    "出现项的数量事实列表与实际事实不一致。",
                    occurrence.id,
                    issues,
                )

    def _validate_relations(self, fact, facts, occurrences, issues) -> None:
        """检查一个数量事实的父项、来源项、单位和场景兼容性。"""
        related_ids = [
            *([fact.parent_quantity_id] if fact.parent_quantity_id else []),
            *fact.derived_from_ids,
        ]
        if fact.id in related_ids or _contains_cycle(fact.id, facts):
            self._append(
                "planning.quantity_cycle",
                "数量父子或派生关系存在循环。",
                fact.id,
                issues,
            )
        for related_id in related_ids:
            related = facts.get(related_id)
            if related is None:
                self._append(
                    "planning.quantity_relation_ref",
                    "数量事实引用未知父项或派生来源。",
                    fact.id,
                    issues,
                )
                continue
            current_occurrence = occurrences.get(fact.occurrence_id)
            related_occurrence = occurrences.get(related.occurrence_id)
            incompatible = (
                current_occurrence is None
                or related_occurrence is None
                or current_occurrence.scene_id != related_occurrence.scene_id
                or fact.unit != related.unit
            )
            if incompatible:
                self._append(
                    "planning.quantity_relation_scope",
                    "数量父子或派生关系必须位于同一场景并使用同一标准单位。",
                    fact.id,
                    issues,
                )

    @staticmethod
    def _validate_subset_groups(
        plan: ProductionPlan,
        issues: list[ProductionPlanningIssue],
    ) -> None:
        """阻断互斥子集已知下界之和超过父项上界。"""
        facts = {item.id: item for item in plan.quantity_facts}
        groups: dict[tuple[str, str], list] = defaultdict(list)
        for fact in plan.quantity_facts:
            if (
                fact.role == QuantityRole.SUBSET
                and fact.parent_quantity_id
                and fact.exclusive_group
            ):
                groups[(fact.parent_quantity_id, fact.exclusive_group)].append(fact)
        for (parent_id, _), children in groups.items():
            parent = facts.get(parent_id)
            if parent is None or parent.bounds.maximum is None:
                continue
            minimum_sum = sum(item.bounds.minimum or 0 for item in children)
            if minimum_sum > parent.bounds.maximum:
                issues.append(
                    planning_issue(
                        "planning.quantity_subset_overflow",
                        "互斥状态子集的最小数量之和超过父项上界。",
                        _ALL_LEVELS,
                        parent_id,
                    )
                )

    @staticmethod
    def _validate_planned_quantities(
        plan: ProductionPlan,
        issues: list[ProductionPlanningIssue],
    ) -> None:
        """检查人工计划只引用现有出现项且单位与资源兼容。"""
        occurrences = {item.id: item for item in plan.occurrences}
        classes = {item.id: item for item in plan.resource_classes}
        for item in plan.planned_quantities:
            occurrence = occurrences.get(item.occurrence_id)
            if occurrence is None:
                ProductionQuantityValidator._append(
                    "planning.planned_quantity_ref",
                    "人工计划数量引用未知出现项。",
                    item.id,
                    issues,
                )
            elif item.unit != classes[occurrence.resource_class_id].canonical_unit:
                ProductionQuantityValidator._append(
                    "planning.planned_quantity_unit",
                    "人工计划数量单位与资源类别不兼容。",
                    item.id,
                    issues,
                )
            elif item.input_fingerprint != planned_quantity_scope_fingerprint(
                plan,
                item.occurrence_id,
            ):
                issues.append(
                    planning_issue(
                        "planning.planned_quantity_stale",
                        "人工计划数量所绑定的资源或剧本数量事实已经变化。",
                        _CATALOG_LEVELS,
                        item.id,
                    )
                )

    @staticmethod
    def _append(
        code: str,
        message: str,
        reference: str,
        issues: list[ProductionPlanningIssue],
    ) -> None:
        """追加阻断全部准备度层级的数量问题。"""
        issues.append(planning_issue(code, message, _ALL_LEVELS, reference))


def _contains_cycle(start_id: str, facts: dict) -> bool:
    """判断一个数量事实沿父项和派生来源是否会回到自身。"""
    pending = [start_id]
    visited: set[str] = set()
    while pending:
        current_id = pending.pop()
        if current_id in visited:
            continue
        visited.add(current_id)
        current = facts.get(current_id)
        if current is None:
            continue
        related = [
            *([current.parent_quantity_id] if current.parent_quantity_id else []),
            *current.derived_from_ids,
        ]
        if start_id in related and current_id != start_id:
            return True
        pending.extend(value for value in related if value not in visited)
    return False


def planned_quantity_scope_fingerprint(plan: ProductionPlan, occurrence_id: str) -> str:
    """计算人工计划数量必须绑定的资源与剧本事实范围指纹。

    Args:
        plan: 包含出现项和数量事实的当前制片规划。
        occurrence_id: 人工计划数量所服务的资源出现项 ID。

    Returns:
        出现项及其有序剧本数量事实的稳定内容指纹。

    Raises:
        ValueError: 出现项不存在。
    """
    occurrence = next((item for item in plan.occurrences if item.id == occurrence_id), None)
    if occurrence is None:
        raise ValueError(f"人工计划数量引用未知出现项：{occurrence_id}")
    facts = {item.id: item for item in plan.quantity_facts if item.occurrence_id == occurrence_id}
    ordered = [facts[item_id] for item_id in occurrence.quantity_fact_ids if item_id in facts]
    return content_fingerprint({"occurrence": occurrence, "quantity_facts": ordered})
