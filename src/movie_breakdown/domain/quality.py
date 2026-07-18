"""叙事语义质量信号、人工抽检目标与评测报告模型。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from movie_breakdown.domain.base import StrictModel, utc_now
from movie_breakdown.domain.scene_analysis import Evidence
from movie_breakdown.domain.source import SourceSpan


class QualityDimension(StrEnum):
    """人工评测使用的叙事质量维度。"""

    SOURCE_FIDELITY = "source_fidelity"
    EVIDENCE_SUFFICIENCY = "evidence_sufficiency"
    CAUSAL_COHERENCE = "causal_coherence"
    STRUCTURAL_PLAUSIBILITY = "structural_plausibility"
    CHARACTER_ARC_COHERENCE = "character_arc_coherence"
    CHARACTER_PORTRAIT_COHERENCE = "character_portrait_coherence"
    THEME_PLAUSIBILITY = "theme_plausibility"
    UNCERTAINTY_CALIBRATION = "uncertainty_calibration"


class SignalStatus(StrEnum):
    """自动质量信号的解释状态。"""

    GOOD = "good"
    ATTENTION = "attention"
    INFO = "info"
    NOT_APPLICABLE = "not_applicable"


class ReviewTargetKind(StrEnum):
    """需要人工判断的叙事结论类型。"""

    SCENE_SUMMARY = "scene_summary"
    EVENT_CAUSALITY = "event_causality"
    ACT_TURNING_POINT = "act_turning_point"
    BEAT = "beat"
    CHARACTER_ARC = "character_arc"
    CHARACTER_RELATION = "character_relation"
    CHARACTER_BIOGRAPHY = "character_biography"
    CHARACTER_BIOGRAPHY_CLAIM = "character_biography_claim"
    PLOT_THREAD = "plot_thread"
    FORESHADOWING = "foreshadowing"
    THEME = "theme"
    MOTIF = "motif"


class ReviewVerdict(StrEnum):
    """专家对单个抽检目标给出的结论。"""

    UNREVIEWED = "unreviewed"
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNSUPPORTED = "unsupported"
    UNCERTAIN = "uncertain"


class AutomaticSignal(StrictModel):
    """不等同于正确率的确定性质量代理信号。"""

    code: str
    name: str
    value: float | None = Field(default=None, ge=0, le=1)
    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    status: SignalStatus
    message: str
    references: list[str]
    limitation: str


class ReviewContext(StrictModel):
    """供评审者核对叙事结论的完整场景上下文。"""

    scene_id: str
    heading: str
    source_span: SourceSpan
    text: str


class ReviewTarget(StrictModel):
    """经过风险分层抽取的单个叙事评测目标。"""

    id: str
    kind: ReviewTargetKind
    title: str
    claim: str
    scene_ids: list[str]
    dimensions: list[QualityDimension]
    evidence: list[Evidence]
    contexts: list[ReviewContext]
    risk_score: int = Field(ge=0)
    risk_reasons: list[str]
    selection_reason: Literal["anchor", "risk"]


class DimensionRating(StrictModel):
    """一个人工评测维度的五分制结果。"""

    dimension: QualityDimension
    score: int | None = Field(default=None, ge=1, le=5)
    comment: str = ""


class ReviewResponse(StrictModel):
    """评审者对一个抽检目标填写的判断。"""

    target_id: str
    verdict: ReviewVerdict = ReviewVerdict.UNREVIEWED
    ratings: list[DimensionRating]
    notes: str = ""
    proposed_correction: str | None = None


class HumanReviewAnswers(StrictModel):
    """可从外部填写并重新导入的人工评测答案。"""

    schema_version: str = "1.0"
    analysis_fingerprint: str
    rubric_version: str
    reviewer: str = ""
    responses: list[ReviewResponse]

    @model_validator(mode="after")
    def _validate_unique_responses(self) -> Self:
        """拒绝同一评测目标出现多份互相覆盖的答案。"""
        ids = [response.target_id for response in self.responses]
        if len(ids) != len(set(ids)):
            raise ValueError("人工评测答案包含重复 target_id。")
        return self


class HumanReviewSheet(StrictModel):
    """绑定分析指纹、抽检目标和当前人工答案的评测表。"""

    schema_version: str = "1.0"
    analysis_fingerprint: str
    rubric_version: str
    reviewer: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    targets: list[ReviewTarget]
    responses: list[ReviewResponse]

    @model_validator(mode="after")
    def _validate_target_responses(self) -> Self:
        """要求目标与响应 ID 唯一且一一对应。"""
        target_ids = [target.id for target in self.targets]
        response_ids = [response.target_id for response in self.responses]
        if len(target_ids) != len(set(target_ids)):
            raise ValueError("人工评测表包含重复目标。")
        if len(response_ids) != len(set(response_ids)) or set(target_ids) != set(response_ids):
            raise ValueError("人工评测响应必须与抽检目标一一对应。")
        return self


class ReviewSummary(StrictModel):
    """人工评测完成度、结论分布和维度均分。"""

    reviewed_count: int = Field(ge=0)
    target_count: int = Field(ge=0)
    coverage: float = Field(ge=0, le=1)
    verdict_counts: dict[ReviewVerdict, int]
    dimension_averages: dict[QualityDimension, float]
    flagged_target_ids: list[str]


class SemanticQualityReport(StrictModel):
    """自动代理信号与人工评测严格分离的语义质量报告。"""

    schema_version: str = "1.0"
    analysis_fingerprint: str
    source_fingerprint: str
    generated_at: datetime = Field(default_factory=utc_now)
    rubric_version: str
    automatic_signals: list[AutomaticSignal]
    human_review: HumanReviewSheet
    human_summary: ReviewSummary
    limitations: list[str]
