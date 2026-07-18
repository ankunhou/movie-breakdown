"""人工叙事修正的严格输入契约与审计回执。"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, field_validator, model_validator

from movie_breakdown.domain.base import StrictModel
from movie_breakdown.domain.scene_analysis import Evidence


class CorrectionField(StrEnum):
    """人工修正允许替换的叙事文本字段。"""

    SCENE_SUMMARY = "scene_summary"
    EVENT_SUMMARY = "event_summary"
    ACT_SUMMARY = "act_summary"
    ACT_TURNING_POINT = "act_turning_point"
    BEAT_SUMMARY = "beat_summary"
    PLOT_SUMMARY = "plot_summary"
    FORESHADOW_DESCRIPTION = "foreshadow_description"
    ARC_INITIAL_STATE = "arc_initial_state"
    ARC_DESIRE = "arc_desire"
    ARC_NEED = "arc_need"
    ARC_FINAL_STATE = "arc_final_state"
    RELATION_DEVELOPMENT = "relation_development"
    BIOGRAPHY_SUMMARY = "biography_summary"
    BIOGRAPHY_CLAIM_STATEMENT = "biography_claim_statement"
    BIOGRAPHY_CLAIM_RATIONALE = "biography_claim_rationale"
    THEME = "theme"
    MOTIF = "motif"


class NarrativeCorrection(StrictModel):
    """一条绑定评审目标、旧值指纹与剧本证据的文本修正。"""

    id: str = Field(min_length=1, max_length=120)
    review_target_id: str = Field(min_length=1, max_length=240)
    field: CorrectionField
    object_id: str = Field(min_length=1, max_length=240)
    expected_value_fingerprint: str = Field(min_length=1, max_length=128)
    replacement: str | None
    rationale: str = Field(min_length=1, max_length=1200)
    evidence: list[Evidence] = Field(min_length=1, max_length=12)

    @field_validator("replacement")
    @classmethod
    def _validate_replacement(cls, value: str | None) -> str | None:
        """拒绝只含空白的替换文本，同时允许清空可空字段。

        Args:
            value: 专家给出的替换文本或显式空值。

        Returns:
            保留原样的有效替换值。

        Raises:
            ValueError: 替换文本只包含空白字符。
        """
        if value is not None and not value.strip():
            raise ValueError("replacement 不能只包含空白字符。")
        return value


class CorrectionSet(StrictModel):
    """绑定一次分析、评审标准与专家答案的原子修正集合。"""

    schema_version: str = "1.0"
    source_fingerprint: str = Field(min_length=1, max_length=128)
    base_analysis_fingerprint: str = Field(min_length=1, max_length=128)
    rubric_version: str = Field(min_length=1, max_length=40)
    review_answers_fingerprint: str = Field(min_length=1, max_length=128)
    reviewer: str = Field(min_length=1, max_length=120)
    corrections: list[NarrativeCorrection] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_unique_ids(self) -> Self:
        """拒绝同一修正集合中的重复修正 ID。

        Returns:
            修正 ID 全部唯一的当前集合。

        Raises:
            ValueError: 两条或更多修正使用相同 ID。
        """
        identifiers = [item.id for item in self.corrections]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("人工修正集合包含重复 correction id。")
        return self


class CorrectionReceipt(StrictModel):
    """一次原子应用成功后用于复核内容身份的确定性回执。

    ``corrected_analysis_fingerprint`` 对应尚未嵌入回执元数据的修正后叙事内容，
    从而避免回执指纹对自身形成循环依赖。
    """

    schema_version: str = "1.0"
    source_fingerprint: str
    base_analysis_fingerprint: str
    corrected_analysis_fingerprint: str
    correction_set_fingerprint: str
    rubric_version: str
    review_answers_fingerprint: str
    reviewer: str
    applied_correction_ids: list[str]
    applied_count: int = Field(ge=1)

    @model_validator(mode="after")
    def _validate_applied_ids(self) -> Self:
        """确保回执数量与唯一修正 ID 列表一致。

        Returns:
            数量和 ID 列表一致的当前回执。

        Raises:
            ValueError: 回执 ID 重复或数量不一致。
        """
        if len(self.applied_correction_ids) != len(set(self.applied_correction_ids)):
            raise ValueError("修正回执包含重复 correction id。")
        if self.applied_count != len(self.applied_correction_ids):
            raise ValueError("applied_count 必须等于已应用修正 ID 的数量。")
        return self
