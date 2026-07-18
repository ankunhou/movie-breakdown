"""阻断制片发布的危险默认实现检查。"""

from __future__ import annotations

from movie_breakdown.domain.production_scene import SceneProductionAnalysis


def find_unsafe_defaults(
    analyses: list[SceneProductionAnalysis],
) -> list[tuple[str, str]]:
    """查找任何发布级别都不能接受的危险默认实现。

    Args:
        analyses: 当前逐场制片结果。

    Returns:
        场景 ID 与中文阻断原因组成的稳定列表。
    """
    findings: list[tuple[str, str]] = []
    for analysis in analyses:
        text = analysis.model_dump_json()
        if "实弹" in text:
            findings.append((analysis.scene_id, "出现“实弹”默认实现；片场必须禁止实弹。"))
        animal_default = "活体" in text and any(term in text for term in ("血包", "倒地", "中弹"))
        if animal_default:
            findings.append(
                (
                    analysis.scene_id,
                    "把危险活体动物动作作为默认方案，必须改为无接触分板与替代方案。",
                )
            )
    return findings
