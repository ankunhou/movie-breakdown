"""确定性汇总后的全局制片目录与正式拆解模型。"""

from __future__ import annotations

from pydantic import Field

from movie_breakdown.domain.base import StrictModel
from movie_breakdown.domain.production_common import (
    CastAppearanceKind,
    ComplexityDimension,
    ComplexityLevel,
    DayPhase,
    InteriorExterior,
    ProductionElementKind,
    QuantityEstimate,
)
from movie_breakdown.domain.production_scene import SceneProductionAnalysis
from movie_breakdown.domain.run import ValidationIssue
from movie_breakdown.domain.scene_analysis import Evidence


class ProductionLocation(StrictModel):
    """跨场景合并后的拍摄地点需求。"""

    id: str
    name: str
    aliases: list[str]
    scene_ids: list[str]
    interior_exterior_modes: list[InteriorExterior]
    time_of_day_modes: list[DayPhase]
    weather_requirements: list[str]
    evidence: list[Evidence] = Field(min_length=1)


class ProductionCast(StrictModel):
    """跨场景汇总的角色出演需求。"""

    id: str
    name: str
    aliases: list[str]
    character_id: str | None
    scene_ids: list[str]
    appearance_kinds: list[CastAppearanceKind]
    source_requirement_ids: list[str]
    evidence: list[Evidence] = Field(min_length=1)


class ProductionBackgroundGroup(StrictModel):
    """跨场景汇总的群众演员组需求。"""

    id: str
    name: str
    descriptions: list[str]
    scene_ids: list[str]
    source_requirement_ids: list[str]
    peak_quantity: QuantityEstimate
    special_skills: list[str]
    evidence: list[Evidence] = Field(min_length=1)


class AggregatedProductionElement(StrictModel):
    """按类别和规范化名称聚合的制片元素。"""

    id: str
    kind: ProductionElementKind
    name: str
    aliases: list[str]
    subtypes: list[str]
    scene_ids: list[str]
    source_requirement_ids: list[str]
    peak_quantity: QuantityEstimate
    continuity_notes: list[str]
    special_requirements: list[str]
    evidence: list[Evidence] = Field(min_length=1)


class ComplexScene(StrictModel):
    """需要重点排期或专业评估的高复杂度场景索引。"""

    scene_id: str
    score: int = Field(ge=4, le=5)
    level: ComplexityLevel
    dimensions: list[ComplexityDimension]


class GlobalProductionCatalog(StrictModel):
    """由逐场结果确定性生成的全剧制片目录。"""

    schema_version: str = "1.0"
    locations: list[ProductionLocation]
    cast: list[ProductionCast]
    background: list[ProductionBackgroundGroup]
    elements: list[AggregatedProductionElement]
    complex_scenes: list[ComplexScene]


class ProductionValidationReport(StrictModel):
    """独立制片流水线的覆盖率和一致性校验报告。"""

    schema_version: str = "1.0"
    valid: bool
    scene_count: int = Field(ge=0)
    analyzed_scene_count: int = Field(ge=0)
    coverage: float = Field(ge=0, le=1)
    catalog_item_count: int = Field(ge=0)
    issues: list[ValidationIssue]


class ProductionBreakdown(StrictModel):
    """独立于叙事报告的正式制片元素拆解。"""

    schema_version: str = "1.0"
    title: str
    source_fingerprint: str
    scenes: list[SceneProductionAnalysis]
    catalog: GlobalProductionCatalog
    validation: ProductionValidationReport
