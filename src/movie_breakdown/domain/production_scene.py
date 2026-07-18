"""单场制片元素、复杂度和可恢复记录模型。"""

from __future__ import annotations

from typing import Self

from pydantic import Field, model_validator

from movie_breakdown.domain.base import StageStatus, StrictModel
from movie_breakdown.domain.production_common import (
    CastAppearanceKind,
    ComplexityDimension,
    ComplexityLevel,
    DayPhase,
    EvidenceBackedRequirement,
    InteriorExterior,
    ProductionElementKind,
    QuantityEstimate,
)
from movie_breakdown.domain.scene_analysis import Evidence, TokenUsage

REQUIREMENT_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"


class SceneSetting(EvidenceBackedRequirement):
    """从场景标题和正文提取的地点、内外景及时间信息。"""

    raw_heading: str = Field(min_length=1, max_length=300)
    location_name: str = Field(min_length=1, max_length=200)
    sub_location: str | None = Field(default=None, max_length=200)
    interior_exterior: InteriorExterior
    time_of_day: DayPhase
    raw_time_label: str | None = Field(default=None, max_length=80)
    weather_requirements: list[str] = Field(default_factory=list, max_length=12)


class CastRequirement(EvidenceBackedRequirement):
    """单场中需要演员、替身或声音出演的角色需求。"""

    id: str = Field(pattern=REQUIREMENT_ID_PATTERN, max_length=100)
    character_name: str = Field(min_length=1, max_length=120)
    character_id: str | None = Field(default=None, max_length=120)
    appearance_kind: CastAppearanceKind
    performance_notes: list[str] = Field(default_factory=list, max_length=12)


class BackgroundRequirement(EvidenceBackedRequirement):
    """群众演员、氛围演员及其技能和数量需求。"""

    id: str = Field(pattern=REQUIREMENT_ID_PATTERN, max_length=100)
    group_name: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=500)
    quantity: QuantityEstimate
    special_skills: list[str] = Field(default_factory=list, max_length=12)


class ProductionElement(EvidenceBackedRequirement):
    """服化道、车辆动物、动作特效或特殊设备需求。"""

    id: str = Field(pattern=REQUIREMENT_ID_PATTERN, max_length=100)
    kind: ProductionElementKind
    name: str = Field(min_length=1, max_length=160)
    subtype: str | None = Field(default=None, max_length=100)
    description: str = Field(min_length=1, max_length=600)
    quantity: QuantityEstimate
    associated_cast_ids: list[str] = Field(default_factory=list, max_length=30)
    state_or_continuity: str | None = Field(default=None, max_length=400)
    special_requirements: list[str] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def _validate_other_subtype(self) -> Self:
        """要求兜底分类保留可追查的原始子类型。"""
        if self.kind == ProductionElementKind.OTHER and not (self.subtype or "").strip():
            raise ValueError("other 类制片元素必须填写 subtype。")
        return self


class ComplexityFactor(StrictModel):
    """有证据支撑的单项复杂度来源。"""

    dimension: ComplexityDimension
    score: int = Field(ge=1, le=5)
    rationale: str = Field(min_length=1, max_length=500)
    related_requirement_ids: list[str] = Field(default_factory=list, max_length=30)
    evidence: list[Evidence] = Field(min_length=1, max_length=8)


class SceneProductionComplexity(StrictModel):
    """单场综合复杂度及各维度证据。"""

    score: int = Field(ge=1, le=5)
    level: ComplexityLevel
    factors: list[ComplexityFactor] = Field(default_factory=list, max_length=12)
    scheduling_notes: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def _validate_level(self) -> Self:
        """固定分数到等级的映射，避免模型自相矛盾。"""
        expected = {
            1: ComplexityLevel.LOW,
            2: ComplexityLevel.MEDIUM,
            3: ComplexityLevel.MEDIUM,
            4: ComplexityLevel.HIGH,
            5: ComplexityLevel.CRITICAL,
        }[self.score]
        if self.level != expected:
            raise ValueError(f"复杂度分数 {self.score} 必须对应 {expected.value}。")
        if self.score >= 4 and not self.factors:
            raise ValueError("高复杂度场景必须提供至少一个复杂度因素。")
        return self


class ProductionUncertainty(StrictModel):
    """剧本未明确但会影响制片判断的信息缺口。"""

    subject: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=500)
    impact: str = Field(min_length=1, max_length=500)


class SceneProductionAnalysis(StrictModel):
    """一个场景的完整制片元素拆解。"""

    schema_version: str = "1.0"
    scene_id: str
    setting: SceneSetting
    cast: list[CastRequirement]
    background: list[BackgroundRequirement]
    elements: list[ProductionElement]
    complexity: SceneProductionComplexity
    uncertainties: list[ProductionUncertainty]


class SceneProductionRecord(StrictModel):
    """支持按场景缓存、失败恢复和用量累计的制片记录。"""

    schema_version: str = "1.0"
    scene_id: str
    cache_key: str
    status: StageStatus
    analysis: SceneProductionAnalysis | None = None
    error: str | None = None
    attempts: int = Field(default=0, ge=0)
    usage: TokenUsage = Field(default_factory=TokenUsage)
