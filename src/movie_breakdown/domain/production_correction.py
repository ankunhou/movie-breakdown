"""制片规划人工修正的可辨识操作、集合与审计回执。"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from movie_breakdown.domain.base import StrictModel
from movie_breakdown.domain.production_planning import (
    PlannedQuantity,
    ProductionEntity,
    ProductionResourceClass,
    QuantityFact,
    ShootingUnit,
)
from movie_breakdown.domain.production_review import ProductionReviewerKind
from movie_breakdown.domain.production_safety import SafetyApproval, SafetyMethodDecision
from movie_breakdown.domain.scene_analysis import Evidence


class ProductionCorrectionBase(StrictModel):
    """所有制片规划修正共享的评审、旧值与证据绑定。"""

    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$", max_length=180)
    review_target_ids: list[str] = Field(min_length=1, max_length=1000)
    expected_value_fingerprint: str = Field(min_length=1, max_length=128)
    rationale: str = Field(min_length=1, max_length=1600)
    evidence: list[Evidence] = Field(min_length=1, max_length=24)

    @field_validator("review_target_ids")
    @classmethod
    def _validate_unique_review_targets(cls, value: list[str]) -> list[str]:
        """拒绝同一修正重复绑定一个评审目标。

        Args:
            value: 全量参与当前修正的评审目标 ID。

        Returns:
            保持输入顺序的唯一目标 ID 列表。

        Raises:
            ValueError: 目标 ID 在同一修正中重复。
        """
        if len(value) != len(set(value)):
            raise ValueError("制片修正不能重复绑定同一评审目标。")
        return value


class ReplaceShootingUnitsCorrection(ProductionCorrectionBase):
    """原子替换一个剧本场景的完整拍摄单元及资源分配。"""

    kind: Literal["replace_shooting_units"] = "replace_shooting_units"
    scene_id: str
    replacement: list[ShootingUnit] = Field(min_length=1, max_length=60)


class ReplaceEntityRegistryCorrection(ProductionCorrectionBase):
    """原子替换完整跨场实体注册表。"""

    kind: Literal["replace_entity_registry"] = "replace_entity_registry"
    replacement: list[ProductionEntity]


class ReplaceResourceClassesCorrection(ProductionCorrectionBase):
    """原子替换完整资源类别表并保留稳定引用 ID。"""

    kind: Literal["replace_resource_classes"] = "replace_resource_classes"
    replacement: list[ProductionResourceClass] = Field(min_length=1)


class ReplaceSceneQuantitiesCorrection(ProductionCorrectionBase):
    """原子替换一个场景的全部剧本数量事实及父子关系。"""

    kind: Literal["replace_scene_quantities"] = "replace_scene_quantities"
    scene_id: str
    replacement: list[QuantityFact]


class ReplacePlannedQuantitiesCorrection(ProductionCorrectionBase):
    """原子替换全部人工决定的实拍、数字扩充、采购与备份数量。"""

    kind: Literal["replace_planned_quantities"] = "replace_planned_quantities"
    replacement: list[PlannedQuantity]


class ReplaceSafetyMethodsCorrection(ProductionCorrectionBase):
    """原子替换全部危险默认实现的人工否决决定。"""

    kind: Literal["replace_safety_methods"] = "replace_safety_methods"
    replacement: list[SafetyMethodDecision]


class ReplaceSafetyApprovalsCorrection(ProductionCorrectionBase):
    """原子替换全部独立专业安全复核决定。"""

    kind: Literal["replace_safety_approvals"] = "replace_safety_approvals"
    replacement: list[SafetyApproval]


ProductionCorrectionOperation = Annotated[
    ReplaceShootingUnitsCorrection
    | ReplaceEntityRegistryCorrection
    | ReplaceResourceClassesCorrection
    | ReplaceSceneQuantitiesCorrection
    | ReplacePlannedQuantitiesCorrection
    | ReplaceSafetyMethodsCorrection
    | ReplaceSafetyApprovalsCorrection,
    Field(discriminator="kind"),
]


class ProductionCorrectionSet(StrictModel):
    """绑定基础规划、完整目标集和专家答案的累计修正快照。"""

    schema_version: str = "1.0"
    source_fingerprint: str = Field(min_length=1, max_length=128)
    base_plan_fingerprint: str = Field(min_length=1, max_length=128)
    target_set_fingerprint: str = Field(min_length=1, max_length=128)
    review_answers_fingerprint: str = Field(min_length=1, max_length=128)
    rubric_version: str = Field(min_length=1, max_length=40)
    safety_policy_version: str = Field(min_length=1, max_length=40)
    reviewer: str = Field(min_length=1, max_length=120)
    reviewer_kind: ProductionReviewerKind
    corrections: list[ProductionCorrectionOperation] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_unique_corrections(self) -> Self:
        """拒绝重复操作 ID 及同一作用域的互相覆盖。"""
        identifiers = [item.id for item in self.corrections]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("制片修正集合包含重复 correction id。")
        scopes = [_correction_scope(item) for item in self.corrections]
        if len(scopes) != len(set(scopes)):
            raise ValueError("制片修正集合对同一作用域包含多条操作。")
        return self


class ProductionCorrectionReceipt(StrictModel):
    """原子应用完整累计修正后的确定性审计回执。"""

    schema_version: str = "1.0"
    source_fingerprint: str
    base_plan_fingerprint: str
    corrected_plan_fingerprint: str
    target_set_fingerprint: str
    correction_set_fingerprint: str
    review_answers_fingerprint: str
    rubric_version: str
    safety_policy_version: str
    reviewer: str
    reviewer_kind: ProductionReviewerKind
    applied_correction_ids: list[str]
    applied_count: int = Field(ge=1)

    @model_validator(mode="after")
    def _validate_count(self) -> Self:
        """确保回执数量与唯一修正 ID 列表一致。"""
        if len(self.applied_correction_ids) != len(set(self.applied_correction_ids)):
            raise ValueError("制片修正回执包含重复 correction id。")
        if self.applied_count != len(self.applied_correction_ids):
            raise ValueError("applied_count 必须等于已应用制片修正数量。")
        return self


def _correction_scope(operation: ProductionCorrectionOperation) -> tuple[str, str]:
    """返回用于冲突检测的稳定修正作用域。"""
    scene_id = getattr(operation, "scene_id", "global")
    return operation.kind, scene_id
