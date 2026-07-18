"""应用层依赖的独立制片模型分析接口。"""

from __future__ import annotations

from typing import Protocol

from movie_breakdown.application.ports import ModelCallResult
from movie_breakdown.domain.production_run import ProductionConfig
from movie_breakdown.domain.production_scene import SceneProductionAnalysis
from movie_breakdown.domain.source import Scene


class ProductionAnalyzer(Protocol):
    """单场制片元素分析的可替换策略接口。"""

    @property
    def production_prompt_fingerprint(self) -> str:
        """返回制片逐场 Prompt 指纹。

        Returns:
            当前制片 Prompt 的稳定内容指纹。
        """
        ...

    def analyze_scene(
        self,
        scene: Scene,
        config: ProductionConfig,
    ) -> ModelCallResult[SceneProductionAnalysis]:
        """从场景原文提取严格制片需求。

        Args:
            scene: 待分析的完整场景与来源范围。
            config: 独立制片模型配置。

        Returns:
            已验证的逐场制片拆解和模型用量。

        Raises:
            ModelAnalysisError: 多次请求后仍无法得到有效结果。
        """
        ...
