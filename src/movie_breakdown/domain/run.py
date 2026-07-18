"""项目配置、流水线状态、产物元数据和校验报告。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from movie_breakdown.domain.base import Severity, StageStatus, StrictModel, utc_now
from movie_breakdown.domain.scene_analysis import TokenUsage


class ProjectConfig(StrictModel):
    """会影响分析结果和缓存的项目配置。"""

    schema_version: str = "1.0"
    model: str = "deepseek-v4-pro"
    structure_framework: Literal["three-act", "sequence", "save-the-cat", "none"] = "three-act"
    format_detection: Literal["auto", "local", "model"] = "auto"
    thinking_enabled: bool = True
    reasoning_effort: Literal["high", "max"] = "high"
    max_retries: int = Field(default=2, ge=0, le=5)
    concurrency: int = Field(default=4, ge=1, le=32)


class ProjectDocument(StrictModel):
    """持久化在项目根目录的项目描述。"""

    schema_version: str = "1.0"
    id: str
    title: str
    source_relative_path: str
    config: ProjectConfig
    created_at: datetime = Field(default_factory=utc_now)


class StageRecord(StrictModel):
    """一个流水线阶段的当前执行状态。"""

    name: str
    version: str
    status: StageStatus = StageStatus.PENDING
    cache_key: str | None = None
    artifact_fingerprint: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)


class RunManifest(StrictModel):
    """项目所有流水线阶段的执行清单。"""

    schema_version: str = "1.0"
    project_id: str
    stages: dict[str, StageRecord]
    updated_at: datetime = Field(default_factory=utc_now)


class ArtifactMetadata(StrictModel):
    """使产物可追溯并支持缓存判定的元数据。"""

    schema_version: str = "1.0"
    stage: str
    stage_version: str
    cache_key: str
    artifact_fingerprint: str
    source_fingerprint: str
    upstream_fingerprints: list[str]
    prompt_fingerprint: str | None = None
    schema_fingerprint: str
    model: str | None = None
    model_parameters: dict[str, str | int | bool] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    usage: TokenUsage = Field(default_factory=TokenUsage)


class Artifact[T](StrictModel):
    """把业务数据和产物元数据封装在同一文件中。"""

    metadata: ArtifactMetadata
    data: T


class ValidationIssue(StrictModel):
    """本地一致性校验发现的单个问题。"""

    severity: Severity
    code: str
    message: str
    reference: str | None = None


class ValidationReport(StrictModel):
    """无需调用模型即可生成的一致性校验报告。"""

    schema_version: str = "1.0"
    valid: bool
    scene_count: int = Field(ge=0)
    analyzed_scene_count: int = Field(ge=0)
    coverage: float = Field(ge=0, le=1)
    issues: list[ValidationIssue]
