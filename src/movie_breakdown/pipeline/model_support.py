"""叙事与制片模型阶段共享的调用成本辅助能力。"""

from __future__ import annotations

from collections.abc import Iterable

from movie_breakdown.domain.scene_analysis import TokenUsage


def sum_usage(values: Iterable[TokenUsage]) -> TokenUsage:
    """合并一组模型调用的 token 用量。

    Args:
        values: 待累加的 token 用量序列。

    Returns:
        输入、输出和总 token 分别求和的结果。
    """
    result = TokenUsage()
    for value in values:
        result = TokenUsage(
            input_tokens=result.input_tokens + value.input_tokens,
            output_tokens=result.output_tokens + value.output_tokens,
            total_tokens=result.total_tokens + value.total_tokens,
        )
    return result


def model_failure_metadata(
    error: Exception,
    call: object | None,
    default_attempts: int,
) -> tuple[int, TokenUsage]:
    """提取模型内部失败或成功后处理失败的真实调用成本。

    Args:
        error: 触发失败记录的异常。
        call: 后处理前已取得的可选模型调用结果。
        default_attempts: 完全没有调用元数据时使用的保守尝试次数。

    Returns:
        实际尝试次数和累计 token；异常元数据优先于成功调用元数据。
    """
    call_attempts = int(getattr(call, "attempts", default_attempts))
    call_usage = getattr(call, "usage", TokenUsage())
    attempts = int(getattr(error, "attempts", call_attempts))
    usage = getattr(error, "usage", call_usage)
    return attempts, usage if isinstance(usage, TokenUsage) else TokenUsage()
