"""通过 Agno 调用 DeepSeek V4 Pro 的叙事分析实现。"""

from __future__ import annotations

import json
from typing import Any

from agno.agent import Agent
from pydantic import BaseModel, SecretStr

from movie_breakdown.application.biography_context import BiographyAnalysisContext
from movie_breakdown.application.ports import ModelCallResult
from movie_breakdown.domain.character_biography import CharacterBiography
from movie_breakdown.domain.global_analysis import GlobalAnalysisResult
from movie_breakdown.domain.run import ProjectConfig
from movie_breakdown.domain.scene_analysis import SceneAnalysis
from movie_breakdown.domain.source import NormalizedDocument, Scene, SceneFormatProfile, Screenplay
from movie_breakdown.infrastructure.llm.agno_client import (
    AgnoStructuredClient,
)
from movie_breakdown.infrastructure.llm.agno_client import (
    ModelAnalysisError as ModelAnalysisError,
)
from movie_breakdown.infrastructure.llm.payloads import (
    normalize_character_biography_payload,
)
from movie_breakdown.infrastructure.llm.prompts import (
    BIOGRAPHY_INSTRUCTIONS,
    FORMAT_INSTRUCTIONS,
    GLOBAL_INSTRUCTIONS,
    SCENE_INSTRUCTIONS,
    biography_prompt_fingerprint,
    format_prompt_fingerprint,
    global_prompt_fingerprint,
    scene_prompt_fingerprint,
)
from movie_breakdown.infrastructure.scene_sampling import sample_format_pages


