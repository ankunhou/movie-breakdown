"""环境诊断检查项与报告模型。"""

from __future__ import annotations

from enum import StrEnum

from movie_breakdown.domain.base import StrictModel


class CheckStatus(StrEnum):
    """单个环境诊断检查项状态。"""

    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"
    SKIPPED = "skipped"


class DoctorCheck(StrictModel):
    """单个环境或服务诊断结果。"""

    name: str
    status: CheckStatus
    message: str


class DoctorReport(StrictModel):
    """CLI 运行环境和 DeepSeek 可用性报告。"""

    schema_version: str = "1.0"
    ok: bool
    checks: list[DoctorCheck]
