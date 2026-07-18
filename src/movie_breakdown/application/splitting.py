"""在本地规则与模型格式识别之间选择的自适应切分策略。"""

from __future__ import annotations

from dataclasses import dataclass

from movie_breakdown.application.ports import ModelCallResult, SceneFormatDetector
from movie_breakdown.domain.run import ProjectConfig
from movie_breakdown.domain.scene_analysis import TokenUsage
from movie_breakdown.domain.source import NormalizedDocument, SceneFormatProfile, Screenplay
from movie_breakdown.infrastructure.scene_splitter import (
    UnsafeScenePatternError,
    split_is_reasonable,
    split_scenes,
)


class SceneSplitError(ValueError):
    """本地规则与模型格式画像都无法可靠切分剧本。"""


@dataclass(frozen=True, slots=True)
class AdaptiveSplitResult:
    """自适应场景切分结果及模型开销。

    Attributes:
        screenplay: 最终被接受的场景切分结果。
        usage: 格式识别消耗的 token；纯本地切分时为零。
        attempts: 格式识别模型请求次数；纯本地切分时为零。
        warning: 自动回退到本地规则时的诊断信息。
    """

    screenplay: Screenplay
    usage: TokenUsage
    attempts: int
    warning: str | None = None


class AdaptiveSceneSplitter:
    """根据项目策略和本地置信度选择场景切分实现。

    Attributes:
        detector: 可选的 Agno/DeepSeek 格式识别策略。
    """

    def __init__(self, detector: SceneFormatDetector | None) -> None:
        """创建自适应场景切分器。

        Args:
            detector: 低置信度或强制模型模式使用的格式识别器。
        """
        self.detector = detector

    def split(
        self,
        document: NormalizedDocument,
        config: ProjectConfig,
    ) -> AdaptiveSplitResult:
        """按 `local`、`auto` 或 `model` 策略切分剧本。

        Args:
            document: 保留页码和行号的规范化文档。
            config: 包含格式识别策略和模型参数的项目配置。

        Returns:
            最终场景列表、格式识别用量和可选回退警告。

        Raises:
            SceneSplitError: 指定策略无法产生合理切分。
        """
        local = split_scenes(document)
        local_valid = split_is_reasonable(local, document)
        if config.format_detection == "local":
            if not local_valid:
                raise SceneSplitError("本地规则无法可靠识别场景，请改用 auto 或 model。")
            return AdaptiveSplitResult(local, TokenUsage(), 0)
        if config.format_detection == "auto" and local_valid:
            return AdaptiveSplitResult(local, TokenUsage(), 0)
        if self.detector is None:
            raise SceneSplitError("当前切分需要模型识别格式，但没有可用的格式识别器。")

        detected = self.detector.detect_format(document, config)
        try:
            candidate = split_scenes(document, detected.content)
        except UnsafeScenePatternError as error:
            return self._fallback_or_raise(local, local_valid, detected, str(error), config)
        if not split_is_reasonable(candidate, document):
            return self._fallback_or_raise(
                local,
                local_valid,
                detected,
                "模型正则产生的场景数量或平均长度不合理。",
                config,
            )
        return AdaptiveSplitResult(candidate, detected.usage, detected.attempts)

    @staticmethod
    def _fallback_or_raise(
        local: Screenplay,
        local_valid: bool,
        detected: ModelCallResult[SceneFormatProfile],
        reason: str,
        config: ProjectConfig,
    ) -> AdaptiveSplitResult:
        """仅在 auto 且本地结果可信时回退，否则明确失败。"""
        if config.format_detection == "auto" and local_valid:
            return AdaptiveSplitResult(
                local,
                detected.usage,
                detected.attempts,
                warning=reason,
            )
        raise SceneSplitError(reason)
