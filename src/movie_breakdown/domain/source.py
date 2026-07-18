"""剧本源文档、规范化文本和场景模型。"""

from __future__ import annotations

from pathlib import PurePath
from typing import Literal, Self

from pydantic import Field, computed_field, model_validator

from movie_breakdown.domain.base import Confidence, StrictModel


class SourceSpan(StrictModel):
    """原始剧本文本中的连续位置范围。"""

    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _validate_order(self) -> Self:
        """拒绝结束位置早于开始位置的范围。"""
        if self.line_end < self.line_start:
            raise ValueError("结束行号不能早于开始行号。")
        if self.page_start and self.page_end and self.page_end < self.page_start:
            raise ValueError("结束页码不能早于开始页码。")
        return self


class SourceLine(StrictModel):
    """保留原始页码映射的规范化文本行。"""

    number: int = Field(ge=1)
    text: str
    page_number: int | None = Field(default=None, ge=1)
    page_line_number: int | None = Field(default=None, ge=1)


class SourceDocument(StrictModel):
    """导入源文件的不可变描述信息。"""

    original_name: str
    media_type: str
    fingerprint: str
    size_bytes: int = Field(ge=0)
    page_count: int | None = Field(default=None, ge=1)
    line_count: int = Field(ge=0)

    @computed_field
    @property
    def suffix(self) -> str:
        """返回统一为小写的源文件扩展名。

        Returns:
            包含前导点号的小写扩展名。
        """

        return PurePath(self.original_name).suffix.lower()


class NormalizedDocument(StrictModel):
    """经过换行和空白规范化的剧本文档。"""

    schema_version: str = "1.0"
    source: SourceDocument
    title: str
    lines: list[SourceLine]

    @computed_field
    @property
    def text(self) -> str:
        """把规范化文本行连接成完整剧本文本。

        Returns:
            使用换行符连接的完整规范化文本。
        """

        return "\n".join(line.text for line in self.lines)


class Scene(StrictModel):
    """具有稳定顺序、来源范围和内容指纹的场景。"""

    schema_version: str = "1.0"
    id: str
    ordinal: int = Field(ge=1)
    heading: str
    location_hint: str | None = None
    time_hint: str | None = None
    interior_exterior_hint: str | None = None
    character_hints: list[str] = Field(default_factory=list)
    text: str
    source_span: SourceSpan
    content_fingerprint: str


class SceneFormatProfile(StrictModel):
    """模型识别出的场景标题格式和受控正则候选。"""

    schema_version: str = "1.0"
    format_name: str
    scene_start_regex: str = Field(min_length=2, max_length=300)
    heading_examples: list[str] = Field(min_length=1, max_length=12)
    confidence: Confidence
    rationale: str


class Screenplay(StrictModel):
    """完成场景切分后的剧本。"""

    schema_version: str = "1.0"
    title: str
    source_fingerprint: str
    scenes: list[Scene]
    split_method: Literal["builtin", "model", "fallback"] = "builtin"
    format_profile: SceneFormatProfile | None = None
