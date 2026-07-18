"""无需生成文本即可执行的 CLI 环境诊断。"""

from __future__ import annotations

import importlib.metadata
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from openai import OpenAI

from movie_breakdown.config import AppSettings
from movie_breakdown.domain.doctor import CheckStatus, DoctorCheck, DoctorReport


class DoctorService:
    """检查 Python、依赖、目录、密钥和模型可用性。

    Attributes:
        settings: 当前应用环境配置。
    """

    def __init__(self, settings: AppSettings) -> None:
        """创建环境诊断服务。

        Args:
            settings: 从环境变量加载的应用配置。
        """
        self.settings = settings

    def run(self, work_directory: Path, *, online: bool = True) -> DoctorReport:
        """执行本地检查和可选 DeepSeek 在线检查。

        Args:
            work_directory: 用于验证项目写权限的目录。
            online: 是否调用模型列表接口检查配置模型。

        Returns:
            不包含任何密钥内容的诊断报告。
        """
        checks = [
            self._check_python(),
            self._check_dependencies(),
            self._check_directory(work_directory),
            self._check_api_key(),
        ]
        checks.append(self._check_model() if online else self._skipped_model())
        ok = not any(check.status == CheckStatus.FAIL for check in checks)
        return DoctorReport(ok=ok, checks=checks)

    def _check_python(self) -> DoctorCheck:
        """检查 Python 是否满足项目最低版本。"""
        version = sys.version_info
        supported = version >= (3, 12)
        return DoctorCheck(
            name="Python",
            status=CheckStatus.PASS if supported else CheckStatus.FAIL,
            message=f"{version.major}.{version.minor}.{version.micro}",
        )

    def _check_dependencies(self) -> DoctorCheck:
        """检查所有关键运行依赖是否可以读取版本。"""
        packages = ("agno", "pydantic", "typer", "pypdf", "openai")
        try:
            versions = [f"{name}={importlib.metadata.version(name)}" for name in packages]
        except importlib.metadata.PackageNotFoundError as error:
            return DoctorCheck(
                name="运行依赖",
                status=CheckStatus.FAIL,
                message=f"缺少依赖：{error.name}",
            )
        return DoctorCheck(
            name="运行依赖",
            status=CheckStatus.PASS,
            message="，".join(versions),
        )

    def _check_directory(self, path: Path) -> DoctorCheck:
        """通过实际创建临时文件验证目录写权限。"""
        try:
            directory = path.resolve()
            directory.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix=".movie-breakdown-", dir=directory)
            os.close(descriptor)
            os.unlink(temporary)
            return DoctorCheck(
                name="目录权限",
                status=CheckStatus.PASS,
                message=str(directory),
            )
        except OSError as error:
            return DoctorCheck(
                name="目录权限",
                status=CheckStatus.FAIL,
                message=str(error),
            )

    def _check_api_key(self) -> DoctorCheck:
        """只判断 API Key 是否存在，绝不输出密钥内容。"""
        configured = self.settings.deepseek_api_key is not None
        return DoctorCheck(
            name="DeepSeek API Key",
            status=CheckStatus.PASS if configured else CheckStatus.FAIL,
            message="已配置" if configured else "未设置 DEEPSEEK_API_KEY",
        )

    def _check_model(self) -> DoctorCheck:
        """通过 DeepSeek 模型列表接口检查目标模型。"""
        if self.settings.deepseek_api_key is None:
            return DoctorCheck(
                name="DeepSeek 模型",
                status=CheckStatus.FAIL,
                message="缺少 API Key，无法在线检查。",
            )
        try:
            client = self._create_client()
            response = client.models.list()
            model_ids = {item.id for item in response.data}
            available = self.settings.model in model_ids
            return DoctorCheck(
                name="DeepSeek 模型",
                status=CheckStatus.PASS if available else CheckStatus.FAIL,
                message=(
                    f"{self.settings.model} 可用"
                    if available
                    else f"模型列表中没有 {self.settings.model}"
                ),
            )
        except Exception as error:
            return DoctorCheck(
                name="DeepSeek 模型",
                status=CheckStatus.FAIL,
                message=f"连接失败：{type(error).__name__}: {error}",
            )

    def _create_client(self) -> Any:
        """构造只用于健康检查的 OpenAI 兼容客户端。"""
        api_key = self.settings.deepseek_api_key
        assert api_key is not None
        return OpenAI(
            api_key=api_key.get_secret_value(),
            base_url="https://api.deepseek.com",
            timeout=min(self.settings.request_timeout_seconds, 30),
            max_retries=1,
        )

    def _skipped_model(self) -> DoctorCheck:
        """生成用户主动跳过在线检查的诊断项。"""
        return DoctorCheck(
            name="DeepSeek 模型",
            status=CheckStatus.SKIPPED,
            message="已通过 --no-online 跳过。",
        )
