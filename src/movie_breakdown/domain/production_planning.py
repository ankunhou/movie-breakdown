"""场内拍摄单元、资源身份和数量语义的制片规划模型。"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from movie_breakdown.domain.base import StrictModel
from movie_breakdown.domain.production_common import (
    DayPhase,
    InteriorExterior,
    ProductionElementKind,
)
from movie_breakdown.domain.production_safety import (
    SafetyApproval,
    SafetyHazard,
    SafetyMethodDecision,
)
from movie_breakdown.domain.scene_analysis import Evidence
from movie_breakdown.domain.source import SourceSpan

PLANNING_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._/-]*$"


class ShootingUnitSplitReason(StrEnum):
    """一个剧本场景需要拆成多个场内拍摄单元的原因。"""

    SCENE_START = "scene_start"
    LOCATION_CHANGE = "location_change"
    TIME_CHANGE = "time_change"
    ERA_CHANGE = "era_change"
    MONTAGE_BEAT = "montage_beat"
    ACTION_PHASE = "action_phase"
    INSERT = "insert"
    TITLE_CARD = "title_card"
    OTHER = "other"


class ProductionResourceKind(StrEnum):
    """规划层统一追踪的资源来源类别。"""

    LOCATION = "location"
    CAST = "cast"
    BACKGROUND = "background"
    ELEMENT = "element"


class IdentityScope(StrEnum):
    """资源是可替换类别、连续性实体还是状态群体。"""

    FUNGIBLE = "fungible"
    CONTINUITY = "continuity"
    COHORT = "cohort"


class ResolutionStatus(StrEnum):
    """跨场资源身份的确认状态。"""

    UNRESOLVED = "unresolved"
    CONFIRMED = "confirmed"
    CONFLICTED = "conflicted"


class NormalizationBasis(StrEnum):
    """资源规范化结论由本地规则、AI 模拟评审还是真人确认产生。"""

    DETERMINISTIC = "deterministic"
    REVIEW_CONFIRMED = "review_confirmed"
    AI_REVIEWED = "ai_reviewed"
    HUMAN_CONFIRMED = "human_confirmed"


class UnitCode(StrEnum):
    """允许参加确定性比较的标准制片单位。"""

    PERSON = "person"
    GROUP = "group"
    ANIMAL = "animal"
    VEHICLE = "vehicle"
    ITEM = "item"
    SET = "set"
    PAIR = "pair"
    COSTUME = "costume"
    WEAPON = "weapon"
    DEVICE = "device"
    EVENT = "event"
    SHOT = "shot"
    LOCATION = "location"
    UNKNOWN = "unknown"


class QuantityRole(StrEnum):
    """数量在总量、状态子集或画面规模中的语义。"""

    TOTAL = "total"
    SUBSET = "subset"
    PER_MEMBER = "per_member"
    EVENT = "event"
    SCREEN_SCALE = "screen_scale"


class QuantityProvenance(StrEnum):
    """数量事实的来源，独立于制片需求本身是否显性。"""

    EXPLICIT_TEXT = "explicit_text"
    DETERMINISTIC_DERIVED = "deterministic_derived"
    UNKNOWN = "unknown"


class PlannedQuantityPurpose(StrEnum):
    """只能由人工决定的制作计划数量用途。"""

    PRACTICAL = "practical"
    DIGITAL = "digital"
    BACKUP = "backup"
    PROCUREMENT = "procurement"


class QuantityBounds(StrictModel):
    """数量的闭合下界和可选开放上界。"""

    minimum: int | None = Field(default=None, ge=0)
    maximum: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _validate_order(self) -> Self:
        """拒绝上界小于下界或只有上界的数量。"""
        if self.minimum is None and self.maximum is not None:
            raise ValueError("数量不能只提供上界。")
        if self.minimum is not None and self.maximum is not None and self.maximum < self.minimum:
            raise ValueError("数量上界不能小于下界。")
        return self


class ShootingUnit(StrictModel):
    """场景内部可独立安排地点、时段和资源的最小拍摄语义单元。"""

    id: str = Field(pattern=PLANNING_ID_PATTERN, max_length=160)
    scene_id: str
    ordinal: int = Field(ge=1)
    label: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=1000)
    source_span: SourceSpan
    split_reasons: list[ShootingUnitSplitReason] = Field(min_length=1, max_length=12)
    location_name: str = Field(min_length=1, max_length=200)
    sub_location: str | None = Field(default=None, max_length=200)
    interior_exterior: InteriorExterior
    time_of_day: DayPhase
    raw_time_label: str | None = Field(default=None, max_length=80)
    occurrence_ids: list[str] = Field(default_factory=list, max_length=300)
    evidence: list[Evidence] = Field(min_length=1, max_length=16)


class ProductionResourceClass(StrictModel):
    """规范名称、标准单位和身份范围一致的一类可调度资源。"""

    id: str = Field(pattern=PLANNING_ID_PATTERN, max_length=160)
    kind: ProductionResourceKind
    element_kind: ProductionElementKind | None = None
    canonical_name: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=60)
    canonical_unit: UnitCode
    identity_scope: IdentityScope
    basis: NormalizationBasis

    @model_validator(mode="after")
    def _validate_element_kind(self) -> Self:
        """要求只有元素资源携带元素子类别。"""
        if (self.kind == ProductionResourceKind.ELEMENT) != (self.element_kind is not None):
            raise ValueError("只有 element 资源必须填写 element_kind。")
        return self


class ProductionEntity(StrictModel):
    """在多个出现项之间保持同一身份和连续性状态的具体资源。"""

    id: str = Field(pattern=PLANNING_ID_PATTERN, max_length=160)
    resource_class_ids: list[str] = Field(min_length=1, max_length=100)
    canonical_name: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=60)
    status: ResolutionStatus
    occurrence_ids: list[str] = Field(min_length=1, max_length=300)
    redirect_from_ids: list[str] = Field(default_factory=list, max_length=100)
    basis: NormalizationBasis
    notes: list[str] = Field(default_factory=list, max_length=30)


class QuantityFact(StrictModel):
    """剧本能够支持的数量事实、单位及父子关系。"""

    id: str = Field(pattern=PLANNING_ID_PATTERN, max_length=180)
    occurrence_id: str
    bounds: QuantityBounds
    unit: UnitCode
    raw_unit: str = Field(min_length=1, max_length=40)
    raw_expression: str | None = Field(default=None, max_length=200)
    role: QuantityRole
    parent_quantity_id: str | None = None
    state: str | None = Field(default=None, max_length=160)
    exclusive_group: str | None = Field(default=None, max_length=120)
    provenance: QuantityProvenance
    derived_from_ids: list[str] = Field(default_factory=list, max_length=20)
    evidence: list[Evidence] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def _validate_semantics(self) -> Self:
        """拒绝缺少父项的子集及缺少来源的派生数量。"""
        if self.role in {QuantityRole.SUBSET, QuantityRole.PER_MEMBER}:
            if self.parent_quantity_id is None:
                raise ValueError("subset 和 per_member 数量必须填写 parent_quantity_id。")
        elif self.parent_quantity_id is not None:
            raise ValueError("只有 subset 和 per_member 数量可以填写 parent_quantity_id。")
        if self.provenance == QuantityProvenance.DETERMINISTIC_DERIVED:
            if not self.derived_from_ids:
                raise ValueError("确定性派生数量必须填写 derived_from_ids。")
        elif self.derived_from_ids:
            raise ValueError("非派生数量不能填写 derived_from_ids。")
        if self.provenance == QuantityProvenance.UNKNOWN and (
            self.bounds.minimum is not None or self.bounds.maximum is not None
        ):
            raise ValueError("未知来源数量不能携带可执行上下界。")
        return self


class PlannedQuantity(StrictModel):
    """与剧本事实分离、经人工决定的实拍或采购数量。"""

    id: str = Field(pattern=PLANNING_ID_PATTERN, max_length=180)
    occurrence_id: str
    purpose: PlannedQuantityPurpose
    bounds: QuantityBounds
    unit: UnitCode
    reviewer: str = Field(min_length=1, max_length=120)
    decision_id: str = Field(min_length=1, max_length=160)
    input_fingerprint: str = Field(min_length=1, max_length=128)
    rationale: str = Field(min_length=1, max_length=800)


class ResourceOccurrence(StrictModel):
    """一个资源在具体场景和拍摄单元中的可追溯出现。"""

    id: str = Field(pattern=PLANNING_ID_PATTERN, max_length=180)
    scene_id: str
    shooting_unit_id: str
    source_requirement_id: str
    resource_class_id: str
    entity_id: str | None = None
    resolution_status: ResolutionStatus
    quantity_fact_ids: list[str] = Field(default_factory=list, max_length=20)
    state_before: str | None = Field(default=None, max_length=400)
    state_after: str | None = Field(default=None, max_length=400)
    evidence: list[Evidence] = Field(min_length=1, max_length=16)


class ProductionPlan(StrictModel):
    """绑定基础拆解指纹的完整本地制片规划与安全复核状态。"""

    schema_version: str = "1.0"
    source_fingerprint: str = Field(min_length=1, max_length=128)
    base_breakdown_fingerprint: str = Field(min_length=1, max_length=128)
    shooting_units: list[ShootingUnit]
    resource_classes: list[ProductionResourceClass]
    entities: list[ProductionEntity]
    occurrences: list[ResourceOccurrence]
    quantity_facts: list[QuantityFact]
    planned_quantities: list[PlannedQuantity]
    safety_hazards: list[SafetyHazard]
    safety_method_decisions: list[SafetyMethodDecision]
    safety_approvals: list[SafetyApproval]
