"""应用层依赖的模型分析接口。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from movie_breakdown.application.biography_context import BiographyAnalysisContext
from movie_breakdown.domain.character_biography import CharacterBiography
from movie_breakdown.domain.global_analysis import GlobalAnalysisResult
from movie_breakdown.domain.run import ProjectConfig
from movie_breakdown.domain.scene_analysis import SceneAnalysis, TokenUsage
from movie_breakdown.domain.source import (
    NormalizedDocument,
    Scene,
    SceneFormatProfile,
    Screenplay,
)


@dataclass(frozen=True, slots=True)
class ModelCallResult[T]:
    """模型调用返回的已验证内容及资源用量。

    Attributes:
        content: 通过目标 Pydantic Schema 校验的内容。
        usage: 本次调用累计的 token 用量。
        attempts: 实际发起模型请求的次数。
    """

    content: T
    usage: TokenUsage
    attempts: int


class SceneFormatDetector(Protocol):
    """剧本场景标题格式识别的可替换策略接口。"""

    @property
    def format_prompt_fingerprint(self) -> str:
        """返回格式识别 Prompt 指纹。

        Returns:
            当前格式识别 Prompt 的稳定内容指纹。
        """
        ...

    def detect_format(
        self,
        document: NormalizedDocument,
        config: ProjectConfig,
    ) -> ModelCallResult[SceneFormatProfile]:
        """从剧本首尾样本识别场景起始格式。

        Args:
            document: 保留页码与行号的规范化文档。
            config: 当前项目模型配置。

        Returns:
            已验证的场景格式画像和模型用量。

        Raises:
            ModelAnalysisError: 多次请求后仍无法得到有效结果。
        """
        ...


class CharacterBiographer(Protocol):
    """单个人物小传分析的可替换策略接口。"""

    @property
    def biography_prompt_fingerprint(self) -> str:
        """返回人物小传 Prompt 指纹。

        Returns:
            当前人物小传 Prompt 的稳定内容指纹。
        """
        ...

    def analyze_biography(
        self,
        context: BiographyAnalysisContext,
        config: ProjectConfig,
    ) -> ModelCallResult[CharacterBiography]:
        """根据有限且可追溯的上下文生成单个人物小传。

        Args:
            context: 目标人物相关实体、场景、事件、关系和人物弧光。
            config: 当前项目模型配置。

        Returns:
            已验证的人物小传和模型用量。

        Raises:
            ModelAnalysisError: 多次请求后仍无法得到有效结果。
        """
        ...


class NarrativeAnalyzer(SceneFormatDetector, CharacterBiographer, Protocol):
    """格式识别、逐场与全局叙事分析的可替换策略接口。"""

    @property
    def scene_prompt_fingerprint(self) -> str:
        """返回逐场分析 Prompt 指纹。

        Returns:
            当前逐场 Prompt 的稳定内容指纹。
        """
        ...

    @property
    def global_prompt_fingerprint(self) -> str:
        """返回全局分析 Prompt 指纹。

        Returns:
            当前全局 Prompt 的稳定内容指纹。
        """
        ...

    def analyze_scene(self, scene: Scene, config: ProjectConfig) -> ModelCallResult[SceneAnalysis]:
        """分析单个场景并返回严格结构化结果。

        Args:
            scene: 待分析的完整场景及原文位置。
            config: 当前项目模型配置。

        Returns:
            已验证的逐场分析结果和调用用量。

        Raises:
            ModelAnalysisError: 多次请求后仍无法得到有效结果。
        """
        ...

    def analyze_global(
        self,
        screenplay: Screenplay,
        analyses: list[SceneAnalysis],
        config: ProjectConfig,
    ) -> ModelCallResult[GlobalAnalysisResult]:
        """基于已验证逐场结果生成全局叙事分析。

        Args:
            screenplay: 场景切分后的剧本索引。
            analyses: 按场景顺序排列的逐场分析结果。
            config: 当前项目模型与结构框架配置。

        Returns:
            已验证的全局分析结果和调用用量。

        Raises:
            ModelAnalysisError: 多次请求后仍无法得到有效结果。
        """
        ...
