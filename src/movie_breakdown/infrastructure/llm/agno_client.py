"""叙事与制片分析共享的 Agno 结构化调用客户端。"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Protocol

from agno.agent import Agent
from agno.models.deepseek import DeepSeek
from pydantic import BaseModel, SecretStr

from movie_breakdown.application.ports import ModelCallResult
from movie_breakdown.domain.scene_analysis import TokenUsage
from movie_breakdown.infrastructure.llm.agno_support import (
    extract_usage,
    merge_usage,
    strip_code_fence,
)
from movie_breakdown.infrastructure.llm.payloads import truncate_evidence_excerpts

PayloadNormalizer = Callable[[Any], Any]


class ModelExecutionConfig(Protocol):
    """结构化模型客户端实际使用的最小配置接口。"""

    model: str
    thinking_enabled: bool
    reasoning_effort: str
    max_retries: int


class ModelAnalysisError(RuntimeError):
    """模型请求耗尽重试次数后仍未产生有效结构化结果。

    Attributes:
        usage: 包含所有无效响应在内的累计 token 用量。
        attempts: 实际发起的模型请求次数。
    """

    def __init__(self, message: str, usage: TokenUsage, attempts: int) -> None:
        """保存失败说明以及已经实际产生的调用成本。

        Args:
            message: 最后一次结构化错误的精简说明。
            usage: 所有尝试累计的 token 用量。
            attempts: 实际发起的模型请求次数。
        """
        super().__init__(message)
        self.usage = usage
        self.attempts = attempts


class AgnoStructuredClient:
    """以无记忆 Agno Agent 执行可重试的严格 JSON 调用。

    Attributes:
        api_key: DeepSeek API 密钥，仅保存在内存中。
        timeout_seconds: 单次网络请求超时秒数。
    """

    def __init__(self, api_key: SecretStr | str, timeout_seconds: float = 600) -> None:
        """创建共享结构化调用客户端。

        Args:
            api_key: DeepSeek API 密钥或 Pydantic `SecretStr`。
            timeout_seconds: 单次模型请求超时秒数。
        """
        self.api_key = api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
        self.timeout_seconds = timeout_seconds

    def call[T: BaseModel](
        self,
        schema: type[T],
        instructions: str,
        prompt: str,
        config: ModelExecutionConfig,
        payload_normalizer: PayloadNormalizer | None = None,
    ) -> ModelCallResult[T]:
        """调用模型并在失败时携带精简错误请求修复。

        Args:
            schema: 最终结构化输出类型。
            instructions: 不随单次输入变化的系统约束。
            prompt: 当前业务输入。
            config: 模型、思考模式与重试配置。
            payload_normalizer: Pydantic 校验前的可选领域净化函数。

        Returns:
            已验证内容、累计 token 和实际尝试次数。

        Raises:
            ModelAnalysisError: 所有尝试均无法通过结构化校验。
        """
        errors: list[str] = []
        total_usage = TokenUsage()
        for attempt in range(1, config.max_retries + 2):
            agent = self.build_agent(schema, instructions, config)
            repair = ""
            if errors:
                repair = f"\n上一次输出无效，请修复这些错误：{errors[-1][:1500]}"
            try:
                response = agent.run(prompt + repair, stream=False)
                total_usage = merge_usage(total_usage, extract_usage(response))
                content = self.coerce(schema, response.content, payload_normalizer)
                return ModelCallResult(content=content, usage=total_usage, attempts=attempt)
            except Exception as error:
                errors.append(f"{type(error).__name__}: {error}")
        attempts = config.max_retries + 1
        detail = errors[-1] if errors else "模型未返回内容"
        raise ModelAnalysisError(
            f"结构化分析失败，已尝试 {attempts} 次：{detail}",
            total_usage,
            attempts,
        )

    def build_agent[T: BaseModel](
        self,
        schema: type[T],
        instructions: str,
        config: ModelExecutionConfig,
    ) -> Agent:
        """为单次请求构造无记忆、无工具的职责单一 Agent。

        Args:
            schema: 输出 Pydantic 类型。
            instructions: 当前分析角色约束。
            config: 模型和思考配置。

        Returns:
            禁用遥测、流式输出和 Agent 内部重试的实例。
        """
        model = DeepSeek(
            id=config.model,
            api_key=self.api_key,
            timeout=self.timeout_seconds,
            use_thinking=config.thinking_enabled,
            reasoning_effort=config.reasoning_effort if config.thinking_enabled else None,
            retries=1,
            exponential_backoff=True,
        )
        return Agent(
            model=model,
            instructions=instructions,
            output_schema=schema.model_json_schema(),
            structured_outputs=False,
            use_json_mode=True,
            parse_response=True,
            retries=0,
            telemetry=False,
        )

    @staticmethod
    def coerce[T: BaseModel](
        schema: type[T],
        content: Any,
        payload_normalizer: PayloadNormalizer | None = None,
    ) -> T:
        """把 Agno 模型、字典或 JSON 文本统一为目标类型。

        Args:
            schema: 最终 Pydantic 类型。
            content: Agno 返回的任意受支持内容。
            payload_normalizer: 严格校验前的可选领域净化函数。

        Returns:
            通过完整 Schema 校验的业务模型。
        """
        if isinstance(content, schema):
            return content
        if isinstance(content, BaseModel):
            content = content.model_dump(mode="json")
        if isinstance(content, str):
            content = json.loads(strip_code_fence(content))
        content = truncate_evidence_excerpts(content)
        if payload_normalizer is not None:
            content = payload_normalizer(content)
        return schema.model_validate(content)
