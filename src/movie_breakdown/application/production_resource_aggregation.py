"""群演和实物类制片资源的确定性汇总函数。"""

from __future__ import annotations

from collections import defaultdict

from movie_breakdown.application.production_aggregation_support import (
    display_text,
    normalize_catalog_key,
    peak_quantity,
    stable_catalog_id,
    unique_evidence,
    unique_texts,
    unique_values,
)
from movie_breakdown.domain.production_catalog import (
    AggregatedProductionElement,
    ProductionBackgroundGroup,
)
from movie_breakdown.domain.production_common import ProductionElementKind
from movie_breakdown.domain.production_scene import (
    BackgroundRequirement,
    ProductionElement,
    SceneProductionAnalysis,
)


def build_background_catalog(
    analyses: list[SceneProductionAnalysis],
    scene_order: dict[str, int],
) -> list[ProductionBackgroundGroup]:
    """按群体名称和描述汇总群演峰值需求。

    Args:
        analyses: 已按剧本顺序排列的完整逐场结果。
        scene_order: 场景 ID 到剧本顺序的映射。

    Returns:
        不会仅凭相同群体名称误合并描述差异的群演目录。
    """
    groups: dict[tuple[str, str], list[tuple[str, BackgroundRequirement]]] = defaultdict(list)
    for analysis in analyses:
        for item in analysis.background:
            key = (
                normalize_catalog_key(item.group_name),
                normalize_catalog_key(item.description),
            )
            groups[key].append((analysis.scene_id, item))
    result: list[ProductionBackgroundGroup] = []
    for key in sorted(groups):
        entries = groups[key]
        result.append(
            ProductionBackgroundGroup(
                id=stable_catalog_id("background", key),
                name=display_text(entries[0][1].group_name),
                descriptions=unique_texts(item.description for _, item in entries),
                scene_ids=unique_values(scene_id for scene_id, _ in entries),
                source_requirement_ids=[f"{scene_id}/{item.id}" for scene_id, item in entries],
                peak_quantity=peak_quantity(item.quantity for _, item in entries),
                special_skills=unique_texts(
                    skill for _, item in entries for skill in item.special_skills
                ),
                evidence=unique_evidence(
                    (evidence for _, item in entries for evidence in item.evidence),
                    scene_order,
                ),
            )
        )
    return result


def build_element_catalog(
    analyses: list[SceneProductionAnalysis],
    scene_order: dict[str, int],
) -> list[AggregatedProductionElement]:
    """按类别、名称及兜底子类型汇总制片元素。

    Args:
        analyses: 已按剧本顺序排列的完整逐场结果。
        scene_order: 场景 ID 到剧本顺序的映射。

    Returns:
        保留数量峰值、连续性和特殊要求的元素目录。
    """
    groups: dict[tuple[str, str, str], list[tuple[str, ProductionElement]]] = defaultdict(list)
    for analysis in analyses:
        for item in analysis.elements:
            subtype = item.subtype if item.kind == ProductionElementKind.OTHER else None
            key = (
                item.kind.value,
                normalize_catalog_key(item.name),
                normalize_catalog_key(subtype),
            )
            groups[key].append((analysis.scene_id, item))
    result: list[AggregatedProductionElement] = []
    for key in sorted(groups):
        entries = groups[key]
        names = unique_texts(item.name for _, item in entries)
        result.append(
            AggregatedProductionElement(
                id=stable_catalog_id("element", key),
                kind=entries[0][1].kind,
                name=names[0],
                aliases=names[1:],
                subtypes=unique_texts(item.subtype for _, item in entries),
                scene_ids=unique_values(scene_id for scene_id, _ in entries),
                source_requirement_ids=[f"{scene_id}/{item.id}" for scene_id, item in entries],
                peak_quantity=peak_quantity(item.quantity for _, item in entries),
                continuity_notes=unique_texts(item.state_or_continuity for _, item in entries),
                special_requirements=unique_texts(
                    value for _, item in entries for value in item.special_requirements
                ),
                evidence=unique_evidence(
                    (evidence for _, item in entries for evidence in item.evidence),
                    scene_order,
                ),
            )
        )
    return result
