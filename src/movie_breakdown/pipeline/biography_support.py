"""人物小传阶段共享的缓存与产物读取辅助函数。"""

from __future__ import annotations

from dataclasses import dataclass

from movie_breakdown.application.biography_context import BiographyAnalysisContext
from movie_breakdown.application.biography_validation import BiographyValidationService
from movie_breakdown.application.evidence import EvidenceNormalizer
from movie_breakdown.domain.base import Severity, StageStatus
from movie_breakdown.domain.character_biography import (
    BiographyAnalysisRecord,
    BiographyCatalog,
    CharacterBiography,
)
from movie_breakdown.domain.global_analysis import GlobalAnalysisResult
from movie_breakdown.domain.scene_analysis import TokenUsage
from movie_breakdown.domain.source import Screenplay
from movie_breakdown.infrastructure.fingerprint import content_fingerprint, schema_fingerprint
from movie_breakdown.infrastructure.storage import ProjectStore
from movie_breakdown.pipeline.definitions import get_stage

STAGE_NAME = "character_biographies"
CATALOG_NAME = "biographies"


@dataclass(frozen=True, slots=True)
class BiographyStageResult:
    """人物小传阶段返回给下游的稳定结果。

    Attributes:
        content: 按全局人物顺序排列的人物小传目录。
        artifact_fingerprint: 人物小传目录的内容指纹。
    """

    content: BiographyCatalog
    artifact_fingerprint: str


def load_biography_result(
    store: ProjectStore,
    cache_key: str | None = None,
    upstream_fingerprints: list[str] | None = None,
) -> BiographyStageResult | None:
    """读取并验证聚合人物小传产物。

    Args:
        store: 当前项目存储。
        cache_key: 可选的预期完整缓存键。
        upstream_fingerprints: 可选的预期场景、逐场和全局产物指纹。

    Returns:
        有效人物小传目录；产物缺失、损坏或过期时返回空。
    """
    try:
        artifact = store.read_artifact(CATALOG_NAME, BiographyCatalog)
    except (OSError, ValueError):
        return None
    metadata = artifact.metadata
    fingerprint = content_fingerprint(artifact.data)
    if (
        metadata.stage != STAGE_NAME
        or metadata.stage_version != get_stage(STAGE_NAME).version
        or metadata.schema_fingerprint != schema_fingerprint(BiographyCatalog)
        or metadata.artifact_fingerprint != fingerprint
        or (cache_key is not None and metadata.cache_key != cache_key)
        or (
            upstream_fingerprints is not None
            and metadata.upstream_fingerprints != upstream_fingerprints
        )
    ):
        return None
    return BiographyStageResult(artifact.data, fingerprint)


def record_is_valid(record: BiographyAnalysisRecord | None, cache_key: str) -> bool:
    """判断人物小传记录是否成功且匹配当前完整缓存键。

    Args:
        record: 已持久化的可选人物小传记录。
        cache_key: 当前人物预期的完整缓存键。

    Returns:
        该记录是否可以安全复用。
    """
    return bool(
        record
        and record.status == StageStatus.SUCCESS
        and record.biography is not None
        and record.cache_key == cache_key
    )


def normalize_biography_references(
    biography: CharacterBiography,
    context: BiographyAnalysisContext,
) -> CharacterBiography:
    """把模型常见的人物 ID 关系引用转换为真实关系 ID。

    模型有时会把 `key_relationship_ids` 误解为“关键关系人物 ID”。此处只在当前
    人物确实存在对应全局关系时进行确定性映射，无法映射的值直接舍弃。

    Args:
        biography: 已通过人物小传 Schema 校验的模型结果。
        context: 含当前人物全部已验证关系和原文场景的输入上下文。

    Returns:
        只保留有效关系 ID、有效代表台词和确定性上下文场景的人物小传。
    """
    relation_ids = {item.id for item in context.relationships}
    relations_by_character: dict[str, list[str]] = {}
    for relation in context.relationships:
        other_id = (
            relation.target_character_id
            if relation.source_character_id == context.character.id
            else relation.source_character_id
        )
        relations_by_character.setdefault(other_id, []).append(relation.id)
    normalized_relations: list[str] = []
    for value in biography.key_relationship_ids:
        candidates = [value] if value in relation_ids else relations_by_character.get(value, [])
        for relation_id in candidates:
            if relation_id not in normalized_relations:
                normalized_relations.append(relation_id)
    context_scene_ids = [scene.id for scene in context.source_scenes]
    context_set = set(context_scene_ids)
    representative_lines = [
        item for item in biography.representative_lines if item.scene_id in context_set
    ]
    claimed_categories = {item.category for item in biography.claims}
    unknowns = [item for item in biography.unknowns if item not in claimed_categories]
    return biography.model_copy(
        update={
            "context_scene_ids": context_scene_ids,
            "key_relationship_ids": normalized_relations[:6],
            "representative_lines": representative_lines,
            "unknowns": unknowns,
        }
    )


