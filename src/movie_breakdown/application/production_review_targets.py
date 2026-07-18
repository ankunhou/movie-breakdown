"""从全部强制风险生成稳定制片专家评审目标。"""

from __future__ import annotations

from collections import defaultdict

from movie_breakdown.application.production_aggregation_support import stable_catalog_id
from movie_breakdown.application.production_safety_defaults import find_unsafe_defaults
from movie_breakdown.application.production_units import suspected_composite_reasons
from movie_breakdown.domain.production_catalog import ProductionBreakdown
from movie_breakdown.domain.production_planning import (
    ProductionPlan,
    QuantityProvenance,
    ResolutionStatus,
    UnitCode,
)
from movie_breakdown.domain.production_review import (
    ProductionReviewDimension,
    ProductionReviewTarget,
    ProductionReviewTargetKind,
)
from movie_breakdown.domain.scene_analysis import Evidence
from movie_breakdown.domain.source import Screenplay
from movie_breakdown.infrastructure.fingerprint import content_fingerprint


def build_production_review_targets(
    screenplay: Screenplay,
    breakdown: ProductionBreakdown,
    plan: ProductionPlan,
) -> list[ProductionReviewTarget]:
    """生成漏拆、实体、数量和全部高危内容的强制目标。

    Args:
        screenplay: 当前共享剧本和原文上下文。
        breakdown: 当前基础逐场制片结果。
        plan: 当前正式制片规划。

    Returns:
        按剧本顺序和风险类别稳定排序的完整目标集。
    """
    targets = [
        *_unit_targets(screenplay, breakdown, plan),
        *_entity_targets(plan),
        *_quantity_targets(plan),
        *_hazard_targets(plan),
        *_unsafe_default_targets(breakdown),
    ]
    scene_order = {scene.id: scene.ordinal for scene in screenplay.scenes}
    return sorted(
        targets,
        key=lambda item: (
            min(
                (scene_order.get(_scene_from_reference(value), 10**9) for value in item.references),
                default=10**9,
            ),
            item.kind.value,
            item.id,
        ),
    )


def _unit_targets(
    screenplay: Screenplay,
    breakdown: ProductionBreakdown,
    plan: ProductionPlan,
) -> list[ProductionReviewTarget]:
    """为每个场景生成必须逐场确认的拍摄单元目标。"""
    analyses = {item.scene_id: item for item in breakdown.scenes}
    units_by_scene: dict[str, list] = defaultdict(list)
    for unit in plan.shooting_units:
        units_by_scene[unit.scene_id].append(unit)
    result = []
    for scene in screenplay.scenes:
        units = units_by_scene[scene.id]
        reasons = suspected_composite_reasons(scene, analyses[scene.id], units)
        result.append(
            ProductionReviewTarget(
                id=stable_catalog_id("review-unit", scene.id),
                kind=ProductionReviewTargetKind.SHOOTING_UNIT,
                title=f"{scene.id} 拍摄单元边界",
                claim=f"当前 {len(units)} 个单元已完整表达场内地点、时段和蒙太奇变化。",
                references=[scene.id, *(item.id for item in units)],
                dimensions=[
                    ProductionReviewDimension.SOURCE_FIDELITY,
                    ProductionReviewDimension.SHOOTING_UNIT_BOUNDARY,
                ],
                evidence=_unique_evidence(item for unit in units for item in unit.evidence),
                risk_reasons=reasons
                or ["逐场拍摄单元边界必须由专家确认，避免已有多单元中的过拆或漏拆。"],
            )
        )
    return result


def _entity_targets(plan: ProductionPlan) -> list[ProductionReviewTarget]:
    """为每个未确认跨场实体生成独立目标。"""
    occurrences = {item.id: item for item in plan.occurrences}
    return [
        ProductionReviewTarget(
            id=stable_catalog_id("review-entity", entity.id),
            kind=ProductionReviewTargetKind.ENTITY,
            title=f"实体归一：{entity.canonical_name}",
            claim="这些出现项属于同一演员、动物、车辆、英雄道具或连续性群体。",
            references=[entity.id, *entity.occurrence_ids],
            dimensions=[
                ProductionReviewDimension.IDENTITY_RESOLUTION,
                ProductionReviewDimension.CONTINUITY,
                ProductionReviewDimension.SOURCE_FIDELITY,
            ],
            evidence=_unique_evidence(
                item
                for occurrence_id in entity.occurrence_ids
                for item in occurrences[occurrence_id].evidence
            ),
            risk_reasons=["跨场身份尚未由专家确认。"],
        )
        for entity in plan.entities
        if entity.status != ResolutionStatus.CONFIRMED
    ]


