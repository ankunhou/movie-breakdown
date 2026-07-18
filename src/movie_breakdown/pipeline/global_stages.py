"""可恢复且保留真实调用成本的全局叙事分析阶段。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from movie_breakdown.application.ports import NarrativeAnalyzer
from movie_breakdown.application.validation import ValidationService
from movie_breakdown.domain.base import StageStatus
from movie_breakdown.domain.global_analysis import GlobalAnalysisResult
from movie_breakdown.domain.run import Artifact, ProjectDocument
from movie_breakdown.domain.scene_analysis import TokenUsage
from movie_breakdown.domain.source import Screenplay
from movie_breakdown.infrastructure.fingerprint import cache_fingerprint, schema_fingerprint
from movie_breakdown.pipeline.artifacts import make_artifact
from movie_breakdown.pipeline.definitions import get_stage
from movie_breakdown.pipeline.global_recovery import normalize_global_with_recovery
from movie_breakdown.pipeline.model_support import model_failure_metadata, sum_usage
from movie_breakdown.pipeline.narrative_support import (
    global_parts,
    load_global_result,
    model_parameters,
    normalize_cached_global,
)
from movie_breakdown.pipeline.runtime import PipelineStageError, StageRuntime

if TYPE_CHECKING:
    from movie_breakdown.pipeline.narrative_stages import SceneStageResult


@dataclass(frozen=True, slots=True)
class GlobalStageResult:
    """全局分析结果及聚合内容指纹。

    Attributes:
        content: 实体、事件、关系和结构的完整结果。
        artifact_fingerprint: 完整全局结果的内容指纹。
    """

    content: GlobalAnalysisResult
    artifact_fingerprint: str


class GlobalNarrativeStageService:
    """执行全局分析缓存、恢复、持久化和成本追踪。

    Attributes:
        runtime: 当前项目的阶段运行时。
        analyzer: 符合应用端口的叙事分析策略。
    """

    def __init__(self, runtime: StageRuntime, analyzer: NarrativeAnalyzer) -> None:
        """创建全局叙事分析阶段服务。

        Args:
            runtime: 当前流水线运行时。
            analyzer: 全局叙事分析实现。
        """
        self.runtime = runtime
        self.analyzer = analyzer

    def analyze(
        self,
        project: ProjectDocument,
        screenplay: Artifact[Screenplay],
        scene_result: SceneStageResult,
    ) -> GlobalStageResult:
        """汇总逐场结果并生成全局实体、关系与三幕结构。

        Args:
            project: 当前项目描述及模型配置。
            screenplay: 已验证的场景切分产物。
            scene_result: 全部成功的逐场分析结果。

        Returns:
            全局分析聚合和内容指纹。

        Raises:
            PipelineStageError: 全局结构化模型调用或结果恢复失败。
        """
        config = project.config
        cache_key = cache_fingerprint(
            screenplay.metadata.artifact_fingerprint,
            scene_result.artifact_fingerprint,
            get_stage("global_analysis").version,
            self.analyzer.global_prompt_fingerprint,
            schema_fingerprint(GlobalAnalysisResult),
            config.model,
            config.structure_framework,
            config.thinking_enabled,
            config.reasoning_effort,
        )
        record = self.runtime.manifest.stages["global_analysis"]
        cache_reusable = record.status in {StageStatus.SUCCESS, StageStatus.STALE}
        cached = self.load(cache_key) if cache_reusable and record.cache_key == cache_key else None
        if cached:
            normalized, fingerprint = normalize_cached_global(
                self.runtime.store,
                cached.content,
                screenplay.data.scenes,
            )
            migration_valid = (
                record.status == StageStatus.STALE
                and fingerprint != cached.artifact_fingerprint
                and ValidationService()
                .validate(
                    screenplay.data,
                    scene_result.records,
                    normalized,
                    require_biographies=False,
                )
                .valid
            )
            if record.status == StageStatus.SUCCESS or migration_valid:
                cached = GlobalStageResult(normalized, fingerprint)
                self.runtime.cached("global_analysis", cache_key, cached.artifact_fingerprint)
                return cached
        previous_usage = record.usage if record.cache_key == cache_key else TokenUsage()
        self.runtime.start("global_analysis", cache_key, "归一实体并分析全局叙事结构")
        call = None
        try:
            analyses = [record.analysis for record in scene_result.records if record.analysis]
            call = self.analyzer.analyze_global(screenplay.data, analyses, config)
            normalized = normalize_global_with_recovery(
                call.content,
                screenplay.data,
                scene_result.records,
                cache_key,
            )
            result = normalized.content
            fingerprint = normalized.recovery.result_fingerprint
            usage = sum_usage((previous_usage, call.usage))
            upstream = [
                screenplay.metadata.artifact_fingerprint,
                scene_result.artifact_fingerprint,
            ]
            for name, data in global_parts(result).items():
                artifact = make_artifact(
                    stage_name="global_analysis",
                    cache_key=cache_key,
                    data=data,
                    source_fingerprint=screenplay.data.source_fingerprint,
                    upstream_fingerprints=upstream,
                    prompt_fingerprint=self.analyzer.global_prompt_fingerprint,
                    model=config.model,
                    model_parameters=model_parameters(project),
                    usage=usage,
                )
                self.runtime.store.write_artifact(name, artifact)
            self.runtime.store.write_model(
                self.runtime.store.artifact_path("global_recovery"),
                normalized.recovery,
            )
            self.runtime.success("global_analysis", cache_key, fingerprint, usage)
            return GlobalStageResult(result, fingerprint)
        except Exception as error:
            _, current_usage = model_failure_metadata(
                error,
                call,
                config.max_retries + 1,
            )
            usage = sum_usage((previous_usage, current_usage))
            self.runtime.fail("global_analysis", error, usage)
            raise PipelineStageError(f"全局叙事分析失败：{error}") from error

    def load(self, cache_key: str | None = None) -> GlobalStageResult | None:
        """从四个严格类型产物重建全局分析结果。

        Args:
            cache_key: 可选的预期缓存键；提供时要求全部产物匹配。

        Returns:
            可重建的全局结果；任一产物缺失、损坏或过期时返回空。
        """
        loaded = load_global_result(self.runtime.store, cache_key)
        if loaded is None:
            return None
        result, fingerprint = loaded
        return GlobalStageResult(result, fingerprint)
