"""叙事稳定版发布门禁的结构化结果模型。"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from movie_breakdown.domain.base import StrictModel


class ReleaseGateCheckCode(StrEnum):
    """叙事稳定版发布前必须通过的门禁检查。"""

    STRUCTURAL_VALIDATION = "structural_validation"
    ANALYSIS_FINGERPRINT = "analysis_fingerprint"
    REVIEWER_IDENTITY = "reviewer_identity"
    TARGET_COUNT = "target_count"
    REVIEW_COMPLETION = "review_completion"
    REVIEW_VERDICTS = "review_verdicts"
    DIMENSION_RATINGS = "dimension_ratings"
    ACCEPTED_RISKS = "accepted_risks"


class ReleaseGateCheck(StrictModel):
    """一条可机读、可定位的发布门禁检查结果。"""

    code: ReleaseGateCheckCode
    name: str = Field(min_length=1)
    passed: bool
    message: str = Field(min_length=1)
    references: list[str] = Field(default_factory=list)


class ReleaseGateReport(StrictModel):
    """绑定当前分析指纹的叙事稳定版发布决策。

    Attributes:
        analysis_fingerprint: 本次被评估的叙事拆解内容指纹。
        stable: 所有门禁检查是否全部通过。
        checks: 按稳定顺序输出的逐条门禁检查。
    """

    schema_version: str = "1.0"
    analysis_fingerprint: str = Field(min_length=1)
    stable: bool
    checks: list[ReleaseGateCheck] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_decision(self) -> Self:
        """保证总体决策与逐条检查一致且检查不重复。

        Returns:
            通过一致性验证的发布门禁报告。

        Raises:
            ValueError: 检查代码重复或总体决策与逐条结果矛盾。
        """
        codes = [check.code for check in self.checks]
        if len(codes) != len(set(codes)):
            raise ValueError("发布门禁报告包含重复检查。")
        if self.stable != all(check.passed for check in self.checks):
            raise ValueError("发布决策必须与所有门禁检查结果一致。")
        if set(codes) != set(ReleaseGateCheckCode):
            raise ValueError("发布门禁报告必须完整覆盖所有检查。")
        return self
