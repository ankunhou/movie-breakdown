"""全人物分级档案及其可解释重要度信号。"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, field_validator, model_validator

from movie_breakdown.domain.base import StrictModel
from movie_breakdown.domain.scene_analysis import Evidence


class CharacterDossierTier(StrEnum):
    """人物档案的叙事重要度层级。"""

    CORE = "core"
    SUPPORTING = "supporting"
    FUNCTIONAL = "functional"
    BACKGROUND = "background"


class CharacterImportanceSignals(StrictModel):
    """解释人物分级所使用的确定性统计信号。"""

    scene_count: int = Field(ge=0)
    event_count: int = Field(ge=0)
    relationship_count: int = Field(ge=0)
    core_relationship_count: int = Field(ge=0)
    has_character_arc: bool
    top_frequency_rank: int | None = Field(default=None, ge=1)


class CharacterDossier(StrictModel):
    """从全局叙事产物确定性生成的单个人物档案。

    档案覆盖所有归一人物；核心人物另有模型生成的完整小传，其他人物通过
    实体描述、出场、事件和关系引用形成无需额外模型调用的分级资料卡。

    Attributes:
        character_id: 指向全局人物实体的稳定 ID。
        name: 全局实体归一后的标准人物名。
        tier: 核心、重要配角、功能人物或背景人物层级。
        summary: 复用全局人物实体的已验证描述。
        aliases: 已归一到该人物的其他称谓。
        first_scene_id: 人物首次出现场景。
        scene_ids: 人物全部已知出现场景。
        event_ids: 以该人物为参与者的全局事件。
        relationship_ids: 以该人物为任一端点的全局关系。
        signals: 用于分级的可审计统计信号。
        classification_reasons: 面向用户解释层级判断的中文原因。
        evidence: 全局人物实体已有的原文证据。
    """

    schema_version: str = "1.0"
    character_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    tier: CharacterDossierTier
    summary: str = Field(min_length=1)
    aliases: list[str]
    first_scene_id: str | None = None
    scene_ids: list[str]
    event_ids: list[str]
    relationship_ids: list[str]
    signals: CharacterImportanceSignals
    classification_reasons: list[str] = Field(min_length=1)
    evidence: list[Evidence]

    @field_validator("aliases", "scene_ids", "event_ids", "relationship_ids")
    @classmethod
    def _validate_unique_values(cls, values: list[str]) -> list[str]:
        """拒绝档案索引字段中的重复值。"""
        if len(values) != len(set(values)):
            raise ValueError("人物档案索引字段不得包含重复值。")
        return values

    @model_validator(mode="after")
    def _validate_signal_counts(self) -> Self:
        """确保可解释信号与档案引用数量一致。"""
        expected = (
            len(self.scene_ids),
            len(self.event_ids),
            len(self.relationship_ids),
        )
        actual = (
            self.signals.scene_count,
            self.signals.event_count,
            self.signals.relationship_count,
        )
        if actual != expected:
            raise ValueError("人物档案统计信号与引用数量不一致。")
        if self.signals.core_relationship_count > self.signals.relationship_count:
            raise ValueError("连接核心人物的关系数不能大于人物关系总数。")
        if self.scene_ids and self.first_scene_id not in self.scene_ids:
            raise ValueError("人物档案首次场景必须属于人物有效场景。")
        if not self.scene_ids and self.first_scene_id is not None:
            raise ValueError("没有有效场景的人物不能设置首次场景。")
        return self


class CharacterDossierCatalog(StrictModel):
    """按全局人物顺序保存的全人物分级档案目录。"""

    schema_version: str = "1.0"
    policy_version: str = Field(min_length=1)
    scene_recurring_threshold: int = Field(ge=1)
    event_recurring_threshold: int = Field(ge=1)
    dossiers: list[CharacterDossier]

    @model_validator(mode="after")
    def _validate_unique_characters(self) -> Self:
        """确保同一人物最多只有一份分级档案。"""
        character_ids = [item.character_id for item in self.dossiers]
        if len(character_ids) != len(set(character_ids)):
            raise ValueError("人物档案目录中的 character_id 必须唯一。")
        return self
