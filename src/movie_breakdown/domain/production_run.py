"""独立制片流水线的配置和项目描述。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from movie_breakdown.domain.base import StrictModel, utc_now
from movie_breakdown.domain.run import ProjectConfig


class ProductionConfig(StrictModel):
    """只包含会影响制片模型结果与缓存的配置。"""

    schema_version: str = "1.0"
    contract_version: str = "1.0"
    model: str = "deepseek-v4-pro"
    thinking_enabled: bool = True
    reasoning_effort: Literal["high", "max"] = "high"
    max_retries: int = Field(default=2, ge=0, le=5)
    concurrency: int = Field(default=4, ge=1, le=32)

    @classmethod
    def from_project_config(cls, config: ProjectConfig) -> ProductionConfig:
        """从叙事项目复制模型参数并排除无关配置。

        Args:
            config: 现有项目的完整配置。

        Returns:
            不包含叙事框架和格式识别策略的制片配置。
        """
        return cls(
            model=config.model,
            thinking_enabled=config.thinking_enabled,
            reasoning_effort=config.reasoning_effort,
            max_retries=config.max_retries,
            concurrency=config.concurrency,
        )


class ProductionProject(StrictModel):
    """持久化在 `production/config.json` 的独立作用域描述。"""

    schema_version: str = "1.0"
    parent_project_id: str
    config: ProductionConfig
    created_at: datetime = Field(default_factory=utc_now)
