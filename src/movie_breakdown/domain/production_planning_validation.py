"""制片规划分级校验问题与报告模型。"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from movie_breakdown.domain.base import Severity, StrictModel


class ProductionReadinessLevel(StrEnum):
    """制片规划从结构草稿到可进入专业执行流程的级别。"""

    DRAFT_VALID = "draft_valid"
    CATALOG_READY = "catalog_ready"
    SHOOT_READY = "shoot_ready"


class ProductionPlanningIssue(StrictModel):
    """一条带明确阻断级别的规划校验问题。"""

    severity: Severity
    code: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1, max_length=1000)
    reference: str | None = Field(default=None, max_length=240)
    blocks_levels: list[ProductionReadinessLevel] = Field(default_factory=list)


class ProductionPlanningValidationReport(StrictModel):
    """规划结构、目录可用度和专业安全准备度的分级结论。"""

    schema_version: str = "1.0"
    plan_fingerprint: str = Field(min_length=1, max_length=128)
    draft_valid: bool
    catalog_ready: bool
    shoot_ready: bool
    scene_count: int = Field(ge=0)
    shooting_unit_count: int = Field(ge=0)
    resource_class_count: int = Field(ge=0)
    entity_count: int = Field(ge=0)
    unresolved_entity_count: int = Field(ge=0)
    unknown_unit_count: int = Field(ge=0)
    hazard_count: int = Field(ge=0)
    qualified_approval_count: int = Field(ge=0)
    issues: list[ProductionPlanningIssue]

    @model_validator(mode="after")
    def _validate_readiness(self) -> Self:
        """保证较高准备度不可能绕过较低层级。"""
        if self.shoot_ready and not self.catalog_ready:
            raise ValueError("shoot_ready 必须先满足 catalog_ready。")
        if self.catalog_ready and not self.draft_valid:
            raise ValueError("catalog_ready 必须先满足 draft_valid。")
        return self
