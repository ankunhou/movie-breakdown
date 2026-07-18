"""把全人物分级档案渲染为中文 Markdown。"""

from __future__ import annotations

from movie_breakdown.domain.character_dossier import CharacterDossier, CharacterDossierTier
from movie_breakdown.domain.export import NarrativeBreakdown

_TIER_LABELS = {
    CharacterDossierTier.CORE: "核心人物",
    CharacterDossierTier.SUPPORTING: "重要配角",
    CharacterDossierTier.FUNCTIONAL: "功能人物",
    CharacterDossierTier.BACKGROUND: "背景／索引人物",
}


def render_dossier_markdown(breakdown: NarrativeBreakdown) -> list[str]:
    """按四级渲染全部已归一人物档案。

    Args:
        breakdown: 已通过确定性校验的完整叙事拆解。

    Returns:
        可直接拼接到主报告的 Markdown 行。
    """
    lines = [
        "## 人物分级档案",
        "",
        "本节覆盖全部已归一人物；档案摘要来自全局实体分析，分级与引用由本地规则确定。",
        "核心人物另在后文提供声明级完整小传。",
        "",
    ]
    for tier in CharacterDossierTier:
        dossiers = [item for item in breakdown.dossiers.dossiers if item.tier == tier]
        if not dossiers:
            continue
        lines.extend([f"### {_TIER_LABELS[tier]}（{len(dossiers)}）", ""])
        for dossier in dossiers:
            lines.extend(_render_dossier(dossier, breakdown))
    return lines


def _render_dossier(
    dossier: CharacterDossier,
    breakdown: NarrativeBreakdown,
) -> list[str]:
    """渲染单个人物的摘要、统计、事件和关系索引。"""
    aliases = "、".join(dossier.aliases) or "无"
    reasons = "；".join(dossier.classification_reasons)
    first_scene = dossier.first_scene_id or "无有效场景"
    lines = [
        f"#### {dossier.name}",
        "",
        dossier.summary,
        "",
        f"- 档案 ID：`{dossier.character_id}`",
        f"- 分级依据：{reasons}",
        f"- 别名：{aliases}",
        f"- 首次出场：{first_scene}",
        f"- 出场 / 事件 / 关系：{dossier.signals.scene_count} / "
        f"{dossier.signals.event_count} / {dossier.signals.relationship_count}",
    ]
    if dossier.tier in {CharacterDossierTier.CORE, CharacterDossierTier.SUPPORTING}:
        lines.extend(_render_events(dossier, breakdown))
        lines.extend(_render_relationships(dossier, breakdown))
    if dossier.tier == CharacterDossierTier.CORE:
        lines.append("- 完整小传：见“核心人物完整小传”章节")
    lines.append("")
    return lines


def _render_events(
    dossier: CharacterDossier,
    breakdown: NarrativeBreakdown,
) -> list[str]:
    """为核心和重要配角展示最多三个全局事件摘要。"""
    events = {item.id: item for item in breakdown.events.events}
    summaries = [events[item].summary for item in dossier.event_ids[:3] if item in events]
    if not summaries:
        return []
    suffix = "；另有更多" if len(dossier.event_ids) > 3 else ""
    return [f"- 关键事件：{'；'.join(summaries)}{suffix}"]


def _render_relationships(
    dossier: CharacterDossier,
    breakdown: NarrativeBreakdown,
) -> list[str]:
    """为核心和重要配角展示最多三条人物关系。"""
    relationships = {item.id: item for item in breakdown.relationships.relationships}
    characters = {item.id: item.name for item in breakdown.entities.characters}
    summaries: list[str] = []
    for relation_id in dossier.relationship_ids[:3]:
        relation = relationships.get(relation_id)
        if relation is None:
            continue
        other_id = (
            relation.target_character_id
            if relation.source_character_id == dossier.character_id
            else relation.source_character_id
        )
        summaries.append(
            f"{characters.get(other_id, other_id)}（{relation.relation_type}）："
            f"{relation.development}"
        )
    if not summaries:
        return []
    suffix = "；另有更多" if len(dossier.relationship_ids) > 3 else ""
    return [f"- 关键关系：{'；'.join(summaries)}{suffix}"]
