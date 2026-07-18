"""对模型生成的悬空制片交叉引用执行保守可审计恢复。"""

from __future__ import annotations

from movie_breakdown.domain.production_scene import (
    ProductionUncertainty,
    SceneProductionAnalysis,
)
from movie_breakdown.domain.source import Scene


def normalize_production_identity(
    scene: Scene,
    analysis: SceneProductionAnalysis,
) -> SceneProductionAnalysis:
    """用本次单场调用的确定输入回填场景 ID 和原始标题。

    `scene_id` 与 `raw_heading` 是调用身份而非模型判断；模型常会把正文标签
    “场景：”或场次编号一并复制，直接使用输入值可避免无意义重试。

    Args:
        scene: 本次模型调用唯一对应的共享场景。
        analysis: 已通过 Pydantic 校验的模型结果。

    Returns:
        身份字段与输入场景严格一致的新结果；已经一致时返回原对象。
    """
    if analysis.scene_id == scene.id and analysis.setting.raw_heading == scene.heading:
        return analysis
    setting = analysis.setting.model_copy(update={"raw_heading": scene.heading})
    return analysis.model_copy(update={"scene_id": scene.id, "setting": setting})


def normalize_production_references(
    analysis: SceneProductionAnalysis,
) -> SceneProductionAnalysis:
    """删除无法指向本场真实需求的可选关联并披露人工确认项。

    元素本身、证据和复杂度因素不会被删除；只清理本可为空的关联 ID，避免把
    群演 ID 等错误值强行解释为演员。所有清理都合并写入 `uncertainties`。

    Args:
        analysis: 已完成证据规范化的单场制片结果。

    Returns:
        不含悬空可选引用、并附恢复说明的新结果。
    """
    cast_ids = {item.id for item in analysis.cast}
    known_ids = {
        *(item.id for item in analysis.cast),
        *(item.id for item in analysis.background),
        *(item.id for item in analysis.elements),
    }
    dropped: list[str] = []
    elements = []
    for element in analysis.elements:
        invalid = [value for value in element.associated_cast_ids if value not in cast_ids]
        if invalid:
            dropped.append(f"元素 {element.id} 的演员关联：{'、'.join(invalid)}")
        elements.append(
            element.model_copy(
                update={
                    "associated_cast_ids": [
                        value for value in element.associated_cast_ids if value in cast_ids
                    ]
                }
            )
        )
    factors = []
    for factor in analysis.complexity.factors:
        invalid = [value for value in factor.related_requirement_ids if value not in known_ids]
        if invalid:
            dropped.append(f"复杂度 {factor.dimension.value} 的需求关联：{'、'.join(invalid)}")
        factors.append(
            factor.model_copy(
                update={
                    "related_requirement_ids": [
                        value for value in factor.related_requirement_ids if value in known_ids
                    ]
                }
            )
        )
    if not dropped:
        return analysis
    uncertainty = ProductionUncertainty(
        subject="结构化引用待人工确认",
        description="模型返回的未知交叉引用已被保守移除：" + "；".join(dropped),
        impact="对应制片需求仍保留，但其实际关联演员或需求需由制片人员核对。",
    )
    complexity = analysis.complexity.model_copy(update={"factors": factors})
    return analysis.model_copy(
        update={
            "elements": elements,
            "complexity": complexity,
            "uncertainties": [*analysis.uncertainties, uncertainty],
        }
    )
