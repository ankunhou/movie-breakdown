"""制片评测封版与专业稳定版的分级发布门禁模型。"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from movie_breakdown.domain.base import StrictModel


class ProductionReleaseProfile(StrEnum):
    """制片封版允许请求的专业强度。"""

    EVALUATION = "evaluation"
    PROFESSIONAL = "professional"


class ProductionReleaseState(StrEnum):
    """当前规划在发布门禁下达到的状态。"""

    BLOCKED = "blocked"
    EVALUATION_READY = "evaluation_ready"
    PROFESSIONAL_STABLE = "professional_stable"


class ProductionReleaseCheckCode(StrEnum):
    """制片封版前必须完整执行的确定性检查。"""

    PLANNING_VALIDATION = "planning_validation"
    PLAN_FINGERPRINT = "plan_fingerprint"
    TARGET_SET_FINGERPRINT = "target_set_fingerprint"
    REVIEW_COMPLETION = "review_completion"
    REVIEW_VERDICTS = "review_verdicts"
    DIMENSION_RATINGS = "dimension_ratings"
    CORRECTION_RECEIPT = "correction_receipt"
    REVIEWER_IDENTITY = "reviewer_identity"
    UNSAFE_DEFAULTS = "unsafe_defaults"
    SAFETY_APPROVALS = "safety_approvals"


class ProductionReleaseCheck(StrictModel):
    """一条可机读、可定位的制片封版检查。"""

    code: ProductionReleaseCheckCode
    name: str = Field(min_length=1, max_length=120)
    passed: bool
    message: str = Field(min_length=1, max_length=1000)
    references: list[str] = Field(default_factory=list)


class ProductionReleaseReport(StrictModel):
    """绑定当前规划与发布策略的最终制片封版决策。"""

    schema_version: str = "1.0"
    profile: ProductionReleaseProfile
    plan_fingerprint: str = Field(min_length=1, max_length=128)
    review_target_set_fingerprint: str = Field(min_length=1, max_length=128)
    state: ProductionReleaseState
    releasable: bool
    checks: list[ProductionReleaseCheck]
    limitations: list[str]

    @model_validator(mode="after")
    def _validate_decision(self) -> Self:
        """保证检查完整唯一且总体决策不能绕过失败项。"""
        codes = [item.code for item in self.checks]
        if len(codes) != len(set(codes)) or set(codes) != set(ProductionReleaseCheckCode):
            raise ValueError("制片发布报告必须完整且唯一地覆盖全部门禁检查。")
        if self.releasable != all(item.passed for item in self.checks):
            raise ValueError("制片发布总体决策必须与全部检查一致。")
        expected_state = (
            ProductionReleaseState.BLOCKED
            if not self.releasable
            else ProductionReleaseState.PROFESSIONAL_STABLE
            if self.profile == ProductionReleaseProfile.PROFESSIONAL
            else ProductionReleaseState.EVALUATION_READY
        )
        if self.state != expected_state:
            raise ValueError("制片发布状态与策略和检查结果不一致。")
        return self
