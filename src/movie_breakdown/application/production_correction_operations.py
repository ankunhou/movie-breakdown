"""在制片规划深拷贝上应用已经预检的结构化修正操作。"""

from __future__ import annotations

from collections import defaultdict

from movie_breakdown.application.production_aggregation_support import (
    normalize_catalog_key,
    stable_catalog_id,
)
from movie_breakdown.application.production_safety import ProductionSafetyDetector
from movie_breakdown.domain.production_correction import (
    ProductionCorrectionOperation,
    ReplaceEntityRegistryCorrection,
    ReplacePlannedQuantitiesCorrection,
    ReplaceResourceClassesCorrection,
    ReplaceSafetyApprovalsCorrection,
    ReplaceSafetyMethodsCorrection,
    ReplaceSceneQuantitiesCorrection,
    ReplaceShootingUnitsCorrection,
)
from movie_breakdown.domain.production_planning import (
    IdentityScope,
    NormalizationBasis,
    ProductionPlan,
    ProductionResourceClass,
    ProductionResourceKind,
    ResolutionStatus,
    ResourceOccurrence,
    UnitCode,
)
from movie_breakdown.domain.production_scene import SceneProductionAnalysis


class ProductionCorrectionOperationError(ValueError):
    """结构化制片修正无法保持规划引用一致性。"""


