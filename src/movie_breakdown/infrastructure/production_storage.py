"""项目内 `production` 命名空间的独立原子存储。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel

from movie_breakdown.domain.base import StageStatus, utc_now
from movie_breakdown.domain.production_run import ProductionConfig, ProductionProject
from movie_breakdown.domain.run import Artifact, ProjectDocument, RunManifest, StageRecord
from movie_breakdown.infrastructure.storage import ProjectStore
from movie_breakdown.pipeline.production_definitions import production_stage_versions


class ProductionNotInitializedError(ValueError):
    """表示项目尚未创建独立制片作用域。"""


class ProductionAlreadyInitializedError(ValueError):
    """表示项目已经存在独立制片作用域。"""


class ProductionStore:
    """组合主项目存储并隔离制片配置、清单、产物和导出。

    Attributes:
        project_store: 只读提供主项目和共享场景产物的仓库。
        root: 独立制片作用域根目录。
        artifacts_dir: 制片结构化产物目录。
        exports_dir: 制片用户可见导出目录。
    """

    def __init__(self, project_store: ProjectStore) -> None:
        """创建尚不产生目录的制片仓库。

        Args:
            project_store: 已有剧本拆解项目的主仓库。
        """
        self.project_store = project_store
        self.root = project_store.root / "production"
        self.project_path = self.root / "config.json"
        self.manifest_path = self.root / "manifest.json"
        self.artifacts_dir = self.root / "artifacts"
        self.exports_dir = self.root / "exports"

    def initialize(
        self,
        parent_project: ProjectDocument,
        config: ProductionConfig,
    ) -> tuple[ProductionProject, RunManifest]:
        """创建制片配置和完全独立的阶段清单。

        Args:
            parent_project: 已验证的主项目描述。
            config: 固化到制片作用域的模型配置。

        Returns:
            新建的制片项目描述和运行清单。

        Raises:
            ProductionAlreadyInitializedError: 制片作用域已经存在。
        """
        if self.project_path.exists() or self.manifest_path.exists():
            raise ProductionAlreadyInitializedError("制片元素拆解已经初始化，请使用 resume。")
        project = ProductionProject(parent_project_id=parent_project.id, config=config)
        manifest = RunManifest(
            project_id=parent_project.id,
            stages={
                name: StageRecord(name=name, version=version, status=StageStatus.PENDING)
                for name, version in production_stage_versions().items()
            },
        )
        self.project_store.write_model(self.project_path, project)
        self.save_manifest(manifest)
        return project, manifest

    def load_project(self) -> ProductionProject:
        """读取独立制片项目描述。

        Returns:
            通过严格类型校验的制片项目。

        Raises:
            ProductionNotInitializedError: 制片作用域尚未初始化。
        """
        if not self.project_path.is_file():
            raise ProductionNotInitializedError("制片元素拆解尚未初始化。")
        return self.project_store.read_model(self.project_path, ProductionProject)

    def load_manifest(self) -> RunManifest:
        """读取独立制片阶段清单。

        Returns:
            通过严格类型校验的运行清单。

        Raises:
            ProductionNotInitializedError: 清单不存在。
        """
        if not self.manifest_path.is_file():
            raise ProductionNotInitializedError("制片元素拆解缺少运行清单。")
        return self.project_store.read_model(self.manifest_path, RunManifest)

    def save_manifest(self, manifest: RunManifest) -> None:
        """更新时间并原子保存制片运行清单。

        Args:
            manifest: 待保存的制片清单。
        """
        manifest.updated_at = utc_now()
        self.project_store.write_model(self.manifest_path, manifest)

    def artifact_path(self, name: str) -> Path:
        """返回制片 JSON 产物绝对路径。

        Args:
            name: 不含扩展名的稳定产物名称。

        Returns:
            `production/artifacts` 下的 JSON 路径。
        """
        return self.artifacts_dir / f"{name}.json"

    def write_artifact[T: BaseModel](self, name: str, artifact: Artifact[T]) -> None:
        """原子写入严格制片产物。

        Args:
            name: 不含扩展名的产物名称。
            artifact: 带完整追溯元数据的业务产物。
        """
        self.project_store.write_model(self.artifact_path(name), artifact)

    def read_artifact[T: BaseModel](self, name: str, data_type: type[T]) -> Artifact[T]:
        """读取并严格校验制片产物。

        Args:
            name: 不含扩展名的产物名称。
            data_type: 业务数据的 Pydantic 类型。

        Returns:
            带完整追溯元数据的制片产物。
        """
        return self.project_store.read_model(self.artifact_path(name), Artifact[data_type])

    def write_jsonl(self, name: str, records: list[BaseModel]) -> None:
        """按顺序原子写入制片逐场记录。

        Args:
            name: 不含扩展名的 JSONL 名称。
            records: 待保存的严格记录。
        """
        lines = [
            json.dumps(
                record.model_dump(mode="json", exclude_computed_fields=True),
                ensure_ascii=False,
                sort_keys=True,
            )
            for record in records
        ]
        content = "\n".join(lines) + ("\n" if lines else "")
        self._atomic_write(self.artifacts_dir / f"{name}.jsonl", content)

    def read_jsonl[T: BaseModel](self, name: str, model_type: type[T]) -> list[T]:
        """逐行读取并严格校验制片记录。

        Args:
            name: 不含扩展名的 JSONL 名称。
            model_type: 单条记录的 Pydantic 类型。

        Returns:
            文件顺序的记录列表；文件不存在时返回空列表。
        """
        path = self.artifacts_dir / f"{name}.jsonl"
        if not path.exists():
            return []
        return [
            model_type.model_validate_json(line) for line in path.read_text("utf-8").splitlines()
        ]

    def write_export(self, name: str, content: str) -> Path:
        """原子写入一个制片导出文件。

        Args:
            name: 不含目录的导出文件名。
            content: UTF-8 文件内容。

        Returns:
            已写入文件的绝对路径。

        Raises:
            ValueError: 文件名试图跳出制片导出目录。
        """
        if Path(name).name != name:
            raise ValueError("制片导出文件名不能包含目录。")
        path = self.exports_dir / name
        self._atomic_write(path, content)
        return path

    def _atomic_write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
