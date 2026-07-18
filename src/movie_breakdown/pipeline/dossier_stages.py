"""生成、缓存并严格读取全人物分级档案。"""

from __future__ import annotations

from dataclasses import dataclass

from movie_breakdown.application.character_dossiers import (
    CharacterDossierStrategy,
    RuleBasedCharacterDossierStrategy,
)
from movie_breakdown.domain.character_dossier import CharacterDossierCatalog
from movie_breakdown.domain.run import Artifact
from movie_breakdown.domain.source import Screenplay
from movie_breakdown.infrastructure.fingerprint import (
    cache_fingerprint,
    content_fingerprint,
    schema_fingerprint,
)
from movie_breakdown.infrastructure.storage import ProjectStore
from movie_breakdown.pipeline.artifacts import make_artifact
from movie_breakdown.pipeline.definitions import get_stage
from movie_breakdown.pipeline.narrative_stages import GlobalStageResult
from movie_breakdown.pipeline.runtime import PipelineStageError, StageRuntime

STAGE_NAME = "character_dossiers"
ARTIFACT_NAME = "character_dossiers"


@dataclass(frozen=True, slots=True)
class CharacterDossierStageResult:
    """全人物档案阶段返回给下游的稳定结果。

    Attributes:
        content: 全部已归一人物的分级档案目录。
        artifact_fingerprint: 档案目录的内容指纹。
    """

    content: CharacterDossierCatalog
    artifact_fingerprint: str


class CharacterDossierStageService:
    """执行无需模型的全人物档案生成与内容指纹缓存。

    Attributes:
        runtime: 当前项目的阶段运行时。
        strategy: 可替换的人物分级与档案构建策略。
    """

    def __init__(
        self,
        runtime: StageRuntime,
        strategy: CharacterDossierStrategy | None = None,
    ) -> None:
        """创建人物档案阶段服务。

        Args:
            runtime: 当前项目的阶段运行时。
            strategy: 可选的全人物档案构建策略。
        """
        self.runtime = runtime
        self.strategy = strategy or RuleBasedCharacterDossierStrategy()

    def build(
        self,
        screenplay: Artifact[Screenplay],
        global_result: GlobalStageResult,
    ) -> CharacterDossierStageResult:
        """生成或复用与当前全局产物匹配的全人物档案。

        Args:
            screenplay: 已验证的场景切分产物。
            global_result: 已验证的全局实体、事件、关系与弧光结果。

        Returns:
            全人物档案目录及其内容指纹。

        Raises:
            PipelineStageError: 档案构建或持久化失败。
        """
        try:
            catalog = self.strategy.build(screenplay.data, global_result.content)
            fingerprint = content_fingerprint(catalog)
            cache_key = cache_fingerprint(
                screenplay.metadata.artifact_fingerprint,
                global_result.artifact_fingerprint,
                fingerprint,
                get_stage(STAGE_NAME).version,
                schema_fingerprint(CharacterDossierCatalog),
            )
            cached = self.runtime.load_cached(
                STAGE_NAME,
                ARTIFACT_NAME,
                CharacterDossierCatalog,
                cache_key,
            )
            if cached is not None:
                return CharacterDossierStageResult(
                    cached.data,
                    cached.metadata.artifact_fingerprint,
                )
            self.runtime.start(
                STAGE_NAME,
                cache_key,
                f"为 {len(catalog.dossiers)} 个已归一人物生成分级档案",
            )
            artifact = make_artifact(
                stage_name=STAGE_NAME,
                cache_key=cache_key,
                data=catalog,
                source_fingerprint=screenplay.data.source_fingerprint,
                upstream_fingerprints=[
                    screenplay.metadata.artifact_fingerprint,
                    global_result.artifact_fingerprint,
                ],
            )
            self.runtime.store.write_artifact(ARTIFACT_NAME, artifact)
            self.runtime.success(STAGE_NAME, cache_key, fingerprint)
            return CharacterDossierStageResult(catalog, fingerprint)
        except PipelineStageError:
            raise
        except Exception as error:
            self.runtime.fail(STAGE_NAME, error)
            raise PipelineStageError(f"人物分级档案生成失败：{error}") from error


def load_dossier_result(
    store: ProjectStore,
    upstream_fingerprints: list[str] | None = None,
) -> CharacterDossierStageResult | None:
    """严格读取已持久化的全人物档案产物。

    Args:
        store: 当前项目存储。
        upstream_fingerprints: 可选的预期场景与全局产物指纹。

    Returns:
        元数据、指纹和上游均有效的档案目录；否则返回空。
    """
    try:
        artifact = store.read_artifact(ARTIFACT_NAME, CharacterDossierCatalog)
    except (OSError, ValueError):
        return None
    metadata = artifact.metadata
    fingerprint = content_fingerprint(artifact.data)
    if (
        metadata.stage != STAGE_NAME
        or metadata.stage_version != get_stage(STAGE_NAME).version
        or metadata.schema_fingerprint != schema_fingerprint(CharacterDossierCatalog)
        or metadata.artifact_fingerprint != fingerprint
        or (
            upstream_fingerprints is not None
            and metadata.upstream_fingerprints != upstream_fingerprints
        )
    ):
        return None
    return CharacterDossierStageResult(artifact.data, fingerprint)
