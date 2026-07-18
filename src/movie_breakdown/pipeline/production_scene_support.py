"""制片逐场缓存、恢复和模型参数的纯函数辅助能力。"""

from __future__ import annotations

from movie_breakdown.domain.base import StageStatus
from movie_breakdown.domain.production_run import ProductionProject
from movie_breakdown.domain.production_scene import SceneProductionRecord
from movie_breakdown.pipeline.model_support import sum_usage


def production_record_is_valid(
    record: SceneProductionRecord | None,
    cache_key: str,
) -> bool:
    """判断制片逐场记录是否成功且匹配当前缓存键。

    Args:
        record: 已持久化的可选逐场记录。
        cache_key: 当前场景的预期缓存键。

    Returns:
        该记录是否可安全复用。
    """
    return bool(
        record
        and record.status == StageStatus.SUCCESS
        and record.analysis is not None
        and record.cache_key == cache_key
    )


def merge_production_retry_history(
    current: SceneProductionRecord,
    previous: SceneProductionRecord | None,
) -> SceneProductionRecord:
    """累计同一缓存键下前次失败的尝试次数和 token。

    Args:
        current: 本次场景分析产生的记录。
        previous: 同一场景上一条可选记录。

    Returns:
        保留真实失败成本的新记录；输入变化时只返回本次成本。
    """
    if (
        previous is None
        or previous.status != StageStatus.FAILED
        or previous.cache_key != current.cache_key
    ):
        return current
    return current.model_copy(
        update={
            "attempts": previous.attempts + current.attempts,
            "usage": sum_usage((previous.usage, current.usage)),
        }
    )


def production_model_parameters(
    project: ProductionProject,
) -> dict[str, str | int | bool]:
    """返回可安全写入产物的制片模型参数。

    Args:
        project: 独立制片项目配置。

    Returns:
        不包含密钥和叙事无关设置的模型参数。
    """
    config = project.config
    return {
        "contract_version": config.contract_version,
        "thinking_enabled": config.thinking_enabled,
        "reasoning_effort": config.reasoning_effort,
        "max_retries": config.max_retries,
    }
