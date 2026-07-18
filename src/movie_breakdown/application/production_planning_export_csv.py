"""为本地制片规划生成便于表格审阅的中文 CSV。"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable

from movie_breakdown.domain.production_planning import ProductionPlan


def render_shooting_units_csv(plan: ProductionPlan) -> str:
    """渲染拍摄单元、原文范围和拆分依据。

    Args:
        plan: 当前制片规划。

    Returns:
        以换行符结尾的 UTF-8 CSV。
    """
    rows: list[list[object]] = [
        [
            "拍摄单元ID",
            "场景ID",
            "序号",
            "标签",
            "描述",
            "起始行",
            "结束行",
            "拆分原因",
            "地点",
            "子地点",
            "内外景",
            "时段",
            "原始时段",
            "出现项ID",
        ]
    ]
    rows.extend(
        [
            item.id,
            item.scene_id,
            item.ordinal,
            item.label,
            item.description,
            item.source_span.line_start,
            item.source_span.line_end,
            _join(reason.value for reason in item.split_reasons),
            item.location_name,
            item.sub_location or "",
            item.interior_exterior.value,
            item.time_of_day.value,
            item.raw_time_label or "",
            _join(item.occurrence_ids),
        ]
        for item in plan.shooting_units
    )
    return _csv(rows)


def render_resources_csv(plan: ProductionPlan) -> str:
    """渲染资源类别及其跨场实体归一状态。

    Args:
        plan: 当前制片规划。

    Returns:
        以换行符结尾的 UTF-8 CSV；一个实体跨多个类别时会逐类别列出。
    """
    header = [
        "资源类别ID",
        "资源种类",
        "元素子类",
        "规范名称",
        "类别别名",
        "标准单位",
        "身份范围",
        "类别归一依据",
        "实体ID",
        "实体名称",
        "实体别名",
        "实体状态",
        "实体归一依据",
        "实体出现项数",
        "实体来源类别ID",
        "重定向来源实体ID",
        "备注",
    ]
    rows: list[list[object]] = [header]
    for resource in plan.resource_classes:
        entities = [item for item in plan.entities if resource.id in item.resource_class_ids]
        if not entities:
            rows.append(_resource_row(resource, None))
            continue
        rows.extend(_resource_row(resource, entity) for entity in entities)
    return _csv(rows)


def render_occurrences_csv(plan: ProductionPlan) -> str:
    """渲染跨单元资源出现项及其实体解析状态。

    Args:
        plan: 当前制片规划。

    Returns:
        以换行符结尾的 UTF-8 CSV。
    """
    classes = {item.id: item for item in plan.resource_classes}
    entities = {item.id: item for item in plan.entities}
    rows: list[list[object]] = [
        [
            "出现项ID",
            "场景ID",
            "拍摄单元ID",
            "来源需求ID",
            "资源类别ID",
            "资源名称",
            "资源种类",
            "实体ID",
            "实体名称",
            "解析状态",
            "数量事实ID",
            "前置状态",
            "后置状态",
            "证据行",
        ]
    ]
    for item in plan.occurrences:
        resource = classes.get(item.resource_class_id)
        entity = entities.get(item.entity_id or "")
        rows.append(
            [
                item.id,
                item.scene_id,
                item.shooting_unit_id,
                item.source_requirement_id,
                item.resource_class_id,
                resource.canonical_name if resource else "",
                resource.kind.value if resource else "",
                item.entity_id or "",
                entity.canonical_name if entity else "",
                item.resolution_status.value,
                _join(item.quantity_fact_ids),
                item.state_before or "",
                item.state_after or "",
                _join(
                    f"{evidence.source_span.line_start}-{evidence.source_span.line_end}"
                    for evidence in item.evidence
                ),
            ]
        )
    return _csv(rows)


def _resource_row(resource, entity) -> list[object]:
    return [
        resource.id,
        resource.kind.value,
        resource.element_kind.value if resource.element_kind else "",
        resource.canonical_name,
        _join(resource.aliases),
        resource.canonical_unit.value,
        resource.identity_scope.value,
        resource.basis.value,
        entity.id if entity else "",
        entity.canonical_name if entity else "",
        _join(entity.aliases) if entity else "",
        entity.status.value if entity else "",
        entity.basis.value if entity else "",
        len(entity.occurrence_ids) if entity else 0,
        _join(entity.resource_class_ids) if entity else "",
        _join(entity.redirect_from_ids) if entity else "",
        _join(entity.notes) if entity else "",
    ]


def _join(values: Iterable[object]) -> str:
    return "、".join(str(value) for value in values if str(value).strip())


def _csv(rows: list[list[object]]) -> str:
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\n").writerows(rows)
    return output.getvalue()
