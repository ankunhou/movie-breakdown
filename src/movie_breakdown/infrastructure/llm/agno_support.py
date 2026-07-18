"""Agno 结构化调用共享的响应净化与用量统计。"""

from __future__ import annotations

from typing import Any

from movie_breakdown.domain.scene_analysis import TokenUsage


def strip_code_fence(content: str) -> str:
    """移除模型偶尔附加在 JSON 外的 Markdown 代码围栏。

    Args:
        content: 可能包含 Markdown 代码围栏的模型文本。

    Returns:
        去除外围代码围栏和空白后的内容。
    """
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1]
        stripped = stripped.rsplit("```", 1)[0]
    return stripped.strip()


def extract_usage(response: Any) -> TokenUsage:
    """兼容不同 Agno 版本地提取 OpenAI 风格 token 指标。

    Args:
        response: Agno 返回的运行响应对象。

    Returns:
        规范化后的输入、输出和总 token 用量。
    """
    metrics = getattr(response, "metrics", None)
    if metrics is None:
        return TokenUsage()
    input_tokens = int(getattr(metrics, "input_tokens", 0) or 0)
    output_tokens = int(getattr(metrics, "output_tokens", 0) or 0)
    total_tokens = int(getattr(metrics, "total_tokens", 0) or 0)
    if input_tokens or output_tokens or total_tokens:
        return TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens or input_tokens + output_tokens,
        )
    details = getattr(metrics, "details", None) or {}
    for entries in details.values():
        for entry in entries:
            input_tokens += int(getattr(entry, "input_tokens", 0) or 0)
            output_tokens += int(getattr(entry, "output_tokens", 0) or 0)
            total_tokens += int(getattr(entry, "total_tokens", 0) or 0)
            values = getattr(entry, "provider_metrics", None) or {}
            if not any(
                (
                    getattr(entry, "input_tokens", 0),
                    getattr(entry, "output_tokens", 0),
                    getattr(entry, "total_tokens", 0),
                )
            ):
                input_tokens += int(values.get("input_tokens", values.get("prompt_tokens", 0)) or 0)
                output_tokens += int(
                    values.get("output_tokens", values.get("completion_tokens", 0)) or 0
                )
                total_tokens += int(values.get("total_tokens", 0) or 0)
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens or input_tokens + output_tokens,
    )


def merge_usage(left: TokenUsage, right: TokenUsage) -> TokenUsage:
    """合并重试过程中产生的 token 用量。

    Args:
        left: 已累计的 token 用量。
        right: 本次请求新增的 token 用量。

    Returns:
        分项相加后的 token 用量。
    """
    return TokenUsage(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        total_tokens=left.total_tokens + right.total_tokens,
    )
