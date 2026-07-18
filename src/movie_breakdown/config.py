"""应用环境变量与项目配置。"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from movie_breakdown.domain.run import ProjectConfig


class AppSettings(BaseSettings):
    """从环境变量和可选 `.env` 文件读取运行配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="MOVIE_BREAKDOWN_",
        extra="ignore",
    )

    deepseek_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="DEEPSEEK_API_KEY",
    )
    model: str = "deepseek-v4-pro"
    max_retries: int = Field(default=2, ge=0, le=5)
    concurrency: int = Field(default=4, ge=1, le=32)
    thinking_enabled: bool = True
    reasoning_effort: str = "high"
    request_timeout_seconds: float = Field(default=600, gt=0)

    def to_project_config(
        self,
        structure_framework: str = "three-act",
        format_detection: str = "auto",
    ) -> ProjectConfig:
        """生成会被项目持久化并参与缓存计算的配置。

        Args:
            structure_framework: 叙事结构分析框架名称。
            format_detection: 场景格式识别策略。

        Returns:
            已完成类型校验的项目配置。
        """
        return ProjectConfig(
            model=self.model,
            structure_framework=structure_framework,  # type: ignore[arg-type]
            format_detection=format_detection,  # type: ignore[arg-type]
            thinking_enabled=self.thinking_enabled,
            reasoning_effort=self.reasoning_effort,  # type: ignore[arg-type]
            max_retries=self.max_retries,
            concurrency=self.concurrency,
        )


@lru_cache
def get_settings() -> AppSettings:
    """返回进程内复用的应用配置。

    Returns:
        从环境变量和 `.env` 加载的应用配置。
    """
    return AppSettings()
