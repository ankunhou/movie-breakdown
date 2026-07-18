"""按人物并发、可恢复地生成人物小传。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from movie_breakdown.application.biography_context import (
    BiographyAnalysisContext,
    build_biography_contexts,
)
from movie_breakdown.application.character_dossiers import RuleBasedCharacterDossierStrategy
from movie_breakdown.application.evidence import EvidenceNormalizer
from movie_breakdown.application.ports import NarrativeAnalyzer
from movie_breakdown.domain.base import StageStatus
from movie_breakdown.domain.character_biography import (
    BiographyAnalysisRecord,
    BiographyCatalog,
    CharacterBiography,
)
from movie_breakdown.domain.run import Artifact, ProjectDocument
from movie_breakdown.domain.source import Screenplay
from movie_breakdown.infrastructure.fingerprint import (
    cache_fingerprint,
    content_fingerprint,
    schema_fingerprint,
)
from movie_breakdown.pipeline.artifacts import make_artifact
from movie_breakdown.pipeline.biography_support import (
    BiographyStageResult,
    load_biography_result,
    merge_biography_retry_history,
    normalize_biography_references,
    prepare_cached_records,
    record_is_valid,
)
from movie_breakdown.pipeline.definitions import get_stage
from movie_breakdown.pipeline.dossier_stages import CharacterDossierStageResult
from movie_breakdown.pipeline.model_support import model_failure_metadata, sum_usage
from movie_breakdown.pipeline.narrative_stages import GlobalStageResult, SceneStageResult
from movie_breakdown.pipeline.runtime import PipelineStageError, StageRuntime

_STAGE_NAME = "character_biographies"
_RECORDS_NAME = "character_biographies"
_CATALOG_NAME = "biographies"
_MODEL_CONTRACT_VERSION = "1.0"


class BiographyStageService:
    """执行按人物缓存和恢复的人物小传模型阶段。

    Attributes:
        runtime: 当前项目的阶段运行时。
        analyzer: 提供人物小传分析能力的模型策略。
    """

    def __init__(self, runtime: StageRuntime, analyzer: NarrativeAnalyzer) -> None:
        """创建人物小传阶段服务。

        Args:
            runtime: 当前流水线运行时。
            analyzer: 支持人物小传调用的叙事分析器。
        """
        self.runtime = runtime
        self.analyzer = analyzer

    def analyze(
        self,
        project: ProjectDocument,
        screenplay: Artifact[Screenplay],
        scene_result: SceneStageResult,
        global_result: GlobalStageResult,
        dossiers: CharacterDossierStageResult | None = None,
    ) -> BiographyStageResult:
        """分析人物小传并在中断后仅重试未完成人物。

        Args:
            project: 当前项目描述及模型配置。
            screenplay: 已验证的场景切分产物。
            scene_result: 全部成功的逐场分析结果。
            global_result: 已验证的全局叙事分析结果。
            dossiers: 可选的全人物分级档案；生产流水线应传入已持久化结果。

        Returns:
            完整人物小传目录及其内容指纹。

        Raises:
            PipelineStageError: 上下文重复、人物分析失败或聚合写入失败。
        """
        analyses = [record.analysis for record in scene_result.records if record.analysis]
        dossier_catalog = (
            dossiers.content
            if dossiers is not None
            else RuleBasedCharacterDossierStrategy().build(
                screenplay.data,
                global_result.content,
            )
        )
        contexts = build_biography_contexts(
            screenplay.data,
            analyses,
            global_result.content,
            dossier_catalog,
        )
        self._guard_unique_contexts(contexts)
        expected = {
            context.character.id: self._character_cache_key(context, project)
            for context in contexts
        }
        overall_key = cache_fingerprint(
            screenplay.metadata.artifact_fingerprint,
            scene_result.artifact_fingerprint,
            global_result.artifact_fingerprint,
            content_fingerprint(dossier_catalog),
            get_stage(_STAGE_NAME).version,
            [expected[context.character.id] for context in contexts],
        )
        stage_record = self.runtime.manifest.stages[_STAGE_NAME]
        cached = (
            load_biography_result(self.runtime.store, overall_key)
            if stage_record.status == StageStatus.SUCCESS and stage_record.cache_key == overall_key
            else None
        )
        if cached is not None:
            self.runtime.cached(_STAGE_NAME, overall_key, cached.artifact_fingerprint)
            return cached

        self.runtime.start(
            _STAGE_NAME,
            overall_key,
            f"分析 {len(contexts)} 个人物的小传",
        )
        try:
            records = prepare_cached_records(
                self._load_records(expected),
                expected,
                contexts,
                screenplay.data,
                global_result.content,
            )
            if records:
                ordered_cached = [
                    records[item.character.id] for item in contexts if item.character.id in records
                ]
                self.runtime.store.write_jsonl(_RECORDS_NAME, ordered_cached)
            pending = [
                context
                for context in contexts
                if not record_is_valid(
                    records.get(context.character.id),
                    expected[context.character.id],
                )
            ]
            self._analyze_pending(project, screenplay, contexts, pending, records)
            ordered = [records[context.character.id] for context in contexts]
            failed = [record for record in ordered if record.status != StageStatus.SUCCESS]
            usage = sum_usage(record.usage for record in ordered)
            if failed:
                message = f"{len(failed)} 个人物小传分析失败，可使用 resume 重试。"
                self.runtime.fail(_STAGE_NAME, message, usage)
                raise PipelineStageError(message)
            biographies = [record.biography for record in ordered if record.biography]
            catalog = BiographyCatalog(biographies=biographies)
            artifact = make_artifact(
                stage_name=_STAGE_NAME,
                cache_key=overall_key,
                data=catalog,
                source_fingerprint=screenplay.data.source_fingerprint,
                upstream_fingerprints=[
                    screenplay.metadata.artifact_fingerprint,
                    scene_result.artifact_fingerprint,
                    global_result.artifact_fingerprint,
                    content_fingerprint(dossier_catalog),
                ],
                prompt_fingerprint=self.analyzer.biography_prompt_fingerprint,
                model=project.config.model,
                model_parameters={
                    "thinking_enabled": project.config.thinking_enabled,
                    "reasoning_effort": project.config.reasoning_effort,
                    "max_retries": project.config.max_retries,
                },
                usage=usage,
            )
            self.runtime.store.write_artifact(_CATALOG_NAME, artifact)
            fingerprint = artifact.metadata.artifact_fingerprint
            self.runtime.success(_STAGE_NAME, overall_key, fingerprint, usage)
            return BiographyStageResult(catalog, fingerprint)
        except PipelineStageError:
            raise
        except Exception as error:
            self.runtime.fail(_STAGE_NAME, error)
            raise PipelineStageError(f"人物小传分析失败：{error}") from error

    def _load_records(
        self,
        expected: dict[str, str],
    ) -> dict[str, BiographyAnalysisRecord]:
        """读取仍属于当前人物集合的可恢复记录。"""
        try:
            existing = self.runtime.store.read_jsonl(_RECORDS_NAME, BiographyAnalysisRecord)
        except (OSError, ValueError):
            existing = []
        return {
            record.character_id: record for record in existing if record.character_id in expected
        }

    def _analyze_pending(
        self,
        project: ProjectDocument,
        screenplay: Artifact[Screenplay],
        contexts: list[BiographyAnalysisContext],
        pending: list[BiographyAnalysisContext],
        records: dict[str, BiographyAnalysisRecord],
    ) -> None:
        """并发分析待处理人物并在每次完成后原子保存记录。"""
        completed = 0
        with ThreadPoolExecutor(max_workers=project.config.concurrency) as executor:
            futures = {
                executor.submit(self._analyze_one, context, project, screenplay): context
                for context in pending
            }
            for future in as_completed(futures):
                context = futures[future]
                current = future.result()
                records[context.character.id] = merge_biography_retry_history(
                    current,
                    records.get(context.character.id),
                )
                completed += 1
                ordered_partial = [
                    records[item.character.id] for item in contexts if item.character.id in records
                ]
                self.runtime.store.write_jsonl(_RECORDS_NAME, ordered_partial)
                self.runtime.notify(
                    _STAGE_NAME,
                    f"已完成 {completed}/{len(pending)}：{context.character.id}",
                )

    def _character_cache_key(
        self,
        context: BiographyAnalysisContext,
        project: ProjectDocument,
    ) -> str:
        """计算单个人物上下文、Prompt、Schema 和模型配置的缓存键。"""
        config = project.config
        return cache_fingerprint(
            content_fingerprint(context),
            _MODEL_CONTRACT_VERSION,
            self.analyzer.biography_prompt_fingerprint,
            schema_fingerprint(CharacterBiography),
            config.model,
            config.thinking_enabled,
            config.reasoning_effort,
        )

    def _analyze_one(
        self,
        context: BiographyAnalysisContext,
        project: ProjectDocument,
        screenplay: Artifact[Screenplay],
    ) -> BiographyAnalysisRecord:
        """把单个人物模型调用统一转换为可恢复记录。"""
        cache_key = self._character_cache_key(context, project)
        call = None
        try:
            call = self.analyzer.analyze_biography(context, project.config)
            biography = EvidenceNormalizer(screenplay.data.scenes).normalize(call.content)
            if biography.character_id != context.character.id:
                raise ValueError("人物小传 ID 与输入人物不一致。")
            biography = normalize_biography_references(biography, context)
            return BiographyAnalysisRecord(
                character_id=context.character.id,
                cache_key=cache_key,
                status=StageStatus.SUCCESS,
                biography=biography,
                attempts=call.attempts,
                usage=call.usage,
            )
        except Exception as error:
            attempts, usage = model_failure_metadata(error, call, project.config.max_retries + 1)
            return BiographyAnalysisRecord(
                character_id=context.character.id,
                cache_key=cache_key,
                status=StageStatus.FAILED,
                error=str(error)[:4000],
                attempts=attempts,
                usage=usage,
            )

    @staticmethod
    def _guard_unique_contexts(contexts: list[BiographyAnalysisContext]) -> None:
        """拒绝上下文中重复的人物 ID，避免记录互相覆盖。"""
        ids = [context.character.id for context in contexts]
        if len(ids) != len(set(ids)):
            raise PipelineStageError("人物小传上下文包含重复人物 ID。")
