"""逐场叙事分析的结构化领域模型。"""

from __future__ import annotations

from pydantic import Field

from movie_breakdown.domain.base import Confidence, StageStatus, StrictModel
from movie_breakdown.domain.source import SourceSpan


class Evidence(StrictModel):
    """支撑分析结论的剧本证据。"""

    scene_id: str
    source_span: SourceSpan
    excerpt: str = Field(max_length=300)
    confidence: Confidence


class SceneEventDraft(StrictModel):
    """逐场阶段提取、尚未全局归一的事件。"""

    summary: str
    participants: list[str]
    causes: list[str]
    consequences: list[str]
    evidence: list[Evidence]


class SceneAnalysis(StrictModel):
    """单个场景的完整叙事分析。"""

    schema_version: str = "1.0"
    scene_id: str
    summary: str
    character_names: list[str]
    objectives: list[str]
    obstacles: list[str]
    core_conflict: str | None
    events: list[SceneEventDraft]
    state_before: list[str]
    state_after: list[str]
    revelations: list[str]
    suspense: list[str]
    foreshadowing_candidates: list[str]
    plot_functions: list[str]
    uncertainties: list[str]
    evidence: list[Evidence]


class TokenUsage(StrictModel):
    """一次或一组模型调用的 token 用量。"""

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class SceneAnalysisRecord(StrictModel):
    """可恢复的单场分析记录。"""

    schema_version: str = "1.0"
    scene_id: str
    cache_key: str
    status: StageStatus
    analysis: SceneAnalysis | None = None
    error: str | None = None
    attempts: int = Field(default=0, ge=0)
    usage: TokenUsage = Field(default_factory=TokenUsage)
