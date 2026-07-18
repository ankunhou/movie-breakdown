"""渲染制片规划中的数量、安全和分级问题 CSV。"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable

from movie_breakdown.domain.production_planning import ProductionPlan, QuantityBounds
from movie_breakdown.domain.production_planning_validation import (
    ProductionPlanningValidationReport,
)


def render_quantities_csv(plan: ProductionPlan) -> str:
    """统一渲染剧本事实与人工计划数量，不隐藏未知上界和父子关系。

    Args:
        plan: 当前制片规划。

    Returns:
        以换行符结尾的 UTF-8 CSV。
    """
    rows: list[list[object]] = [
        [
            "记录类型",
            "数量ID",
            "出现项ID",
            "计划用途",
            "下界",
            "上界",
            "上界语义",
            "标准单位",
            "原始单位",
            "原始表达",
            "数量角色",
            "父数量ID",
            "状态",
            "互斥组",
            "来源",
            "派生来源ID",
            "复核人",
            "决策ID",
            "输入指纹",
            "理由",
        ]
    ]
    for item in plan.quantity_facts:
        rows.append(
            [
                "剧本事实",
                item.id,
                item.occurrence_id,
                "",
                _lower(item.bounds),
                _upper(item.bounds),
                _upper_semantics(item.bounds),
                item.unit.value,
                item.raw_unit,
                item.raw_expression or "",
                item.role.value,
                item.parent_quantity_id or "",
                item.state or "",
                item.exclusive_group or "",
                item.provenance.value,
                _join(item.derived_from_ids),
                "",
                "",
                "",
                "",
            ]
        )
    for item in plan.planned_quantities:
        rows.append(
            [
                "人工计划",
                item.id,
                item.occurrence_id,
                item.purpose.value,
                _lower(item.bounds),
                _upper(item.bounds),
                _upper_semantics(item.bounds),
                item.unit.value,
                "",
                "",
                "",
                "",
                "",
                "",
                "manual_decision",
                "",
                item.reviewer,
                item.decision_id,
                item.input_fingerprint,
                item.rationale,
            ]
        )
    return _csv(rows)


def render_safety_csv(plan: ProductionPlan) -> str:
    """渲染高危候选、逐角色审批和危险方法替代决定。

    Args:
        plan: 当前制片规划。

    Returns:
        以换行符结尾的 UTF-8 CSV。
    """
    rows: list[list[object]] = [
        [
            "记录类型",
            "风险或决策ID",
            "场景ID",
            "拍摄单元ID",
            "风险类别",
            "风险级别",
            "说明",
            "必需审批角色",
            "触发规则",
            "强制控制",
            "禁止方法",
            "范围指纹",
            "审批人",
            "审批角色",
            "审批人类别",
            "审批决定",
            "审批理由",
            "审批控制",
            "审批范围匹配",
            "安全替代政策",
        ]
    ]
    hazard_ids = {item.id for item in plan.safety_hazards}
    for hazard in plan.safety_hazards:
        approvals = [item for item in plan.safety_approvals if item.hazard_id == hazard.id]
        rows.extend(
            [_hazard_row(hazard, None)]
            if not approvals
            else (_hazard_row(hazard, approval) for approval in approvals)
        )
    for approval in plan.safety_approvals:
        if approval.hazard_id not in hazard_ids:
            rows.append(
                [
                    "孤立审批",
                    approval.hazard_id,
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    approval.scope_fingerprint,
                    approval.reviewer,
                    approval.reviewer_role,
                    approval.reviewer_kind.value,
                    approval.decision.value,
                    approval.reason,
                    _join(approval.required_controls),
                    "无法匹配风险",
                    "",
                ]
            )
    for decision in plan.safety_method_decisions:
        rows.append(
            [
                "危险方法决定",
                decision.id,
                decision.scene_id,
                "",
                "",
                "",
                decision.rationale,
                "",
                "",
                "",
                decision.prohibited_method,
                decision.analysis_fingerprint,
                decision.reviewer,
                "",
                decision.reviewer_kind.value,
                "",
                "",
                "",
                "",
                decision.replacement_policy,
            ]
        )
    return _csv(rows)


def render_issues_csv(validation: ProductionPlanningValidationReport) -> str:
    """渲染全部校验问题及其阻断准备度。

    Args:
        validation: 当前规划的分级校验报告。

    Returns:
        以换行符结尾的 UTF-8 CSV。
    """
    rows: list[list[object]] = [["严重度", "代码", "消息", "引用", "阻断层级"]]
    rows.extend(
        [
            item.severity.value,
            item.code,
            item.message,
            item.reference or "",
            _join(level.value for level in item.blocks_levels),
        ]
        for item in validation.issues
    )
    return _csv(rows)


def _hazard_row(hazard, approval) -> list[object]:
    return [
        "高危候选",
        hazard.id,
        hazard.scene_id,
        hazard.shooting_unit_id,
        hazard.kind.value,
        hazard.risk_level.value,
        hazard.description,
        _join(hazard.required_reviewer_roles),
        _join(hazard.trigger_rule_ids),
        _join(hazard.mandatory_controls),
        _join(hazard.prohibited_methods),
        hazard.scope_fingerprint,
        approval.reviewer if approval else "",
        approval.reviewer_role if approval else "",
        approval.reviewer_kind.value if approval else "",
        approval.decision.value if approval else "",
        approval.reason if approval else "",
        _join(approval.required_controls) if approval else "",
        (
            str(approval.scope_fingerprint == hazard.scope_fingerprint).lower()
            if approval
            else "未审批"
        ),
        "",
    ]


def _lower(bounds: QuantityBounds) -> object:
    return bounds.minimum if bounds.minimum is not None else "未知"


def _upper(bounds: QuantityBounds) -> object:
    return bounds.maximum if bounds.maximum is not None else "未知"


def _upper_semantics(bounds: QuantityBounds) -> str:
    if bounds.maximum is not None:
        return "闭合上界"
    if bounds.minimum is not None:
        return "开放上界（至少）"
    return "全量未知"


def _join(values: Iterable[object]) -> str:
    return "、".join(str(value) for value in values if str(value).strip())


def _csv(rows: list[list[object]]) -> str:
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\n").writerows(rows)
    return output.getvalue()
