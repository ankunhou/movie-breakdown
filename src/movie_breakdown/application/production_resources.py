"""从逐场制片需求构建资源类别、出现项、候选实体和数量事实。"""

from __future__ import annotations

from collections import defaultdict

from movie_breakdown.application.production_aggregation_support import (
    display_text,
    normalize_catalog_key,
    stable_catalog_id,
    unique_texts,
)
from movie_breakdown.application.production_quantities import QuantityFactBuilder, normalize_unit
from movie_breakdown.application.production_resource_support import (
    ProductionResourcePlanResult,
    identity_scope,
    unit_for_line,
)
from movie_breakdown.domain.production_common import ProductionElementKind
from movie_breakdown.domain.production_planning import (
    IdentityScope,
    NormalizationBasis,
    ProductionEntity,
    ProductionResourceClass,
    ProductionResourceKind,
    ResolutionStatus,
    ResourceOccurrence,
    ShootingUnit,
    UnitCode,
)
from movie_breakdown.domain.production_scene import (
    BackgroundRequirement,
    CastRequirement,
    ProductionElement,
    SceneProductionAnalysis,
)


class ProductionResourcePlanner:
    """把分散需求转换为稳定资源出现项，不猜测跨名称同义实体。"""

    def __init__(self, quantity_builder: QuantityFactBuilder | None = None) -> None:
        """创建资源规划器。

        Args:
            quantity_builder: 可替换的数量事实构建策略。
        """
        self._quantity_builder = quantity_builder or QuantityFactBuilder()

    def build(
        self,
        analyses: list[SceneProductionAnalysis],
        shooting_units: list[ShootingUnit],
    ) -> ProductionResourcePlanResult:
        """构建资源类别、出现项、候选实体并回填单元分配。

        Args:
            analyses: 已按剧本顺序排列的逐场制片结果。
            shooting_units: 覆盖相同场景的有序拍摄单元。

        Returns:
            所有 ID 与输入顺序无关的完整资源规划结果。
        """
        units_by_scene: dict[str, list[ShootingUnit]] = defaultdict(list)
        for unit in shooting_units:
            units_by_scene[unit.scene_id].append(unit)
        classes: dict[str, ProductionResourceClass] = {}
        occurrences: list[ResourceOccurrence] = []
        quantity_inputs: list[
            tuple[ResourceOccurrence, BackgroundRequirement | ProductionElement]
        ] = []
        for analysis in analyses:
            units = units_by_scene[analysis.scene_id]
            occurrences.extend(self._location_occurrences(units, classes))
            for item in analysis.cast:
                occurrence = self._cast_occurrence(analysis.scene_id, item, units, classes)
                occurrences.append(occurrence)
            for item in analysis.background:
                occurrence = self._background_occurrence(analysis.scene_id, item, units, classes)
                occurrences.append(occurrence)
                quantity_inputs.append((occurrence, item))
            for item in analysis.elements:
                occurrence = self._element_occurrence(analysis.scene_id, item, units, classes)
                occurrences.append(occurrence)
                quantity_inputs.append((occurrence, item))
        facts = [
            self._quantity_builder.build(occurrence, item) for occurrence, item in quantity_inputs
        ]
        fact_ids = {fact.occurrence_id: fact.id for fact in facts}
        occurrences = [
            item.model_copy(
                update={"quantity_fact_ids": [fact_ids[item.id]] if item.id in fact_ids else []}
            )
            for item in occurrences
        ]
        entities, occurrences = self._entities(list(classes.values()), occurrences)
        assigned_units = self._assign_occurrences(shooting_units, occurrences)
        return ProductionResourcePlanResult(
            resource_classes=sorted(classes.values(), key=lambda item: item.id),
            entities=entities,
            occurrences=occurrences,
            quantity_facts=facts,
            shooting_units=assigned_units,
        )

    def _location_occurrences(
        self,
        units: list[ShootingUnit],
        classes: dict[str, ProductionResourceClass],
    ) -> list[ResourceOccurrence]:
        """为每个单元建立独立地点出现项。"""
        result = []
        for unit in units:
            name = " / ".join(value for value in (unit.location_name, unit.sub_location) if value)
            resource = self._resource_class(
                classes,
                ProductionResourceKind.LOCATION,
                name,
                UnitCode.LOCATION,
                IdentityScope.FUNGIBLE,
            )
            source_id = f"{unit.id}/setting"
            result.append(
                ResourceOccurrence(
                    id=stable_catalog_id("occurrence", source_id),
                    scene_id=unit.scene_id,
                    shooting_unit_id=unit.id,
                    source_requirement_id=source_id,
                    resource_class_id=resource.id,
                    resolution_status=ResolutionStatus.CONFIRMED,
                    evidence=unit.evidence,
                )
            )
        return result

    def _cast_occurrence(
        self,
        scene_id: str,
        item: CastRequirement,
        units: list[ShootingUnit],
        classes: dict[str, ProductionResourceClass],
    ) -> ResourceOccurrence:
        """构建演员出现项并优先保留已有角色 ID。"""
        key_name = item.character_id or item.character_name
        resource = self._resource_class(
            classes,
            ProductionResourceKind.CAST,
            key_name,
            UnitCode.PERSON,
            IdentityScope.CONTINUITY,
            alias=item.character_name,
        )
        return self._occurrence(scene_id, item.id, item.evidence, units, resource)

    def _background_occurrence(
        self,
        scene_id: str,
        item: BackgroundRequirement,
        units: list[ShootingUnit],
        classes: dict[str, ProductionResourceClass],
    ) -> ResourceOccurrence:
        """按名称和描述保守区分群演状态组。"""
        name = f"{item.group_name}｜{item.description}"
        resource = self._resource_class(
            classes,
            ProductionResourceKind.BACKGROUND,
            name,
            UnitCode.PERSON,
            IdentityScope.COHORT,
            alias=item.group_name,
        )
        return self._occurrence(scene_id, item.id, item.evidence, units, resource)

    def _element_occurrence(
        self,
        scene_id: str,
        item: ProductionElement,
        units: list[ShootingUnit],
        classes: dict[str, ProductionResourceClass],
    ) -> ResourceOccurrence:
        """构建元素出现项并识别必须维护连续性的资源类型。"""
        unit = normalize_unit(item.quantity.unit, element_kind=item.kind, name=item.name)
        scope = identity_scope(item)
        resource = self._resource_class(
            classes,
            ProductionResourceKind.ELEMENT,
            item.name,
            unit,
            scope,
            element_kind=item.kind,
        )
        occurrence = self._occurrence(scene_id, item.id, item.evidence, units, resource)
        return occurrence.model_copy(update={"state_after": item.state_or_continuity})

    def _occurrence(
        self,
        scene_id: str,
        requirement_id: str,
        evidence,
        units: list[ShootingUnit],
        resource: ProductionResourceClass,
    ) -> ResourceOccurrence:
        """按第一条证据把需求分配给覆盖该行的拍摄单元。"""
        source_id = f"{scene_id}/{requirement_id}"
        unit = unit_for_line(units, evidence[0].source_span.line_start)
        return ResourceOccurrence(
            id=stable_catalog_id("occurrence", source_id),
            scene_id=scene_id,
            shooting_unit_id=unit.id,
            source_requirement_id=source_id,
            resource_class_id=resource.id,
            resolution_status=(
                ResolutionStatus.CONFIRMED
                if resource.identity_scope == IdentityScope.FUNGIBLE
                else ResolutionStatus.UNRESOLVED
            ),
            evidence=evidence,
        )

    def _resource_class(
        self,
        classes: dict[str, ProductionResourceClass],
        kind: ProductionResourceKind,
        name: str,
        unit: UnitCode,
        scope: IdentityScope,
        *,
        alias: str | None = None,
        element_kind: ProductionElementKind | None = None,
    ) -> ProductionResourceClass:
        """查找或创建一个最小规范键完全一致的资源类别。"""
        normalized_name = normalize_catalog_key(name)
        key = (kind.value, element_kind.value if element_kind else "", unit.value, normalized_name)
        identifier = stable_catalog_id("resource", key)
        existing = classes.get(identifier)
        if existing is not None:
            aliases = unique_texts([*existing.aliases, alias, name])
            classes[identifier] = existing.model_copy(update={"aliases": aliases})
            return classes[identifier]
        resource = ProductionResourceClass(
            id=identifier,
            kind=kind,
            element_kind=element_kind,
            canonical_name=display_text(name),
            aliases=unique_texts([alias]) if alias and alias != name else [],
            canonical_unit=unit,
            identity_scope=scope,
            basis=NormalizationBasis.DETERMINISTIC,
        )
        classes[identifier] = resource
        return resource

    def _entities(
        self,
        classes: list[ProductionResourceClass],
        occurrences: list[ResourceOccurrence],
    ) -> tuple[list[ProductionEntity], list[ResourceOccurrence]]:
        """为连续性或群体资源创建待人工确认的实体候选。"""
        class_by_id = {item.id: item for item in classes}
        by_class: dict[str, list[ResourceOccurrence]] = defaultdict(list)
        for occurrence in occurrences:
            by_class[occurrence.resource_class_id].append(occurrence)
        entities: list[ProductionEntity] = []
        entity_by_class: dict[str, str] = {}
        for class_id, items in sorted(by_class.items()):
            resource = class_by_id[class_id]
            if resource.identity_scope == IdentityScope.FUNGIBLE:
                continue
            entity_id = stable_catalog_id("entity-candidate", class_id)
            entity_by_class[class_id] = entity_id
            entities.append(
                ProductionEntity(
                    id=entity_id,
                    resource_class_ids=[class_id],
                    canonical_name=resource.canonical_name,
                    aliases=resource.aliases,
                    status=ResolutionStatus.UNRESOLVED,
                    occurrence_ids=[item.id for item in items],
                    basis=NormalizationBasis.DETERMINISTIC,
                )
            )
        updated = [
            item.model_copy(update={"entity_id": entity_by_class.get(item.resource_class_id)})
            for item in occurrences
        ]
        return entities, updated

    @staticmethod
    def _assign_occurrences(
        units: list[ShootingUnit],
        occurrences: list[ResourceOccurrence],
    ) -> list[ShootingUnit]:
        """把出现项 ID 回填到其所属拍摄单元。"""
        by_unit: dict[str, list[str]] = defaultdict(list)
        for item in occurrences:
            by_unit[item.shooting_unit_id].append(item.id)
        return [unit.model_copy(update={"occurrence_ids": by_unit[unit.id]}) for unit in units]