class ProductionCorrectionOperationApplier:
    """负责各种可辨识操作的确定性替换和引用回填。"""

    def apply(
        self,
        plan: ProductionPlan,
        operations: list[ProductionCorrectionOperation],
        analyses: list[SceneProductionAnalysis],
    ) -> ProductionPlan:
        """依次应用无冲突操作并重建模型无权关闭的安全候选。

        Args:
            plan: 已深拷贝且全部旧值指纹通过预检的规划。
            operations: 无重复作用域的结构化修正。
            analyses: 风险重建使用的只读逐场结果。

        Returns:
            通过完整 Pydantic Schema 重建的修正规划。

        Raises:
            ProductionCorrectionOperationError: 任一替换破坏单元或资源引用。
        """
        for operation in operations:
            if isinstance(operation, ReplaceShootingUnitsCorrection):
                self._replace_units(plan, operation)
            elif isinstance(operation, ReplaceEntityRegistryCorrection):
                self._replace_entities(plan, operation)
            elif isinstance(operation, ReplaceResourceClassesCorrection):
                self._replace_resource_classes(plan, operation)
            elif isinstance(operation, ReplaceSceneQuantitiesCorrection):
                self._replace_quantities(plan, operation)
            elif isinstance(operation, ReplacePlannedQuantitiesCorrection):
                plan.planned_quantities = operation.replacement
            elif isinstance(operation, ReplaceSafetyMethodsCorrection):
                plan.safety_method_decisions = operation.replacement
            elif isinstance(operation, ReplaceSafetyApprovalsCorrection):
                plan.safety_approvals = operation.replacement
        plan.safety_hazards = ProductionSafetyDetector().detect(
            analyses,
            plan.occurrences,
            plan.shooting_units,
        )
        try:
            return ProductionPlan.model_validate(plan.model_dump(mode="python"))
        except ValueError as error:
            raise ProductionCorrectionOperationError(
                f"修正后的制片规划不满足 Schema：{error}"
            ) from error

    def _replace_units(
        self,
        plan: ProductionPlan,
        operation: ReplaceShootingUnitsCorrection,
    ) -> None:
        """替换一场单元、重分配需求并重建地点出现项。"""
        scene_id = operation.scene_id
        if any(item.scene_id != scene_id for item in operation.replacement):
            raise ProductionCorrectionOperationError("替换拍摄单元包含其他场景。")
        classes = {item.id: item for item in plan.resource_classes}
        old_scene_occurrences = [item for item in plan.occurrences if item.scene_id == scene_id]
        location_ids = {
            item.id
            for item in old_scene_occurrences
            if classes[item.resource_class_id].kind == ProductionResourceKind.LOCATION
        }
        movable = [item for item in old_scene_occurrences if item.id not in location_ids]
        supplied = [item for unit in operation.replacement for item in unit.occurrence_ids]
        if len(supplied) != len(set(supplied)) or set(supplied) != {item.id for item in movable}:
            raise ProductionCorrectionOperationError(
                "替换拍摄单元必须且只能把本场非地点出现项各分配一次。"
            )
        unit_by_occurrence = {
            occurrence_id: unit.id
            for unit in operation.replacement
            for occurrence_id in unit.occurrence_ids
        }
        kept = [item for item in plan.occurrences if item.id not in location_ids]
        kept = [
            item.model_copy(update={"shooting_unit_id": unit_by_occurrence[item.id]})
            if item.id in unit_by_occurrence
            else item
            for item in kept
        ]
        new_units = []
        for unit in operation.replacement:
            resource = self._location_class(plan, unit.location_name, unit.sub_location)
            source_id = f"{unit.id}/setting"
            location = ResourceOccurrence(
                id=stable_catalog_id("occurrence", source_id),
                scene_id=scene_id,
                shooting_unit_id=unit.id,
                source_requirement_id=source_id,
                resource_class_id=resource.id,
                resolution_status=ResolutionStatus.CONFIRMED,
                evidence=unit.evidence,
            )
            kept.append(location)
            new_units.append(
                unit.model_copy(update={"occurrence_ids": [location.id, *unit.occurrence_ids]})
            )
        plan.occurrences = kept
        plan.shooting_units = [
            item for item in plan.shooting_units if item.scene_id != scene_id
        ] + new_units
        self._remove_orphan_location_classes(plan)
        plan.shooting_units.sort(key=lambda item: (item.scene_id, item.ordinal))
        order = {item.id: index for index, item in enumerate(plan.shooting_units)}
        plan.occurrences.sort(key=lambda item: (order[item.shooting_unit_id], item.id))
        occurrences_by_unit: dict[str, list[str]] = defaultdict(list)
        for occurrence in plan.occurrences:
            occurrences_by_unit[occurrence.shooting_unit_id].append(occurrence.id)
        plan.shooting_units = [
            unit.model_copy(update={"occurrence_ids": occurrences_by_unit[unit.id]})
            for unit in plan.shooting_units
        ]

    @staticmethod
    def _replace_entities(
        plan: ProductionPlan,
        operation: ReplaceEntityRegistryCorrection,
    ) -> None:
        """替换实体注册表并回填所有非可替代出现项。"""
        classes = {item.id: item for item in plan.resource_classes}
        required = {
            item.id
            for item in plan.occurrences
            if classes[item.resource_class_id].identity_scope != IdentityScope.FUNGIBLE
        }
        supplied = [value for entity in operation.replacement for value in entity.occurrence_ids]
        if len(supplied) != len(set(supplied)) or set(supplied) != required:
            raise ProductionCorrectionOperationError(
                "实体注册表必须且只能覆盖全部非可替代出现项一次。"
            )
        entity_by_occurrence = {
            occurrence_id: entity
            for entity in operation.replacement
            for occurrence_id in entity.occurrence_ids
        }
        for entity in operation.replacement:
            actual_classes = {
                next(
                    item for item in plan.occurrences if item.id == occurrence_id
                ).resource_class_id
                for occurrence_id in entity.occurrence_ids
            }
            if set(entity.resource_class_ids) != actual_classes:
                raise ProductionCorrectionOperationError("实体资源类别与实际出现项不一致。")
        plan.entities = operation.replacement
        plan.occurrences = [
            item.model_copy(
                update={
                    "entity_id": entity_by_occurrence[item.id].id,
                    "resolution_status": entity_by_occurrence[item.id].status,
                }
            )
            if item.id in entity_by_occurrence
            else item.model_copy(
                update={"entity_id": None, "resolution_status": ResolutionStatus.CONFIRMED}
            )
            for item in plan.occurrences
        ]

    @staticmethod
    def _replace_resource_classes(
        plan: ProductionPlan,
        operation: ReplaceResourceClassesCorrection,
    ) -> None:
        """替换资源类别并禁止删除、新增或改写稳定 ID。"""
        current_ids = [item.id for item in plan.resource_classes]
        replacement_ids = [item.id for item in operation.replacement]
        if len(replacement_ids) != len(set(replacement_ids)) or set(replacement_ids) != set(
            current_ids
        ):
            raise ProductionCorrectionOperationError("资源类别替换必须且只能保留当前全部稳定 ID。")
        plan.resource_classes = operation.replacement

    @staticmethod
    def _replace_quantities(
        plan: ProductionPlan,
        operation: ReplaceSceneQuantitiesCorrection,
    ) -> None:
        """替换一场全部数量事实并回填出现项事实列表。"""
        scene_occurrences = {
            item.id for item in plan.occurrences if item.scene_id == operation.scene_id
        }
        current = [item for item in plan.quantity_facts if item.occurrence_id in scene_occurrences]
        required_occurrences = {item.occurrence_id for item in current}
        replacement_occurrences = {item.occurrence_id for item in operation.replacement}
        identifiers = [item.id for item in operation.replacement]
        if replacement_occurrences != required_occurrences or len(identifiers) != len(
            set(identifiers)
        ):
            raise ProductionCorrectionOperationError(
                "场景数量替换必须覆盖原有全部数量出现项且事实 ID 唯一。"
            )
        plan.quantity_facts = [
            item for item in plan.quantity_facts if item.occurrence_id not in scene_occurrences
        ] + operation.replacement
        facts_by_occurrence: dict[str, list[str]] = defaultdict(list)
        for fact in plan.quantity_facts:
            facts_by_occurrence[fact.occurrence_id].append(fact.id)
        plan.occurrences = [
            item.model_copy(update={"quantity_fact_ids": facts_by_occurrence[item.id]})
            for item in plan.occurrences
        ]

    @staticmethod
    def _location_class(
        plan: ProductionPlan,
        location_name: str,
        sub_location: str | None,
    ) -> ProductionResourceClass:
        """查找或创建替换单元所需的地点资源类别。"""
        name = " / ".join(value for value in (location_name, sub_location) if value)
        key = (
            ProductionResourceKind.LOCATION.value,
            "",
            normalize_catalog_key(name),
        )
        identifier = stable_catalog_id("resource", key)
        existing = next((item for item in plan.resource_classes if item.id == identifier), None)
        if existing is not None:
            return existing
        resource = ProductionResourceClass(
            id=identifier,
            kind=ProductionResourceKind.LOCATION,
            element_kind=None,
            canonical_name=name,
            aliases=[],
            canonical_unit=UnitCode.LOCATION,
            identity_scope=IdentityScope.FUNGIBLE,
            basis=NormalizationBasis.REVIEW_CONFIRMED,
        )
        plan.resource_classes.append(resource)
        return resource

    @staticmethod
    def _remove_orphan_location_classes(plan: ProductionPlan) -> None:
        """删除单元替换后不再被任何出现项引用的旧地点类别。"""
        referenced = {item.resource_class_id for item in plan.occurrences}
        plan.resource_classes = [
            item
            for item in plan.resource_classes
            if item.id in referenced or item.kind != ProductionResourceKind.LOCATION
        ]
