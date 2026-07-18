"""通过共享 Agno 客户端调用 DeepSeek 的制片分析实现。"""

from __future__ import annotations

from pydantic import SecretStr

from movie_breakdown.application.ports import ModelCallResult
from movie_breakdown.domain.production_run import ProductionConfig
from movie_breakdown.domain.production_scene import SceneProductionAnalysis
from movie_breakdown.domain.source import Scene
from movie_breakdown.infrastructure.llm.agno_client import AgnoStructuredClient
from movie_breakdown.infrastructure.llm.payloads import normalize_production_payload
from movie_breakdown.infrastructure.llm.production_prompts import (
    PRODUCTION_INSTRUCTIONS,
    production_prompt_fingerprint,
)


class AgnoProductionAnalyzer:
    """使用独立 Prompt 和 Schema 完成单场制片元素拆解。

    Attributes:
        client: 无记忆、无工具的共享 Agno 结构化客户端。
    """

    def __init__(self, api_key: SecretStr | str, timeout_seconds: float = 600) -> None:
        """创建 DeepSeek 制片分析器。

        Args:
            api_key: DeepSeek API 密钥或 Pydantic `SecretStr`。
            timeout_seconds: 单次模型请求超时秒数。
        """
        self.client = AgnoStructuredClient(api_key, timeout_seconds)

    @property
    def production_prompt_fingerprint(self) -> str:
        """返回制片逐场 Prompt 指纹。

        Returns:
            当前制片 Prompt 的稳定内容指纹。
        """
        return production_prompt_fingerprint()

    def analyze_scene(
        self,
        scene: Scene,
        config: ProductionConfig,
    ) -> ModelCallResult[SceneProductionAnalysis]:
        """分析一个场景的制片设置、演员、元素和复杂度。

        Args:
            scene: 待分析场景及原文范围。
            config: 独立制片模型配置。

        Returns:
            已通过 Pydantic 校验的制片结果和调用用量。

        Raises:
            ModelAnalysisError: 重试后仍无法得到有效结构化结果。
        """
        numbered_text = "\n".join(
            f"{scene.source_span.line_start + offset}: {line}"
            for offset, line in enumerate(scene.text.splitlines())
        )
        prompt = (
            f"请拆解以下场景的制片元素并输出 JSON。\n"
            f"场景 ID：{scene.id}\n"
            f"标题：{scene.heading}\n"
            f"有效行号：{scene.source_span.line_start}-{scene.source_span.line_end}\n"
            f"原文：\n{numbered_text}"
        )
        return self.client.call(
            SceneProductionAnalysis,
            PRODUCTION_INSTRUCTIONS,
            prompt,
            config,
            normalize_production_payload,
        )