def prepare_cached_records(
    records: dict[str, BiographyAnalysisRecord],
    expected: dict[str, str],
    contexts: list[BiographyAnalysisContext],
    screenplay: Screenplay,
    global_result: GlobalAnalysisResult,
) -> dict[str, BiographyAnalysisRecord]:
    """本地迁移缓存并剔除仍有确定性错误的人物记录。

    Args:
        records: 从人物级 JSONL 读取的当前候选记录。
        expected: 当前人物到完整缓存键的映射。
        contexts: 当前输入对应的全部人物小传上下文。
        screenplay: 提供场景原文与稳定顺序的剧本。
        global_result: 当前实体、关系和人物弧光全局结果。

    Returns:
        已完成本地引用迁移且可安全复用的记录映射；有错误的人物记录被移除，
        由上层正常进入待分析队列。
    """
    context_by_id = {item.character.id: item for item in contexts}
    prepared: dict[str, BiographyAnalysisRecord] = {}
    for character_id, record in records.items():
        context = context_by_id.get(character_id)
        if context is None:
            continue
        if record.status == StageStatus.FAILED:
            if record.cache_key == expected.get(character_id):
                prepared[character_id] = record
            continue
        if (
            record.biography is None
            or record.character_id != record.biography.character_id
            or record.biography.character_id != context.character.id
        ):
            continue
        try:
            biography = EvidenceNormalizer(
                screenplay.scenes,
                drop_unlocatable=True,
            ).normalize(record.biography)
            biography = normalize_biography_references(biography, context)
        except (TypeError, ValueError):
            continue
        prepared[character_id] = record.model_copy(update={"biography": biography})
    catalog = BiographyCatalog(
        biographies=[
            prepared[item.character.id].biography
            for item in contexts
            if item.character.id in prepared and prepared[item.character.id].biography is not None
        ]
    )
    issues = []
    BiographyValidationService().validate(catalog, screenplay, global_result, issues)
    invalid_ids: set[str] = set()
    for issue in issues:
        if issue.severity != Severity.ERROR:
            continue
        reference = issue.reference or ""
        if reference.startswith("biography:"):
            invalid_ids.add(reference.split(":", 2)[1])
        elif issue.code == "biography.coverage":
            continue
        else:
            return {}
    return {key: value for key, value in prepared.items() if key not in invalid_ids}


def merge_biography_retry_history(
    current: BiographyAnalysisRecord,
    previous: BiographyAnalysisRecord | None,
) -> BiographyAnalysisRecord:
    """把同一缓存键的前次失败成本累计到最新人物记录。

    Args:
        current: 本次人物分析产生的成功或失败记录。
        previous: JSONL 中同一人物的上一条记录。

    Returns:
        累计实际尝试次数与 token 的新记录；不属于同一输入时返回本次记录。
    """
    if (
        previous is None
        or previous.status != StageStatus.FAILED
        or previous.cache_key != current.cache_key
    ):
        return current
    usage = TokenUsage(
        input_tokens=previous.usage.input_tokens + current.usage.input_tokens,
        output_tokens=previous.usage.output_tokens + current.usage.output_tokens,
        total_tokens=previous.usage.total_tokens + current.usage.total_tokens,
    )
    return current.model_copy(
        update={"attempts": previous.attempts + current.attempts, "usage": usage}
    )
