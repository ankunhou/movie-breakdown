"""领域模型共享的基础类型。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


def utc_now() -> datetime:
    """返回带 UTC 时区的当前时间。

    Returns:
        当前 UTC 时间。
    """

    return datetime.now(UTC)


class StrictModel(BaseModel):
    """禁止模型或文件悄悄携带未声明字段。"""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Confidence(StrEnum):
    """模型结论的可信程度。"""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class StageStatus(StrEnum):
    """流水线阶段或逐场任务的执行状态。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    STALE = "stale"


class Severity(StrEnum):
    """本地校验问题的严重程度。"""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
