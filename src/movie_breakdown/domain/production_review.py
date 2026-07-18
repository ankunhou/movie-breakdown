"""制片规划专家评审目标、答案与完成度模型。"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from movie_breakdown.domain.base import StrictModel
from movie_breakdown.domain.scene_analysis import Evidence


class ProductionReviewDimension(StrEnum):
    """制片专家需要逐项评分的专业维度。"""

    SOURCE_FIDELITY = "source_fidelity"
    SHOOTING_UNIT_BOUNDARY = "shooting_unit_boundary"
    IDENTITY_RESOLUTION = "identity_resolution"
    QUANTITY_FIDELITY = "quantity_fidelity"
    UNIT_STANDARDIZATION = "unit_standardization"
    CONTINUITY = "continuity"
    SAFETY_SCOPE = "safety_scope"
    IMPLEMENTATION_SAFETY = "implementation_safety"


class ProductionReviewTargetKind(StrEnum):
    """必须由专家判断的制片规划对象类型。"""

    SHOOTING_UNIT = "shooting_unit"
    ENTITY = "entity"
    QUANTITY = "quantity"
    SAFETY_HAZARD = "safety_hazard"
    UNSAFE_DEFAULT = "unsafe_default"


class ProductionReviewerKind(StrEnum):
    """制片评审者是真人专家还是明确标注的 AI 模拟。"""

    AI_SIMULATED = "ai_simulated"
    HUMAN_EXPERT = "human_expert"


class ProductionReviewVerdict(StrEnum):
    """专家对一个制片规划目标给出的结论。"""

    UNREVIEWED = "unreviewed"
    SUPPORTED = "supported"
    NEEDS_CORRECTION = "needs_correction"
    ACCEPTED_RISK = "accepted_risk"
    BLOCKED = "blocked"


class ProductionReviewTarget(StrictModel):
    """从全部强制风险与目录问题生成的单个评审目标。"""

    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$", max_length=200)
    kind: ProductionReviewTargetKind
    title: str = Field(min_length=1, max_length=300)
    claim: str = Field(min_length=1, max_length=1200)
    references: list[str] = Field(min_length=1, max_length=300)
    dimensions: list[ProductionReviewDimension] = Field(min_length=1)
    evidence: list[Evidence] = Field(min_length=1, max_length=24)
    mandatory: bool = True
    risk_reasons: list[str] = Field(min_length=1, max_length=30)


class ProductionDimensionRating(StrictModel):
    """一个制片评审维度的五分制结果。"""

    dimension: ProductionReviewDimension
    score: int | None = Field(default=None, ge=1, le=5)
    comment: str = Field(default="", max_length=800)


class ProductionReviewResponse(StrictModel):
    """评审者对一个规划目标的判断与关联修正。"""

    target_id: str
    verdict: ProductionReviewVerdict = ProductionReviewVerdict.UNREVIEWED
    ratings: list[ProductionDimensionRating]
    notes: str = Field(default="", max_length=1600)
    correction_ids: list[str] = Field(default_factory=list, max_length=30)


class ProductionReviewAnswers(StrictModel):
    """可外部填写并按完整目标集指纹重新导入的专家答案。"""

    schema_version: str = "1.0"
    plan_fingerprint: str = Field(min_length=1, max_length=128)
    target_set_fingerprint: str = Field(min_length=1, max_length=128)
    rubric_version: str = Field(min_length=1, max_length=40)
    safety_policy_version: str = Field(min_length=1, max_length=40)
    reviewer: str = Field(default="", max_length=120)
    reviewer_kind: ProductionReviewerKind = ProductionReviewerKind.AI_SIMULATED
    reviewer_roles: list[str] = Field(default_factory=list, max_length=20)
    responses: list[ProductionReviewResponse]

    @model_validator(mode="after")
    def _validate_unique_responses(self) -> Self:
        """拒绝重复目标答案和重复评审角色。"""
        response_ids = [item.target_id for item in self.responses]
        if len(response_ids) != len(set(response_ids)):
            raise ValueError("制片专家答案包含重复 target_id。")
        if len(self.reviewer_roles) != len(set(self.reviewer_roles)):
            raise ValueError("制片专家答案包含重复 reviewer role。")
        return self


class ProductionReviewReport(StrictModel):
    """绑定当前规划、完整目标集和可选答案的制片专家评审报告。"""

    schema_version: str = "1.0"
    plan_fingerprint: str = Field(min_length=1, max_length=128)
    target_set_fingerprint: str = Field(min_length=1, max_length=128)
    rubric_version: str = Field(min_length=1, max_length=40)
    safety_policy_version: str = Field(min_length=1, max_length=40)
    reviewer: str
    reviewer_kind: ProductionReviewerKind
    reviewer_roles: list[str]
    targets: list[ProductionReviewTarget]
    responses: list[ProductionReviewResponse]
    reviewed_count: int = Field(ge=0)
    target_count: int = Field(ge=0)
    coverage: float = Field(ge=0, le=1)
    complete: bool
    blocked_target_ids: list[str]
    limitations: list[str]

    @model_validator(mode="after")
    def _validate_summary(self) -> Self:
        """确保目标、响应与完成度摘要完全一致。"""
        target_ids = [item.id for item in self.targets]
        response_ids = [item.target_id for item in self.responses]
        if len(target_ids) != len(set(target_ids)) or set(target_ids) != set(response_ids):
            raise ValueError("制片评审目标与响应必须唯一且一一对应。")
        reviewed = sum(
            item.verdict != ProductionReviewVerdict.UNREVIEWED for item in self.responses
        )
        if self.reviewed_count != reviewed or self.target_count != len(self.targets):
            raise ValueError("制片评审完成度摘要与实际响应不一致。")
        expected_coverage = reviewed / len(self.targets) if self.targets else 0
        if self.coverage != expected_coverage or self.complete != (reviewed == len(self.targets)):
            raise ValueError("制片评审覆盖率或完成状态不一致。")
        return self
