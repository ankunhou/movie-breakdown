"""人物小传声明、聚合与可恢复记录模型。"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Self

from pydantic import Field, field_validator, model_validator

from movie_breakdown.domain.base import Confidence, StageStatus, StrictModel
from movie_breakdown.domain.scene_analysis import Evidence, TokenUsage


class BiographyClaimBasis(StrEnum):
    """人物小传声明与剧本原文之间的认识论关系。"""

    OBSERVED = "observed"
    REPORTED = "reported"
    INFERRED = "inferred"


class BiographyClaimCategory(StrEnum):
    """人物小传声明覆盖的稳定内容分类。"""

    OVERVIEW = "overview"
    IDENTITY = "identity"
    AGE = "age"
    APPEARANCE = "appearance"
    OCCUPATION = "occupation"
    RELATIONSHIP = "relationship"
    BACKSTORY = "backstory"
    BEHAVIOR = "behavior"
    GOAL = "goal"
    MOTIVATION = "motivation"
    BELIEF = "belief"
    TRAIT = "trait"
    FEAR = "fear"
    SECRET = "secret"
    CHANGE = "change"
    SPEECH_STYLE = "speech_style"
    DRAMATIC_FUNCTION = "dramatic_function"


class CharacterBiographyClaim(StrictModel):
    """一条区分观察、转述和推断并携带证据的人物声明。"""

    id: str = Field(min_length=1, max_length=80)
    category: BiographyClaimCategory
    statement: str = Field(min_length=1, max_length=600)
    basis: BiographyClaimBasis
    attribution: str | None = Field(default=None, min_length=1, max_length=120)
    confidence: Confidence
    rationale: str | None = Field(default=None, min_length=1, max_length=400)
    alternatives: list[str] = Field(max_length=3)
    evidence: list[Evidence] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_basis_contract(self) -> Self:
        """确保转述有归属、推断有依据，且其他声明不冒充转述。

        Returns:
            通过认识论字段互斥约束的当前声明。

        Raises:
            ValueError: 声明依据与归属或推断说明不匹配。
        """
        if self.basis == BiographyClaimBasis.REPORTED and self.attribution is None:
            raise ValueError("转述声明必须提供 attribution。")
        if self.basis != BiographyClaimBasis.REPORTED and self.attribution is not None:
            raise ValueError("只有转述声明可以提供 attribution。")
        if self.basis == BiographyClaimBasis.INFERRED and self.rationale is None:
            raise ValueError("推断声明必须提供 rationale。")
        return self


class CharacterBiography(StrictModel):
    """基于有限剧本上下文生成的单个人物小传。"""

    schema_version: str = "1.0"
    character_id: str = Field(min_length=1)
    context_scene_ids: list[str] = Field(max_length=8)
    summary: CharacterBiographyClaim
    claims: list[CharacterBiographyClaim] = Field(max_length=12)
    unknowns: list[BiographyClaimCategory]
    key_relationship_ids: list[str] = Field(max_length=6)
    representative_lines: list[Evidence] = Field(max_length=3)

    @model_validator(mode="before")
    @classmethod
    def _normalize_partially_known_categories(cls, value: Any) -> Any:
        """迁移曾允许同一分类既有声明又标记未知的旧产物。

        ``unknowns`` 当前只能表达整个分类未提供，无法描述分类中的局部缺口。
        因此只要普通声明已经覆盖该分类，就确定性移除对应未知标记。

        Args:
            value: 待进入严格字段校验的人物小传原始值。

        Returns:
            已消除声明分类与未知分类重叠的浅拷贝；其他输入原样返回。
        """
        if not isinstance(value, dict):
            return value
        claims = value.get("claims")
        unknowns = value.get("unknowns")
        if not isinstance(claims, list) or not isinstance(unknowns, list):
            return value
        claimed_categories = {claim.get("category") for claim in claims if isinstance(claim, dict)}
        normalized = [item for item in unknowns if item not in claimed_categories]
        return value if normalized == unknowns else {**value, "unknowns": normalized}

    @field_validator("context_scene_ids", "key_relationship_ids")
    @classmethod
    def _validate_unique_ids(cls, values: list[str]) -> list[str]:
        """拒绝小传中的重复场景或关系引用。

        Args:
            values: 待检查的 ID 列表。

        Returns:
            保持原有顺序的唯一 ID 列表。

        Raises:
            ValueError: 列表包含重复 ID。
        """
        if len(values) != len(set(values)):
            raise ValueError("人物小传引用的 ID 不得重复。")
        return values

    @field_validator("unknowns")
    @classmethod
    def _validate_unknown_categories(
        cls,
        values: list[BiographyClaimCategory],
    ) -> list[BiographyClaimCategory]:
        """拒绝重复未知分类和不可缺失的概览分类。

        Args:
            values: 模型明确标记为剧本未说明的分类。

        Returns:
            通过约束的未知分类列表。

        Raises:
            ValueError: 分类重复或包含人物概览。
        """
        if BiographyClaimCategory.OVERVIEW in values:
            raise ValueError("人物概览不能标记为 unknown。")
        if len(values) != len(set(values)):
            raise ValueError("unknowns 中的分类不得重复。")
        return values

    @model_validator(mode="after")
    def _validate_claims(self) -> Self:
        """确保概览位置、声明 ID 与未知分类彼此一致。

        Returns:
            通过声明集合约束的当前人物小传。

        Raises:
            ValueError: 概览分类、声明 ID 或未知分类发生冲突。
        """
        if self.summary.category != BiographyClaimCategory.OVERVIEW:
            raise ValueError("summary 的 category 必须为 overview。")
        if any(claim.category == BiographyClaimCategory.OVERVIEW for claim in self.claims):
            raise ValueError("普通 claims 不能重复人物概览。")
        claim_ids = [self.summary.id, *(claim.id for claim in self.claims)]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("人物小传中的 claim id 必须唯一。")
        claimed_categories = {claim.category for claim in self.claims}
        overlap = claimed_categories.intersection(self.unknowns)
        if overlap:
            values = "、".join(sorted(item.value for item in overlap))
            raise ValueError(f"已声明分类不能同时标记为 unknown：{values}")
        return self


class BiographyCatalog(StrictModel):
    """一次剧本分析生成的全部核心人物小传目录。"""

    schema_version: str = "1.0"
    biographies: list[CharacterBiography]

    @model_validator(mode="after")
    def _validate_unique_characters(self) -> Self:
        """确保每个人物在目录中最多只有一份小传。

        Returns:
            人物引用唯一的当前目录。

        Raises:
            ValueError: 多份小传引用同一人物。
        """
        character_ids = [item.character_id for item in self.biographies]
        if len(character_ids) != len(set(character_ids)):
            raise ValueError("人物小传目录中的 character_id 必须唯一。")
        return self


class BiographyAnalysisRecord(StrictModel):
    """支持按人物缓存、失败记录和断点恢复的小传分析记录。"""

    schema_version: str = "1.0"
    character_id: str
    cache_key: str
    status: StageStatus
    biography: CharacterBiography | None = None
    error: str | None = None
    attempts: int = Field(default=0, ge=0)
    usage: TokenUsage = Field(default_factory=TokenUsage)
