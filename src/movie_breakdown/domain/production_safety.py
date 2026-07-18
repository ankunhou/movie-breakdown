"""制片高危候选、专业复核范围和批准状态模型。"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from movie_breakdown.domain.base import StrictModel
from movie_breakdown.domain.scene_analysis import Evidence


class HazardKind(StrEnum):
    """必须进入专业复核的拍摄风险类别。"""

    FIREARM = "firearm"
    PYROTECHNICS = "pyrotechnics"
    OPEN_FLAME = "open_flame"
    BLADE_COMBAT = "blade_combat"
    VEHICLE_ACTION = "vehicle_action"
    ANIMAL_ACTION = "animal_action"
    HEIGHT_RIGGING = "height_rigging"
    WATER_DROWNING = "water_drowning"
    CROWD_ACTION = "crowd_action"
    MINOR_PERFORMER = "minor_performer"
    EXTREME_ENVIRONMENT = "extreme_environment"
    PROSTHETIC_GORE = "prosthetic_gore"
    OTHER = "other"


class SafetyRiskLevel(StrEnum):
    """高危候选在进入专业评估前的保守风险级别。"""

    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SafetyDecision(StrEnum):
    """一个固定风险范围当前得到的复核结论。"""

    REQUIRES_REVISION = "requires_revision"
    REQUIRES_PROFESSIONAL_REVIEW = "requires_professional_review"
    APPROVED_WITH_CONTROLS = "approved_with_controls"
    APPROVED = "approved"
    REJECTED = "rejected"
    NOT_APPLICABLE = "not_applicable"


CLOSED_SAFETY_DECISIONS = frozenset(
    {
        SafetyDecision.APPROVED,
        SafetyDecision.APPROVED_WITH_CONTROLS,
        SafetyDecision.NOT_APPLICABLE,
    }
)


class SafetyReviewerKind(StrEnum):
    """安全评审者身份的可验证程度。"""

    AI_SIMULATED = "ai_simulated"
    UNVERIFIED_HUMAN = "unverified_human"
    QUALIFIED_PROFESSIONAL = "qualified_professional"


class SafetyHazard(StrictModel):
    """由确定性规则产生且模型或人工修正无权静默删除的风险候选。"""

    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$", max_length=160)
    scene_id: str
    shooting_unit_id: str
    kind: HazardKind
    risk_level: SafetyRiskLevel
    trigger_rule_ids: list[str] = Field(min_length=1, max_length=20)
    occurrence_ids: list[str] = Field(min_length=1, max_length=100)
    required_reviewer_roles: list[str] = Field(min_length=1, max_length=12)
    description: str = Field(min_length=1, max_length=800)
    mandatory_controls: list[str] = Field(min_length=1, max_length=20)
    prohibited_methods: list[str] = Field(default_factory=list, max_length=20)
    scope_fingerprint: str = Field(min_length=1, max_length=128)
    evidence: list[Evidence] = Field(min_length=1, max_length=24)

    @model_validator(mode="after")
    def _validate_unique_values(self) -> Self:
        """拒绝重复触发规则、资源和复核角色。"""
        for label, values in (
            ("触发规则", self.trigger_rule_ids),
            ("资源出现项", self.occurrence_ids),
            ("复核角色", self.required_reviewer_roles),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"安全候选包含重复{label}。")
        return self


class SafetyApproval(StrictModel):
    """评审者对一个不可变风险范围给出的独立安全决定。"""

    hazard_id: str
    scope_fingerprint: str = Field(min_length=1, max_length=128)
    reviewer: str = Field(min_length=1, max_length=120)
    reviewer_role: str = Field(min_length=1, max_length=120)
    reviewer_kind: SafetyReviewerKind
    decision: SafetyDecision
    reason: str = Field(min_length=1, max_length=1200)
    required_controls: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def _prevent_unqualified_approval(self) -> Self:
        """禁止 AI 或未核实人员把风险标记为已批准。"""
        if (
            self.decision in CLOSED_SAFETY_DECISIONS
            and self.reviewer_kind != SafetyReviewerKind.QUALIFIED_PROFESSIONAL
        ):
            raise ValueError("只有经确认的合格专业人员可以批准或排除高危候选。")
        if self.decision == SafetyDecision.APPROVED_WITH_CONTROLS and not self.required_controls:
            raise ValueError("附条件批准必须列出 required_controls。")
        return self


class SafetyMethodDecision(StrictModel):
    """人工明确否决危险默认实现并指定安全替代边界的审计决定。"""

    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$", max_length=160)
    scene_id: str
    analysis_fingerprint: str = Field(min_length=1, max_length=128)
    prohibited_method: str = Field(min_length=1, max_length=300)
    replacement_policy: str = Field(min_length=1, max_length=1200)
    reviewer: str = Field(min_length=1, max_length=120)
    reviewer_kind: SafetyReviewerKind
    rationale: str = Field(min_length=1, max_length=1200)