def _quantity_targets(plan: ProductionPlan) -> list[ProductionReviewTarget]:
    """按场景聚合未知事实或未知单位，避免把模型估算当预算数。"""
    occurrences = {item.id: item for item in plan.occurrences}
    grouped: dict[str, list] = defaultdict(list)
    for fact in plan.quantity_facts:
        if fact.provenance == QuantityProvenance.UNKNOWN or fact.unit == UnitCode.UNKNOWN:
            grouped[occurrences[fact.occurrence_id].scene_id].append(fact)
    return [
        ProductionReviewTarget(
            id=stable_catalog_id("review-quantity", scene_id),
            kind=ProductionReviewTargetKind.QUANTITY,
            title=f"{scene_id} 数量事实与父子关系",
            claim="当前未知值、总量、状态子集及单位不会被误读为可直接执行的实拍或采购数量。",
            references=[scene_id, *(item.id for item in facts)],
            dimensions=[
                ProductionReviewDimension.QUANTITY_FIDELITY,
                ProductionReviewDimension.UNIT_STANDARDIZATION,
                ProductionReviewDimension.SOURCE_FIDELITY,
            ],
            evidence=_unique_evidence(item for fact in facts for item in fact.evidence),
            risk_reasons=[f"有 {len(facts)} 条数量需要确认事实边界、单位或父子关系。"],
        )
        for scene_id, facts in grouped.items()
    ]


def _hazard_targets(plan: ProductionPlan) -> list[ProductionReviewTarget]:
    """把全部确定性高危候选无抽样地纳入评审。"""
    return [
        ProductionReviewTarget(
            id=stable_catalog_id("review-safety", hazard.id),
            kind=ProductionReviewTargetKind.SAFETY_HAZARD,
            title=f"{hazard.scene_id} · {hazard.kind.value} 安全范围",
            claim="风险类别、涉及资源、禁止方法和所需专业角色已完整识别。",
            references=[hazard.scene_id, hazard.shooting_unit_id, hazard.id],
            dimensions=[
                ProductionReviewDimension.SAFETY_SCOPE,
                ProductionReviewDimension.IMPLEMENTATION_SAFETY,
                ProductionReviewDimension.SOURCE_FIDELITY,
            ],
            evidence=hazard.evidence,
            risk_reasons=[hazard.description, *hazard.prohibited_methods],
        )
        for hazard in plan.safety_hazards
    ]


def _unsafe_default_targets(
    breakdown: ProductionBreakdown,
) -> list[ProductionReviewTarget]:
    """为模型提出的危险默认实现生成不可接受风险目标。"""
    analyses = {item.scene_id: item for item in breakdown.scenes}
    return [
        ProductionReviewTarget(
            id=stable_catalog_id("review-unsafe", (scene_id, message)),
            kind=ProductionReviewTargetKind.UNSAFE_DEFAULT,
            title=f"{scene_id} 危险默认实现",
            claim="危险模型建议已被明确否决，并替换为专业团队另行设计的安全边界。",
            references=[scene_id],
            dimensions=[
                ProductionReviewDimension.IMPLEMENTATION_SAFETY,
                ProductionReviewDimension.SOURCE_FIDELITY,
            ],
            evidence=_analysis_evidence(analyses[scene_id]),
            risk_reasons=[message],
        )
        for scene_id, message in find_unsafe_defaults(breakdown.scenes)
    ]


def _analysis_evidence(analysis) -> list[Evidence]:
    """收集一场全部需求和复杂度证据供危险默认目标核对。"""
    return _unique_evidence(
        [
            *analysis.setting.evidence,
            *(item for value in analysis.cast for item in value.evidence),
            *(item for value in analysis.background for item in value.evidence),
            *(item for value in analysis.elements for item in value.evidence),
            *(item for value in analysis.complexity.factors for item in value.evidence),
        ]
    )


def _unique_evidence(values) -> list[Evidence]:
    """按完整内容指纹去重目标证据并限制安全上限。"""
    result: dict[str, Evidence] = {}
    for value in values:
        result.setdefault(content_fingerprint(value), value)
    return list(result.values())[:24]


def _scene_from_reference(reference: str) -> str:
    """从场景或下游对象引用中提取稳定场景 ID。"""
    return reference.split("/", maxsplit=1)[0]
