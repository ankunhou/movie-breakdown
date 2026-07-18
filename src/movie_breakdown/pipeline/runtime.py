"""流水线阶段状态转换、缓存读取和进度通知。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from pydantic import BaseModel

from movie_breakdown.domain.base import StageStatus, utc_now
from movie_breakdown.domain.run import Artifact, RunManifest
from movie_breakdown.domain.scene_analysis import TokenUsage
from movie_breakdown.infrastructure.fingerprint import content_fingerprint
from movie_breakdown.pipeline.definitions import StageSpec, get_stage

ProgressCallback = Callable[[str, str], None]
StageLookup = Callable[[str], StageSpec]


class StageRepository(Protocol):
    """阶段运行时所需的最小持久化仓库接口。"""

    def read_artifact[T: BaseModel](self, name: str, data_type: type[T]) -> Artifact[T]:
        """读取严格类型的阶段产物。

        Args:
            name: 不含扩展名的产物名称。
            data_type: 产物业务数据类型。

        Returns:
            带追溯元数据的阶段产物。
        """
        ...

    def save_manifest(self, manifest: RunManifest) -> None:
        """原子保存阶段运行清单。

        Args:
            manifest: 待保存的运行清单。
        """
        ...


class PipelineStageError(RuntimeError):
    """流水线阶段失败且状态已经安全持久化。"""


class StageRuntime:
    """集中管理 manifest 状态机与阶段级缓存。

    Attributes:
        store: 当前项目的持久化仓库。
        manifest: 当前运行清单的内存实例。
        progress: 可选的阶段进度回调。
    """

    def __init__(
        self,
        store: StageRepository,
        manifest: RunManifest,
        progress: ProgressCallback | None = None,
        stage_lookup: StageLookup = get_stage,
    ) -> None:
        """创建一次流水线运行所需的状态管理器。

        Args:
            store: 当前项目存储。
            manifest: 从项目加载的运行清单。
            progress: 接收阶段名和中文状态消息的回调。
            stage_lookup: 按名称返回阶段定义的注册表函数。
        """
        self.store = store
        self.manifest = manifest
        self.progress = progress
        self.stage_lookup = stage_lookup

    def load_cached[T: BaseModel](
        self,
        stage_name: str,
        artifact_name: str,
        data_type: type[T],
        cache_key: str,
    ) -> Artifact[T] | None:
        """读取缓存键和阶段版本均匹配的严格类型产物。

        Args:
            stage_name: 负责生成产物的阶段名称。
            artifact_name: 不含扩展名的产物名称。
            data_type: 产物业务数据的 Pydantic 类型。
            cache_key: 当前输入与配置对应的预期缓存键。

        Returns:
            可安全复用的产物；不存在、损坏或过期时返回空。
        """
        try:
            artifact = self.store.read_artifact(artifact_name, data_type)
        except (OSError, ValueError):
            return None
        record = self.manifest.stages[stage_name]
        current_version = self.stage_lookup(stage_name).version
        data_fingerprint = content_fingerprint(artifact.data)
        if (
            artifact.metadata.cache_key == cache_key
            and artifact.metadata.stage_version == current_version
            and artifact.metadata.artifact_fingerprint == data_fingerprint
        ):
            record.version = current_version
            self.cached(stage_name, cache_key, data_fingerprint)
            return artifact
        record.status = StageStatus.STALE
        record.error = "缓存内容指纹、输入指纹或阶段版本已过期。"
        self.store.save_manifest(self.manifest)
        return None

    def start(self, stage_name: str, cache_key: str, message: str) -> None:
        """把阶段转换为运行中并立即持久化。

        Args:
            stage_name: 待启动阶段名称。
            cache_key: 当前阶段预期缓存键。
            message: 展示给用户的中文进度消息。
        """
        record = self.manifest.stages[stage_name]
        record.version = self.stage_lookup(stage_name).version
        record.status = StageStatus.RUNNING
        record.cache_key = cache_key
        record.started_at = utc_now()
        record.finished_at = None
        record.error = None
        self.store.save_manifest(self.manifest)
        self.notify(stage_name, message)

    def success(
        self,
        stage_name: str,
        cache_key: str,
        artifact_fingerprint: str,
        usage: TokenUsage | None = None,
    ) -> None:
        """把阶段转换为成功并记录产物指纹与用量。

        Args:
            stage_name: 已完成阶段名称。
            cache_key: 本次完成时使用的缓存键。
            artifact_fingerprint: 阶段最终业务产物指纹。
            usage: 阶段累计 token 用量。
        """
        record = self.manifest.stages[stage_name]
        record.version = self.stage_lookup(stage_name).version
        record.status = StageStatus.SUCCESS
        record.cache_key = cache_key
        record.artifact_fingerprint = artifact_fingerprint
        record.finished_at = utc_now()
        record.error = None
        record.usage = usage or TokenUsage()
        self.store.save_manifest(self.manifest)
        self.notify(stage_name, "完成")

    def cached(self, stage_name: str, cache_key: str, artifact_fingerprint: str) -> None:
        """把阶段记为成功缓存命中。

        Args:
            stage_name: 命中缓存的阶段名称。
            cache_key: 已匹配的预期缓存键。
            artifact_fingerprint: 被复用的产物指纹。
        """
        record = self.manifest.stages[stage_name]
        record.version = self.stage_lookup(stage_name).version
        record.status = StageStatus.SUCCESS
        record.cache_key = cache_key
        record.artifact_fingerprint = artifact_fingerprint
        record.error = None
        self.store.save_manifest(self.manifest)
        self.notify(stage_name, "命中有效缓存")

    def fail(
        self,
        stage_name: str,
        error: Exception | str,
        usage: TokenUsage | None = None,
    ) -> None:
        """把失败原因写入运行清单并保留已有产物。

        Args:
            stage_name: 失败阶段名称。
            error: 异常实例或可展示的失败说明。
            usage: 失败前已经实际发生的累计 token 用量；省略时保留原记录。
        """
        record = self.manifest.stages[stage_name]
        record.status = StageStatus.FAILED
        record.finished_at = utc_now()
        record.error = str(error)[:4000]
        if usage is not None:
            record.usage = usage
        self.store.save_manifest(self.manifest)
        self.notify(stage_name, f"失败：{record.error}")

    def notify(self, stage_name: str, message: str) -> None:
        """在配置回调时发送阶段进度。

        Args:
            stage_name: 当前阶段名称。
            message: 简短中文状态消息。
        """
        if self.progress:
            self.progress(stage_name, message)
