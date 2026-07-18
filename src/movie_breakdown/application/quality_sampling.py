"""从叙事结论中构建并稳定抽取高风险人工评测目标。"""

from __future__ import annotations

from movie_breakdown.application.quality_targets import collect_review_candidates
from movie_breakdown.domain.export import NarrativeBreakdown
from movie_breakdown.domain.quality import AutomaticSignal, ReviewTarget
from movie_breakdown.infrastructure.fingerprint import hash_text


def sample_review_targets(
    breakdown: NarrativeBreakdown,
    signals: list[AutomaticSignal],
    sample_size: int,
) -> list[ReviewTarget]:
    """按锚点覆盖和风险分数稳定抽取人工评测目标。

    Args:
        breakdown: 已通过确定性一致性校验的完整叙事拆解。
        signals: 当前拆解的自动代理信号。
        sample_size: 期望抽取的目标数，范围为 6 到 50。

    Returns:
        同一分析内容下顺序和内容稳定、无重复的评测目标。

    Raises:
        ValueError: 抽样数量不在允许范围内。
    """
    if not 6 <= sample_size <= 50:
        raise ValueError("人工抽检数量必须在 6 到 50 之间。")
    candidates = collect_review_candidates(breakdown, signals)
    if len(candidates) <= sample_size:
        return candidates
    by_id = {target.id: target for target in candidates}
    selected: list[ReviewTarget] = []
    anchor_limit = min(len(_anchor_ids(breakdown)), max(6, sample_size // 2))
    for target_id in _anchor_ids(breakdown)[:anchor_limit]:
        target = by_id.get(target_id)
        if target is not None and all(item.id != target.id for item in selected):
            selected.append(target.model_copy(update={"selection_reason": "anchor"}))
    remaining = [target for target in candidates if all(item.id != target.id for item in selected)]
    remaining.sort(
        key=lambda target: (
            -target.risk_score,
            hash_text(f"{breakdown.screenplay.source_fingerprint}:{target.id}"),
        )
    )
    selected.extend(remaining[: sample_size - len(selected)])
    return selected


def _anchor_ids(breakdown: NarrativeBreakdown) -> list[str]:
    """按首中尾、幕转折、主要人物小传和其他解释构建锚点。"""
    analyses = breakdown.scene_analyses
    scene_ids = []
    if analyses:
        scene_ids = [
            analyses[0].scene_id,
            analyses[len(analyses) // 2].scene_id,
            analyses[-1].scene_id,
        ]
    arcs = sorted(
        breakdown.relationships.character_arcs,
        key=lambda item: (-len(item.turning_points), item.character_id),
    )
    characters = {item.id: item for item in breakdown.entities.characters}
    biographies = sorted(
        breakdown.biographies.biographies,
        key=lambda item: (
            -len(characters[item.character_id].scene_ids) if item.character_id in characters else 0,
            item.character_id,
        ),
    )
    return list(
        dict.fromkeys(
            [
                *(f"scene-summary:{scene_id}" for scene_id in scene_ids),
                *(f"act:{item.act}" for item in breakdown.structure.acts),
                *(f"biography:{item.character_id}" for item in biographies[:2]),
                *(
                    f"theme:{index}"
                    for index in range(1, min(2, len(breakdown.structure.themes)) + 1)
                ),
                *(f"arc:{item.character_id}" for item in arcs[:2]),
            ]
        )
    )
