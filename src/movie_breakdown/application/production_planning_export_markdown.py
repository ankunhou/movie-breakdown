"""把本地制片规划渲染为中文人工复核报告。"""

from __future__ import annotations

from collections import defaultdict

from movie_breakdown.domain.production_planning import ProductionPlan, QuantityBounds
from movie_breakdown.domain.production_planning_validation import (
    ProductionPlanningValidationReport,
)


def render_planning_markdown(
    plan: ProductionPlan,
    validation: ProductionPlanningValidationReport,
) -> str:
    """生成同时说明三级准备度、数量语义和安全审批的中文报告。

    Args:
        plan: 当前制片规划。
        validation: 绑定当前规划指纹的分级校验报告。

    Returns:
        以换行符结尾的 Markdown 正文。
    """
    lines = [
        "# 制片规划与安全复核报告",
        "",
        "> 本报告区分剧本数量事实与人工计划数量；未知上界不得当作确定采购量。",
        "> `shoot_ready` 只表示全部确定性门禁通过，不替代现场风险评估和动态安全管理。",
        "",
        "## 三级准备度",
        "",
        "| 层级 | 结论 | 使用边界 |",
        "|---|---|---|",
        f"| `draft_valid`（结构草稿） | {_status(validation.draft_valid)} | 结构与引用可审阅 |",
        (
            f"| `catalog_ready`（资源目录） | {_status(validation.catalog_ready)} | "
            "实体与单位可进入目录协同 |"
        ),
        (
            f"| `shoot_ready`（拍摄准备） | {_status(validation.shoot_ready)} | "
            "高危角色审批齐备后才可通过 |"
        ),
        "",
        "## 规模摘要",
        "",
        f"- 场景：{validation.scene_count}",
        f"- 拍摄单元：{validation.shooting_unit_count}",
        f"- 资源类别：{validation.resource_class_count}",
        f"- 跨场实体：{validation.entity_count}（未确认 {validation.unresolved_entity_count}）",
        f"- 未标准化单位：{validation.unknown_unit_count}",
        f"- 高危候选：{validation.hazard_count}",
        f"- 合格专业审批记录：{validation.qualified_approval_count}",
        "",
    ]
    _append_units(lines, plan)
    _append_entities(lines, plan)
    _append_quantities(lines, plan)
    _append_safety(lines, plan)
    _append_issues(lines, validation)
    lines.extend(
        [
            "",
            "---",
            "",
            f"来源指纹：`{plan.source_fingerprint}`",
            f"基础拆解指纹：`{plan.base_breakdown_fingerprint}`",
            f"规划指纹：`{validation.plan_fingerprint}`",
            "",
        ]
    )
    return "\n".join(lines)


def _append_units(lines: list[str], plan: ProductionPlan) -> None:
    lines.extend(
        [
            "## 拍摄单元",
            "",
            "| 单元 | 场景 | 原文行 | 地点 | 内外景/时段 | 拆分原因 |",
            "|---|---|---:|---|---|---|",
        ]
    )
    for item in plan.shooting_units:
        lines.append(
            f"| `{item.id}` | {_cell(item.scene_id)} | "
            f"{item.source_span.line_start}-{item.source_span.line_end} | "
            f"{_cell(item.location_name)} | {item.interior_exterior.value}/"
            f"{item.time_of_day.value} | "
            f"{_list(reason.value for reason in item.split_reasons)} |"
        )
    lines.append("")


def _append_entities(lines: list[str], plan: ProductionPlan) -> None:
    lines.extend(
        [
            "## 跨场实体归一",
            "",
            "| 实体 | 规范名称 | 资源类别 | 状态 | 归一依据 | 出现项 |",
            "|---|---|---|---|---|---:|",
        ]
    )
    if not plan.entities:
        lines.append("| — | — | — | — | — | 0 |")
    for item in plan.entities:
        lines.append(
            f"| `{item.id}` | {_cell(item.canonical_name)} | "
            f"{_list(item.resource_class_ids)} | `{item.status.value}` | "
            f"`{item.basis.value}` | {len(item.occurrence_ids)} |"
        )
    lines.append("")