class AgnoNarrativeAnalyzer:
    """使用职责单一的 Agno Agent 完成叙事分析。

    Attributes:
        api_key: DeepSeek API 密钥，仅保存在内存中。
        timeout_seconds: 单次网络请求超时秒数。
    """

    def __init__(self, api_key: SecretStr | str, timeout_seconds: float = 600) -> None:
        """创建 DeepSeek 叙事分析器。

        Args:
            api_key: DeepSeek API 密钥或 Pydantic `SecretStr`。
            timeout_seconds: 单次模型请求超时秒数。
        """
        self.api_key = api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
        self.timeout_seconds = timeout_seconds
        self._client = AgnoStructuredClient(self.api_key, timeout_seconds)

    @property
    def scene_prompt_fingerprint(self) -> str:
        """返回逐场分析 Prompt 指纹。

        Returns:
            当前逐场 Prompt 的稳定内容指纹。
        """
        return scene_prompt_fingerprint()

    @property
    def format_prompt_fingerprint(self) -> str:
        """返回格式识别 Prompt 指纹。

        Returns:
            当前格式识别 Prompt 的稳定内容指纹。
        """
        return format_prompt_fingerprint()

    @property
    def global_prompt_fingerprint(self) -> str:
        """返回全局分析 Prompt 指纹。

        Returns:
            当前全局 Prompt 的稳定内容指纹。
        """
        return global_prompt_fingerprint()

    @property
    def biography_prompt_fingerprint(self) -> str:
        """返回人物小传 Prompt 指纹。

        Returns:
            当前人物小传 Prompt 的稳定内容指纹。
        """
        return biography_prompt_fingerprint()

    def detect_format(
        self,
        document: NormalizedDocument,
        config: ProjectConfig,
    ) -> ModelCallResult[SceneFormatProfile]:
        """根据剧本首尾样本生成受控场景起始正则。

        Args:
            document: 保留行号和页码映射的规范化剧本。
            config: 当前项目模型配置。

        Returns:
            场景格式画像、token 用量和尝试次数。

        Raises:
            ModelAnalysisError: 重试后仍无法得到有效格式画像。
        """
        sample = sample_format_pages(document)
        prompt = "请识别以下剧本样本的场景起始格式并输出 JSON。\n样本：\n" + sample
        return self._call(SceneFormatProfile, FORMAT_INSTRUCTIONS, prompt, config)

    def analyze_scene(self, scene: Scene, config: ProjectConfig) -> ModelCallResult[SceneAnalysis]:
        """分析单个场景并验证返回的 Pydantic 模型。

        Args:
            scene: 待分析场景。
            config: 当前项目模型配置。

        Returns:
            逐场分析结果、token 用量和尝试次数。

        Raises:
            ModelAnalysisError: 重试后仍无法得到有效结果。
        """
        numbered_text = "\n".join(
            f"{scene.source_span.line_start + offset}: {line}"
            for offset, line in enumerate(scene.text.splitlines())
        )
        prompt = (
            f"请分析以下场景并输出 JSON。\n"
            f"场景 ID：{scene.id}\n标题：{scene.heading}\n"
            f"有效行号：{scene.source_span.line_start}-{scene.source_span.line_end}\n"
            f"原文：\n{numbered_text}"
        )
        return self._call(SceneAnalysis, SCENE_INSTRUCTIONS, prompt, config)

    def analyze_global(
        self,
        screenplay: Screenplay,
        analyses: list[SceneAnalysis],
        config: ProjectConfig,
    ) -> ModelCallResult[GlobalAnalysisResult]:
        """基于逐场分析生成实体归一与全局叙事结构。

        Args:
            screenplay: 场景切分后的剧本。
            analyses: 按场景顺序排列的已验证逐场分析。
            config: 当前项目模型和结构框架配置。

        Returns:
            全局叙事分析结果、token 用量和尝试次数。

        Raises:
            ModelAnalysisError: 重试后仍无法得到有效结果。
        """
        payload = {
            "title": screenplay.title,
            "framework": config.structure_framework,
            "scenes": [
                {"id": scene.id, "ordinal": scene.ordinal, "heading": scene.heading}
                for scene in screenplay.scenes
            ],
            "scene_analyses": [
                analysis.model_dump(mode="json", exclude_computed_fields=True)
                for analysis in analyses
            ],
        }
        prompt = "请完成全局叙事结构分析并输出 JSON。\n输入：\n" + json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        )
        return self._call(GlobalAnalysisResult, GLOBAL_INSTRUCTIONS, prompt, config)

    def analyze_biography(
        self,
        context: BiographyAnalysisContext,
        config: ProjectConfig,
    ) -> ModelCallResult[CharacterBiography]:
        """基于有限、可追溯上下文生成单个人物小传。

        Args:
            context: 目标人物及其相关场景、事件、关系和人物弧光。
            config: 当前项目模型配置。

        Returns:
            声明级人物小传、token 用量和尝试次数。

        Raises:
            ModelAnalysisError: 重试后仍无法得到有效结果。
        """
        payload = context.model_dump(mode="json", exclude_computed_fields=True)
        prompt = "请为输入中的目标人物生成人物小传并输出 JSON。\n输入：\n" + json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return self._call(CharacterBiography, BIOGRAPHY_INSTRUCTIONS, prompt, config)

    def _call[T: BaseModel](
        self,
        schema: type[T],
        instructions: str,
        prompt: str,
        config: ProjectConfig,
    ) -> ModelCallResult[T]:
        """调用 Agno 并在失败时携带精简校验错误请求修复。"""
        normalizer = normalize_character_biography_payload if schema is CharacterBiography else None
        return self._client.call(
            schema,
            instructions,
            prompt,
            config,
            normalizer,
        )

    def _build_agent[T: BaseModel](
        self,
        schema: type[T],
        instructions: str,
        config: ProjectConfig,
    ) -> Agent:
        """为单次请求构造无记忆、无工具的职责单一 Agent。"""
        return self._client.build_agent(schema, instructions, config)

    @staticmethod
    def _coerce[T: BaseModel](schema: type[T], content: Any) -> T:
        """把 Agno 返回的模型、字典或 JSON 文本统一为目标类型。"""
        normalizer = normalize_character_biography_payload if schema is CharacterBiography else None
        return AgnoStructuredClient.coerce(schema, content, normalizer)
