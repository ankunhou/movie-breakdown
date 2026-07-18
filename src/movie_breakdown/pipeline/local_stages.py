"""剧本规范化与自适应场景切分阶段。"""

from __future__ import annotations

from movie_breakdown.application.splitting import AdaptiveSceneSplitter
from movie_breakdown.domain.run import Artifact, ProjectDocument
from movie_breakdown.domain.source import NormalizedDocument, Screenplay
from movie_breakdown.infrastructure.fingerprint import (
    cache_fingerprint,
    hash_bytes,
    schema_fingerprint,
)
from movie_breakdown.infrastructure.parsers import read_and_normalize
from movie_breakdown.pipeline.artifacts import make_artifact
from movie_breakdown.pipeline.definitions import get_stage
from movie_breakdown.pipeline.runtime import PipelineStageError, StageRuntime


class LocalStageService:
    """执行不依赖正文分析模型的导入与切分阶段。

    Attributes:
        runtime: 阶段状态和项目存储运行时。
        splitter: 可按策略调用格式识别模型的场景切分器。
    """

    def __init__(self, runtime: StageRuntime, splitter: AdaptiveSceneSplitter) -> None:
        """创建本地阶段服务。

        Args:
            runtime: 当前流水线运行时。
            splitter: 自适应场景切分策略。
        """
        self.runtime = runtime
        self.splitter = splitter

    def normalize(self, project: ProjectDocument) -> Artifact[NormalizedDocument]:
        """读取、规范化并缓存项目内源剧本。

        Args:
            project: 当前项目描述。

        Returns:
            带完整追溯元数据的规范化文档产物。

        Raises:
            PipelineStageError: 读取或规范化源剧本失败。
        """
        source_path = self.runtime.store.source_path(project)
        source_fingerprint = hash_bytes(source_path.read_bytes())
        spec = get_stage("normalize")
        cache_key = cache_fingerprint(
            source_fingerprint,
            spec.version,
            schema_fingerprint(NormalizedDocument),
        )
        cached = self.runtime.load_cached("normalize", "normalized", NormalizedDocument, cache_key)
        if cached:
            return cached
        self.runtime.start("normalize", cache_key, "导入并规范化剧本文本")
        try:
            document = read_and_normalize(source_path)
            artifact = make_artifact(
                stage_name="normalize",
                cache_key=cache_key,
                data=document,
                source_fingerprint=document.source.fingerprint,
                upstream_fingerprints=[],
            )
            self.runtime.store.write_artifact("normalized", artifact)
            self.runtime.success("normalize", cache_key, artifact.metadata.artifact_fingerprint)
            return artifact
        except Exception as error:
            self.runtime.fail("normalize", error)
            raise PipelineStageError(f"剧本规范化失败：{error}") from error

    def split(
        self,
        project: ProjectDocument,
        normalized: Artifact[NormalizedDocument],
    ) -> Artifact[Screenplay]:
        """按项目策略切分场景并缓存格式画像。

        Args:
            project: 当前项目描述及格式识别策略。
            normalized: 已验证的规范化文档产物。

        Returns:
            场景切分后的剧本产物。

        Raises:
            PipelineStageError: 本地与模型策略均无法可靠切分。
        """
        detector = self.splitter.detector
        prompt_fingerprint = detector.format_prompt_fingerprint if detector else None
        spec = get_stage("scenes")
        config = project.config
        cache_key = cache_fingerprint(
            normalized.metadata.artifact_fingerprint,
            spec.version,
            schema_fingerprint(Screenplay),
            config.format_detection,
            prompt_fingerprint,
            config.model,
            config.thinking_enabled,
            config.reasoning_effort,
        )
        cached = self.runtime.load_cached("scenes", "scenes", Screenplay, cache_key)
        if cached:
            return cached
        self.runtime.start("scenes", cache_key, "识别格式并切分场景")
        try:
            result = self.splitter.split(normalized.data, config)
            used_model = result.screenplay.split_method == "model" or result.usage.total_tokens > 0
            artifact = make_artifact(
                stage_name="scenes",
                cache_key=cache_key,
                data=result.screenplay,
                source_fingerprint=normalized.data.source.fingerprint,
                upstream_fingerprints=[normalized.metadata.artifact_fingerprint],
                prompt_fingerprint=prompt_fingerprint if used_model else None,
                model=config.model if used_model else None,
                model_parameters=_model_parameters(project) if used_model else {},
                usage=result.usage,
            )
            self.runtime.store.write_artifact("scenes", artifact)
            self.runtime.success(
                "scenes",
                cache_key,
                artifact.metadata.artifact_fingerprint,
                result.usage,
            )
            if result.warning:
                self.runtime.notify("scenes", f"已回退本地规则：{result.warning}")
            return artifact
        except Exception as error:
            self.runtime.fail("scenes", error)
            raise PipelineStageError(f"场景切分失败：{error}") from error


def _model_parameters(project: ProjectDocument) -> dict[str, str | int | bool]:
    """提取场景格式识别所需的非敏感模型参数。"""
    config = project.config
    return {
        "thinking_enabled": config.thinking_enabled,
        "reasoning_effort": config.reasoning_effort,
        "max_retries": config.max_retries,
    }
