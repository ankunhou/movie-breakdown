"""把已验证制片拆解渲染为中文 Markdown 报告。"""

from __future__ import annotations

from movie_breakdown.domain.production_catalog import ProductionBreakdown
from movie_breakdown.infrastructure.fingerprint import content_fingerprint

_ELEMENT_LABELS = {
    "costume": "服装",
    "hair_makeup": "妆发",
    "hand_prop": "手持道具",
    "set_dressing": "陈设",
    "vehicle": "车辆",
    "animal": "动物",
    "stunt_action": "动作特技",
    "practical_effect": "实拍特效",
    "vfx": "视效",
    "special_equipment": "特殊设备",
    "sound_music": "现场声音/音乐",
    "other": "其他",
}
_COMPLEXITY_LABELS = {
    "low": "低",
    "medium": "中",
    "high": "高",
    "critical": "关键",
}


def render_production_markdown(breakdown: ProductionBreakdown) -> str:
    """生成包含全剧目录、逐场索引和未决问题的中文报告。

    Args:
        breakdown: 已通过确定性校验的完整制片拆解。

    Returns:
        以换行符结尾的 Markdown 正文。
    """
    catalog = breakdown.catalog
    validation = breakdown.validation
    lines = [
        f"# {breakdown.title}：制片元素拆解",
        "",
        "> 本报告只记录剧本支持的制作需求与不确定项，不代表预算、排期或采购数量。",
        (
            "> 这是 AI 辅助初稿，不是动作、枪械、烟火、车辆、动物或职业安全方案；"
            "高危内容必须由有资质的专业团队另行评估和批准。"
        ),
        "",
        "## 校验摘要",
        "",
        f"- 场景覆盖：{validation.analyzed_scene_count}/{validation.scene_count} "
        f"({validation.coverage:.1%})",
        f"- 目录项目：{validation.catalog_item_count}",
        f"- 校验问题：{len(validation.issues)}",
        "",
        "## 地点需求",
        "",
        "| 地点 | 场景 | 内外景 | 时段 | 天气要求 |",
        "|---|---|---|---|---|",
    ]
    for item in catalog.locations:
        lines.append(
            f"| {_table(item.name)} | {_join(item.scene_ids)} | "
            f"{_join(value.value for value in item.interior_exterior_modes)} | "
            f"{_join(value.value for value in item.time_of_day_modes)} | "
            f"{_join(item.weather_requirements)} |"
        )
    lines.extend(
        [
            "",
            "## 演员需求",
            "",
            "| 角色 | 场景 | 出现方式 | 别名 |",
            "|---|---|---|---|",
        ]
    )
    for item in catalog.cast:
        lines.append(
            f"| {_table(item.name)} | {_join(item.scene_ids)} | "
            f"{_join(value.value for value in item.appearance_kinds)} | "
            f"{_join(item.aliases)} |"
        )
    lines.extend(
        [
            "",
            "## 群演需求",
            "",
            "| 群体 | 描述 | 场景 | 峰值数量 | 特殊技能 |",
            "|---|---|---|---|---|",
        ]
    )
    for item in catalog.background:
        lines.append(
            f"| {_table(item.name)} | {_join(item.descriptions)} | "
            f"{_join(item.scene_ids)} | {_quantity(item.peak_quantity)} | "
            f"{_join(item.special_skills)} |"
        )
    lines.extend(
        [
            "",
            "## 制片元素总表",
            "",
            "| 类别 | 名称 | 子类型 | 场景 | 峰值数量 | 连续性/状态 | 特殊要求 |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for item in catalog.elements:
        lines.append(
            f"| {_ELEMENT_LABELS[item.kind.value]} | {_table(item.name)} | "
            f"{_join(item.subtypes)} | {_join(item.scene_ids)} | "
            f"{_quantity(item.peak_quantity)} | {_join(item.continuity_notes)} | "
            f"{_join(item.special_requirements)} |"
        )
    lines.extend(
        [
            "",
            "## 逐场制片索引",
            "",
            "| 场景 | 标题 | 地点 | 演员 | 群演组 | 元素数 | 复杂度 |",
            "|---|---|---|---|---:|---:|---|",
        ]
    )
    for scene in breakdown.scenes:
        lines.append(
            f"| {scene.scene_id} | {_table(scene.setting.raw_heading)} | "
            f"{_table(scene.setting.location_name)} | "
            f"{_join(item.character_name for item in scene.cast)} | "
            f"{len(scene.background)} | {len(scene.elements)} | "
            f"{scene.complexity.score}（{_COMPLEXITY_LABELS[scene.complexity.level.value]}） |"
        )
    uncertainties = [
        (scene.scene_id, item.subject, item.description, item.impact)
        for scene in breakdown.scenes
        for item in scene.uncertainties
    ]
    lines.extend(["", "## 待人工确认", ""])
    if uncertainties:
        lines.extend(["| 场景 | 事项 | 信息缺口 | 制片影响 |", "|---|---|---|---|"])
        lines.extend(
            f"| {scene_id} | {_table(subject)} | {_table(description)} | {_table(impact)} |"
            for scene_id, subject, description, impact in uncertainties
        )
    else:
        lines.append("本次结构化结果没有记录额外信息缺口。")
    lines.extend(
        [
            "",
            "---",
            "",
            f"来源指纹：`{breakdown.source_fingerprint}`",
            f"产物指纹：`{content_fingerprint(breakdown)}`",
            "",
        ]
    )
    return "\n".join(lines)


def _join(values) -> str:
    items = [str(value) for value in values if str(value).strip()]
    return _table("、".join(items) if items else "—")


def _table(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _quantity(value) -> str:
    if value.minimum is None:
        return f"未知（{value.unit}）"
    if value.maximum is None:
        return f"至少 {value.minimum} {value.unit}"
    if value.minimum == value.maximum:
        return f"{value.minimum} {value.unit}"
    return f"{value.minimum}-{value.maximum} {value.unit}"