def _append_quantities(lines: list[str], plan: ProductionPlan) -> None:
    lines.extend(
        [
            "## 数量与单位语义",
            "",
            "| 类型 | 数量 | 出现项 | 范围 | 单位 | 角色/用途 | 父数量 | 来源/决策 |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )
    if not plan.quantity_facts and not plan.planned_quantities:
        lines.append("| — | — | — | — | — | — | — | — |")
    for item in plan.quantity_facts:
        lines.append(
            f"| 剧本事实 | `{item.id}` | `{item.occurrence_id}` | "
            f"{_bounds(item.bounds)} | `{item.unit.value}` | `{item.role.value}` | "
            f"{_code(item.parent_quantity_id)} | `{item.provenance.value}` |"
        )
    for item in plan.planned_quantities:
        lines.append(
            f"| 人工计划 | `{item.id}` | `{item.occurrence_id}` | "
            f"{_bounds(item.bounds)} | `{item.unit.value}` | `{item.purpose.value}` | — | "
            f"{_cell(item.reviewer)} / `{item.decision_id}` |"
        )
    lines.extend(
        [
            "",
            "> `subset` / `per_member` 通过“父数量”列关联，不能与父项直接相加；"
            "“至少 N”表示上界未知。",
            "",
        ]
    )


def _append_safety(lines: list[str], plan: ProductionPlan) -> None:
    approvals = defaultdict(list)
    for item in plan.safety_approvals:
        approvals[item.hazard_id].append(item)
    lines.extend(
        [
            "## 高危元素强制安全复核",
            "",
            "| 风险 | 场景/单元 | 级别 | 必需审批角色 | 已有审批 |",
            "|---|---|---|---|---|",
        ]
    )
    if not plan.safety_hazards:
        lines.append("| — | — | — | — | 未发现确定性候选 |")
    for hazard in plan.safety_hazards:
        decisions = [
            f"{item.reviewer_role}:{item.decision.value}({item.reviewer_kind.value})"
            for item in approvals[hazard.id]
        ]
        lines.append(
            f"| `{hazard.id}` / `{hazard.kind.value}` | {_cell(hazard.scene_id)} / "
            f"`{hazard.shooting_unit_id}` | `{hazard.risk_level.value}` | "
            f"{_list(hazard.required_reviewer_roles)} | {_list(decisions)} |"
        )
    lines.extend(["", "### 危险默认方法替代决定", ""])
    if not plan.safety_method_decisions:
        lines.append("尚无危险默认方法替代决定。")
    else:
        lines.extend(
            [
                "| 决策 | 场景 | 禁止方法 | 安全替代边界 | 复核人类别 |",
                "|---|---|---|---|---|",
            ]
        )
        for item in plan.safety_method_decisions:
            lines.append(
                f"| `{item.id}` | {_cell(item.scene_id)} | {_cell(item.prohibited_method)} | "
                f"{_cell(item.replacement_policy)} | `{item.reviewer_kind.value}` |"
            )
    lines.append("")


def _append_issues(
    lines: list[str],
    validation: ProductionPlanningValidationReport,
) -> None:
    lines.extend(["## 校验问题", ""])
    if not validation.issues:
        lines.append("当前没有分级校验问题。")
        return
    lines.extend(
        [
            "| 严重度 | 代码 | 问题 | 引用 | 阻断层级 |",
            "|---|---|---|---|---|",
        ]
    )
    for item in validation.issues:
        lines.append(
            f"| `{item.severity.value}` | `{item.code}` | {_cell(item.message)} | "
            f"{_code(item.reference)} | {_list(level.value for level in item.blocks_levels)} |"
        )


def _status(value: bool) -> str:
    return "通过" if value else "阻断"


def _bounds(value: QuantityBounds) -> str:
    if value.minimum is None:
        return "未知（上下界均未知）"
    if value.maximum is None:
        return f"至少 {value.minimum}（上界未知）"
    if value.minimum == value.maximum:
        return str(value.minimum)
    return f"{value.minimum}-{value.maximum}"


def _list(values) -> str:
    items = [str(value) for value in values if str(value).strip()]
    return _cell("、".join(items)) if items else "—"


def _code(value: str | None) -> str:
    return f"`{value}`" if value else "—"


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
