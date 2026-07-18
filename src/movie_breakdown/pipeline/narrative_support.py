"""模型分析阶段共享的纯函数辅助能力。"""

from __future__ import annotations

from pydantic import BaseModel

from movie_breakdown.application.evidence import EvidenceNormalizer
from movie_breakdown.application.structure_normalization import fill_unassigned_act_scenes
from movie_breakdown.domain.base import StageStatus
from movie_breakdown.domain.global_analysis import (
    EntityCatalog,
    EventCatalog,
    GlobalAnalysisResult,
    RelationshipCatalog,
    StructureAnalysis,
)
from movie_breakdown.domain.run import ProjectDocument
from movie_breakdown.domain.scene_analysis import SceneAnalysis, SceneAnalysisRecord
from movie_breakdown.domain.source import Scene
from movie_breakdown.infrastructure.fingerprint import content_fingerprint
from movie_breakdown.infrastructure.storage import ProjectStore
from movie_breakdown.pipeline.model_support import sum_usage


def record_is_valid(record: SceneAnalysisRecord | None, cache_key: str) -> bool:
    """判断逐场记录是否成功且匹配当前完整缓存键。

    Args:
        record: 已持久化的可选逐场记录。
        cache_key: 当前场景预期的完整缓存键。

    Returns:
        该记录是否可安全复用。
    """
    return bool(
        record
        and record.status == StageStatus.SUCCESS
        and record.analysis is not None
        and record.cache_key == cache_key
    )


def merge_scene_retry_history(
    current: SceneAnalysisRecord,
    previous: SceneAnalysisRecord | None,
) -> SceneAnalysisRecord:
    """把同一缓存键的前次失败成本累计到最新逐场记录。

    Args:
        current: 本次场景分析产生的成功或失败记录。
        previous: JSONL 中同一场景的上一条记录。

    Returns:
        累计实际尝试次数与 token 的新记录；输入已变化时返回本次记录。
    """
    if (
        previous is None
        or previous.status != StageStatus.FAILED
        or previous.cache_key != current.cache_key
    ):
        return current
    return current.model_copy(
        update={
            "attempts": previous.attempts + current.attempts,
            "usage": sum_usage((previous.usage, current.usage)),
        }
    )


def validate_scene_analysis(scene: Scene, analysis: SceneAnalysis) -> None:
    """检查逐场结果的场景 ID 及全部证据范围。

    Args:
        scene: 当前模型调用对应的原始场景。
        analysis: 已完成证据归一化的逐场分析。

    Raises:
        ValueError: 结果引用其他场景或证据行号越过当前场景。
    """
    if analysis.scene_id != scene.id:
        raise ValueError("模型返回的场景 ID 与输入不一致。")
    evidence_items = [*analysis.evidence]
    for event in analysis.events:
        evidence_items.extend(event.evidence)
    for evidence in evidence_items:
        if evidence.scene_id != scene.id:
            raise ValueError("逐场证据引用了其他场景。")
        span = evidence.source_span
        if (
            span.line_start < scene.source_span.line_start
            or span.line_end > scene.source_span.line_end
        ):
            raise ValueError("逐场证据行号超出当前场景范围。")


def global_parts(result: GlobalAnalysisResult) -> dict[str, BaseModel]:
    """返回全局聚合到独立持久化文件的稳定映射。

    Args:
        result: 完整全局叙事分析结果。

    Returns:
        产物文件名到对应 Pydantic 数据的映射。
    """
    return {
        "entities": result.entities,
        "events": result.events,
        "relationships": result.relationships,
        "structure": result.structure,
    }


def normalize_global_analysis(
    result: GlobalAnalysisResult,
    scenes: list[Scene],
    *,
    drop_unlocatable: bool = False,
) -> GlobalAnalysisResult:
    """规范化全局证据并补齐可确定的幕内场景遗漏。

    Args:
        result: 模型新结果或已持久化的全局分析。
        scenes: 提供证据原文和完整场景顺序的剧本场景。
        drop_unlocatable: 是否允许旧缓存迁移删除无法定位的证据。

    Returns:
        移除无法定位证据、修复有效证据范围并补齐明确幕内缺口的结果。
    """
    normalized = EvidenceNormalizer(
        scenes,
        drop_unlocatable=drop_unlocatable,
    ).normalize(result)
    structure = fill_unassigned_act_scenes(normalized.structure, scenes)
    return normalized.model_copy(update={"structure": structure})


def model_parameters(project: ProjectDocument) -> dict[str, str | int | bool]:
    """返回不包含密钥的全局分析模型参数。

    Args:
        project: 当前项目描述及模型配置。

    Returns:
        可安全写入产物元数据的模型参数。
    """
    config = project.config
    return {
        "framework": config.structure_framework,
        "thinking_enabled": config.thinking_enabled,
        "reasoning_effort": config.reasoning_effort,
        "max_retries": config.max_retries,
    }


def load_global_result(
    store: ProjectStore,
    cache_key: str | None = None,
) -> tuple[GlobalAnalysisResult, str] | None:
    """从四个严格类型产物重建全局分析结果。

    Args:
        store: 当前项目存储。
        cache_key: 可选的预期缓存键；提供时要求全部产物匹配。

    Returns:
        全局分析及其内容指纹；任一产物不可用时返回空。
    """
    try:
        entities = store.read_artifact("entities", EntityCatalog)
        events = store.read_artifact("events", EventCatalog)
        relationships = store.read_artifact("relationships", RelationshipCatalog)
        structure = store.read_artifact("structure", StructureAnalysis)
    except (OSError, ValueError):
        return None
    artifacts = [entities, events, relationships, structure]
    artifact_cache_keys = {item.metadata.cache_key for item in artifacts}
    if len(artifact_cache_keys) != 1:
        return None
    if cache_key and any(item.metadata.cache_key != cache_key for item in artifacts):
        return None
    result = GlobalAnalysisResult(
        entities=entities.data,
        events=events.data,
        relationships=relationships.data,
        structure=structure.data,
    )
    return result, content_fingerprint(result)


def normalize_cached_global(
    store: ProjectStore,
    result: GlobalAnalysisResult,
    scenes: list[Scene],
) -> tuple[GlobalAnalysisResult, str]:
    """迁移旧全局缓存中的证据定位并保留模型调用元数据。

    Args:
        store: 当前项目存储。
        result: 从四个缓存产物重建的全局分析。
        scenes: 用于逐字反查证据的完整场景集合。

    Returns:
        规范化后的全局结果及其新内容指纹。
    """
    normalized = normalize_global_analysis(result, scenes, drop_unlocatable=True)
    fingerprint = content_fingerprint(normalized)
    if fingerprint == content_fingerprint(result):
        return result, fingerprint
    for name, data in global_parts(normalized).items():
        current = store.read_artifact(name, type(data))
        metadata = current.metadata.model_copy(
            update={"artifact_fingerprint": content_fingerprint(data)}
        )
        store.write_artifact(
            name,
            current.model_copy(update={"metadata": metadata, "data": data}),
        )
    return normalized, fingerprint
