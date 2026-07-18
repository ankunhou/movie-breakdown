"""制片元素拆解共享的枚举、数量和证据约束。"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from movie_breakdown.domain.base import Confidence, StrictModel
from movie_breakdown.domain.scene_analysis import Evidence


class InteriorExterior(StrEnum):
    """场景在制片语义上的内外景分类。"""

    INTERIOR = "interior"
    EXTERIOR = "exterior"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class DayPhase(StrEnum):
    """剧本能够明确支持的拍摄时段。"""

    DAWN = "dawn"
    DAY = "day"
    DUSK = "dusk"
    NIGHT = "night"
    CONTINUOUS = "continuous"
    UNKNOWN = "unknown"


class RequirementBasis(StrEnum):
    """制片需求来自原文明示还是分析推断。"""

    EXPLICIT = "explicit"
    INFERRED = "inferred"


class QuantityBasis(StrEnum):
    """制片数量的确定程度。"""

    EXACT = "exact"
    MINIMUM = "minimum"
    RANGE = "range"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class ProductionElementKind(StrEnum):
    """需要跨场景汇总的制片元素大类。"""

    COSTUME = "costume"
    HAIR_MAKEUP = "hair_makeup"
    HAND_PROP = "hand_prop"
    SET_DRESSING = "set_dressing"
    VEHICLE = "vehicle"
    ANIMAL = "animal"
    STUNT_ACTION = "stunt_action"
    PRACTICAL_EFFECT = "practical_effect"
    VFX = "vfx"
    SPECIAL_EQUIPMENT = "special_equipment"
    SOUND_MUSIC = "sound_music"
    OTHER = "other"


class CastAppearanceKind(StrEnum):
    """演员或角色在场景中的出现方式。"""

    ON_SCREEN = "on_screen"
    VOICE_ONLY = "voice_only"
    PHOTO_OR_RECORDING = "photo_or_recording"
    DOUBLE = "double"
    OTHER = "other"


class ComplexityLevel(StrEnum):
    """单场制片复杂度等级。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ComplexityDimension(StrEnum):
    """导致场景制片复杂度上升的稳定维度。"""

    CAST_CROWD = "cast_crowd"
    LOCATION_LOGISTICS = "location_logistics"
    COSTUME_MAKEUP_PROPS = "costume_makeup_props"
    ACTION_SAFETY = "action_safety"
    EFFECTS_TECHNICAL = "effects_technical"
    CONTINUITY_SCHEDULE = "continuity_schedule"


class QuantityEstimate(StrictModel):
    """保留数量上下界、单位和确定程度的估算。"""

    minimum: int | None = Field(default=None, ge=0)
    maximum: int | None = Field(default=None, ge=0)
    unit: str = Field(min_length=1, max_length=30)
    basis: QuantityBasis

    @model_validator(mode="after")
    def _validate_bounds(self) -> Self:
        """拒绝与数量依据不一致的上下界组合。"""
        if self.minimum is not None and self.maximum is not None and self.maximum < self.minimum:
            raise ValueError("数量上界不能小于下界。")
        if self.basis == QuantityBasis.EXACT:
            if self.minimum is None or self.minimum != self.maximum:
                raise ValueError("精确数量必须提供相同的上下界。")
        elif self.basis == QuantityBasis.MINIMUM:
            if self.minimum is None or self.maximum is not None:
                raise ValueError("最低数量只能提供下界。")
        elif self.basis == QuantityBasis.RANGE:
            if self.minimum is None or self.maximum is None:
                raise ValueError("范围数量必须同时提供上下界。")
        elif self.basis == QuantityBasis.ESTIMATED:
            if self.minimum is None:
                raise ValueError("估算数量至少需要提供下界。")
        elif self.minimum is not None or self.maximum is not None:
            raise ValueError("未知数量不能提供上下界。")
        return self


class EvidenceBackedRequirement(StrictModel):
    """所有逐场制片需求共享的证据和推断边界。"""

    basis: RequirementBasis
    confidence: Confidence
    rationale: str | None = Field(default=None, max_length=500)
    evidence: list[Evidence] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def _validate_inference_rationale(self) -> Self:
        """要求所有推断性需求披露可审查的推断依据。"""
        if self.basis == RequirementBasis.INFERRED and not (self.rationale or "").strip():
            raise ValueError("推断性制片需求必须填写 rationale。")
        return self
