"""把声明级人物小传渲染为不混淆事实与推断的 Markdown。"""

from __future__ import annotations

from collections import defaultdict

from movie_breakdown.domain.base import Confidence
from movie_breakdown.domain.character_biography import (
    BiographyClaimBasis,
    BiographyClaimCategory,
    CharacterBiography,
    CharacterBiographyClaim,
)
from movie_breakdown.domain.export import NarrativeBreakdown
from movie_breakdown.domain.global_analysis import Character, CharacterArc
from movie_breakdown.domain.scene_analysis import Evidence

_BASIS_LABELS = {
    BiographyClaimBasis.OBSERVED: "剧本直接呈现",
    BiographyClaimBasis.REPORTED: "角色或文本转述",
    BiographyClaimBasis.INFERRED: "分析推断",
}
_CATEGORY_LABELS = {
    BiographyClaimCategory.OVERVIEW: "人物概览",
    BiographyClaimCategory.IDENTITY: "身份",
    BiographyClaimCategory.AGE: "年龄",
    BiographyClaimCategory.APPEARANCE: "外貌",
    BiographyClaimCategory.OCCUPATION: "职业",
    BiographyClaimCategory.RELATIONSHIP: "人物关系",
    BiographyClaimCategory.BACKSTORY: "前史",
    BiographyClaimCategory.BEHAVIOR: "行为模式",
    BiographyClaimCategory.GOAL: "外在目标",
    BiographyClaimCategory.MOTIVATION: "行动动机",
    BiographyClaimCategory.BELIEF: "价值观或信念",
    BiographyClaimCategory.TRAIT: "性格特征",
    BiographyClaimCategory.FEAR: "恐惧",
    BiographyClaimCategory.SECRET: "秘密",
    BiographyClaimCategory.CHANGE: "人物变化",
    BiographyClaimCategory.SPEECH_STYLE: "语言风格",
    BiographyClaimCategory.DRAMATIC_FUNCTION: "戏剧功能",
}
_CONFIDENCE_LABELS = {
    Confidence.HIGH: "高置信",
    Confidence.MEDIUM: "中置信",
    Confidence.LOW: "低置信",
}


def render_biography_markdown(breakdown: NarrativeBreakdown) -> list[str]:
    """渲染核心人物完整小传、关键关系和人物弧。

    Args:
        breakdown: 已通过确定性校验的完整叙事拆解。

    Returns:
        可直接拼接到主报告的 Markdown 行。
    """
    biographies = {item.character_id: item for item in breakdown.biographies.biographies}
    characters = {item.id: item for item in breakdown.entities.characters}
    arcs = {item.character_id: item for item in breakdown.relationships.character_arcs}
    lines = ["## 核心人物完整小传", ""]
    for character in breakdown.entities.characters:
        biography = biographies.get(character.id)
        if biography is None:
            continue
        lines.extend(_render_biography(biography, character, characters, arcs, breakdown))
    return lines


def _render_biography(
    biography: CharacterBiography,
    character: Character,
    characters: dict[str, Character],
    arcs: dict[str, CharacterArc],
    breakdown: NarrativeBreakdown,
) -> list[str]:
    """渲染一个人物的声明、未知项和既有全局人物分析。"""
    lines = [f"### {character.name}", ""]
    lines.extend(_render_claim(biography.summary, summary=True))
    grouped: defaultdict[BiographyClaimBasis, list[CharacterBiographyClaim]] = defaultdict(list)
    for claim in biography.claims:
        grouped[claim.basis].append(claim)
    for basis in BiographyClaimBasis:
        claims = grouped[basis]
        if not claims:
            continue
        lines.extend([f"#### {_BASIS_LABELS[basis]}", ""])
        for claim in claims:
            lines.extend(_render_claim(claim))
    if biography.unknowns:
        unknowns = "、".join(_CATEGORY_LABELS[item] for item in biography.unknowns)
        lines.extend(["#### 剧本未提供", "", f"- {unknowns}", ""])
    lines.extend(_render_relationships(biography, character, characters, breakdown))
    arc = arcs.get(character.id)
    if arc is not None:
        lines.extend(_render_arc(arc))
    if biography.representative_lines:
        lines.extend(["#### 代表性台词", ""])
        for evidence in biography.representative_lines:
            excerpt = _single_line(evidence.excerpt)
            span = evidence.source_span
            lines.extend(
                [
                    f"> {excerpt}",
                    f"> — {evidence.scene_id}，L{span.line_start}–{span.line_end}",
                    "",
                ]
            )
    return lines


def _render_claim(claim: CharacterBiographyClaim, *, summary: bool = False) -> list[str]:
    """渲染一条带依据类型、置信度和第一条证据的声明。"""
    category = _CATEGORY_LABELS[claim.category]
    basis = _BASIS_LABELS[claim.basis]
    confidence = _CONFIDENCE_LABELS[claim.confidence]
    prefix = f"**{category}〔{basis}·{confidence}〕：**" if summary else f"- **{category}：**"
    lines = [f"{prefix} {claim.statement}", ""]
    if claim.attribution:
        lines.extend([f"  - 信息来源：{claim.attribution}", ""])
    if claim.rationale:
        lines.extend([f"  - 推断依据：{claim.rationale}", ""])
    if claim.alternatives:
        lines.extend([f"  - 其他可能：{'；'.join(claim.alternatives)}", ""])
    lines.extend(_render_evidence(claim.evidence))
    return lines


def _render_evidence(evidence_items: list[Evidence]) -> list[str]:
    """紧凑展示第一条证据并提示其余证据数量。"""
    if not evidence_items:
        return []
    evidence = evidence_items[0]
    span = evidence.source_span
    suffix = f"；另有 {len(evidence_items) - 1} 处证据" if len(evidence_items) > 1 else ""
    return [
        f"  - 证据：`{evidence.scene_id}:L{span.line_start}-{span.line_end}` "
        f"「{_single_line(evidence.excerpt)}」{suffix}",
        "",
    ]


def _render_relationships(
    biography: CharacterBiography,
    character: Character,
    characters: dict[str, Character],
    breakdown: NarrativeBreakdown,
) -> list[str]:
    """复用已验证全局关系渲染人物小传中的关键关系。"""
    relation_by_id = {item.id: item for item in breakdown.relationships.relationships}
    lines: list[str] = []
    for relation_id in biography.key_relationship_ids:
        relation = relation_by_id.get(relation_id)
        if relation is None:
            continue
        other_id = (
            relation.target_character_id
            if relation.source_character_id == character.id
            else relation.source_character_id
        )
        other = characters.get(other_id)
        if not lines:
            lines.extend(["#### 关键关系（全局关系分析）", ""])
        lines.append(
            f"- **{other.name if other else other_id}**（{relation.relation_type}）："
            f"{relation.development}"
        )
    if lines:
        lines.append("")
    return lines


def _render_arc(arc: CharacterArc) -> list[str]:
    """复用已有全局人物弧光并展示转折场景。"""
    lines = [
        "#### 人物弧光（全局分析）",
        "",
        f"- 初始状态：{arc.initial_state}",
        f"- 欲望：{arc.desire}",
        f"- 内在需要：{arc.need or '剧本未明确'}",
        f"- 最终状态：{arc.final_state}",
    ]
    for point in arc.turning_points:
        lines.append(f"- 转折（{'、'.join(point.scene_ids)}）：{point.summary}")
    lines.append("")
    return lines


def _single_line(value: str) -> str:
    """把原文摘录压缩为适合 Markdown 行内展示的文本。"""
    return " ".join(value.split())
