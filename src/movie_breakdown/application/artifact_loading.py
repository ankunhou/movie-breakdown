"""只读命令所需的现有严格分析产物装载。"""

from __future__ import annotations

from dataclasses import dataclass

from movie_breakdown.application.validation import ValidationService
from movie_breakdown.domain.export import NarrativeBreakdown
from movie_breakdown.domain.run import Artifact
from movie_breakdown.domain.scene_analysis import SceneAnalysisRecord
from movie_breakdown.domain.source import Screenplay
from movie_breakdown.infrastructure.fingerprint import content_fingerprint
from movie_breakdown.infrastructure.storage import ProjectStore
from movie_breakdown.pipeline.biography_support import (
    BiographyStageResult,
    load_biography_result,
)
from movie_breakdown.pipeline.dossier_stages import (
    CharacterDossierStageResult,
    load_dossier_result,
)
from movie_breakdown.pipeline.narrative_stages import GlobalStageResult, SceneStageResult
from movie_breakdown.pipeline.narrative_support import load_global_result
from movie_breakdown.pipeline.runtime import PipelineStageError


@dataclass(frozen=True, slots=True)
class ExistingAnalysisArtifacts:
    """校验、导出和质量评测使用的现有阶段产物。

    Attributes:
        screenplay: 已验证的场景切分产物。
        scenes: 按剧本顺序排列的逐场分析记录。
        global_result: 实体、事件、关系和结构的全局分析。
        dossiers: 覆盖全部已归一人物的分级档案目录。
        biographies: 与当前上游指纹匹配的人物小传目录。
    """

    screenplay: Artifact[Screenplay]
    scenes: SceneStageResult
    global_result: GlobalStageResult
    dossiers: CharacterDossierStageResult
    biographies: BiographyStageResult


def load_existing_artifacts(store: ProjectStore) -> ExistingAnalysisArtifacts:
    """严格读取并交叉核对所有模型分析阶段产物。

    Args:
        store: 当前剧本拆解项目存储。

    Returns:
        可供本地校验、导出和质量评测使用的完整上游产物。

    Raises:
        PipelineStageError: 任一必要产物缺失、损坏或与上游指纹不匹配。
    """
    try:
        screenplay = store.read_artifact("scenes", Screenplay)
        records = store.read_jsonl("scene_analysis", SceneAnalysisRecord)
    except (OSError, ValueError) as error:
        raise PipelineStageError(f"项目缺少有效的逐场分析产物：{error}") from error
    loaded = load_global_result(store)
    if loaded is None:
        raise PipelineStageError("项目缺少有效的全局叙事分析产物。")
    global_content, global_fingerprint = loaded
    scene_fingerprint = content_fingerprint(records)
    dossiers = load_dossier_result(
        store,
        upstream_fingerprints=[
            screenplay.metadata.artifact_fingerprint,
            global_fingerprint,
        ],
    )
    if dossiers is None:
        raise PipelineStageError("项目缺少有效的全人物分级档案，请先执行 resume。")
    biographies = load_biography_result(
        store,
        upstream_fingerprints=[
            screenplay.metadata.artifact_fingerprint,
            scene_fingerprint,
            global_fingerprint,
            dossiers.artifact_fingerprint,
        ],
    )
    if biographies is None:
        raise PipelineStageError("项目缺少有效的人物小传产物，请先执行 resume。")
    return ExistingAnalysisArtifacts(
        screenplay=screenplay,
        scenes=SceneStageResult(records, scene_fingerprint),
        global_result=GlobalStageResult(global_content, global_fingerprint),
        dossiers=dossiers,
        biographies=biographies,
    )


def load_validated_base_breakdown(store: ProjectStore) -> NarrativeBreakdown:
    """只读加载、内存校验并聚合未应用人工修正的分析。

    Args:
        store: 当前剧本拆解项目存储。

    Returns:
        通过当前确定性规则校验的基础叙事聚合。

    Raises:
        PipelineStageError: 必要产物缺失、损坏或一致性校验失败。
    """
    existing = load_existing_artifacts(store)
    validation = ValidationService().validate(
        existing.screenplay.data,
        existing.scenes.records,
        existing.global_result.content,
        existing.biographies.content,
        existing.dossiers.content,
    )
    if not validation.valid:
        raise PipelineStageError("一致性校验未通过，不能执行人工修正预览。")
    content = existing.global_result.content
    return NarrativeBreakdown(
        screenplay=existing.screenplay.data,
        scene_analyses=[record.analysis for record in existing.scenes.records if record.analysis],
        entities=content.entities,
        events=content.events,
        relationships=content.relationships,
        dossiers=existing.dossiers.content,
        biographies=existing.biographies.content,
        structure=content.structure,
        validation=validation,
    )
