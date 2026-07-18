"""项目目录、原子产物文件和运行清单持久化。"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel

from movie_breakdown.domain.base import StageStatus, utc_now
from movie_breakdown.domain.run import (
    Artifact,
    ProjectConfig,
    ProjectDocument,
    RunManifest,
    StageRecord,
)


class ProjectExistsError(FileExistsError):
    """目标目录已经包含电影拆解项目。"""


class InvalidProjectError(ValueError):
    """目录不是有效的电影拆解项目。"""


class ProjectStore:
    """封装一个剧本拆解项目的所有本地文件操作。"""

    def __init__(self, root: Path) -> None:
        """使用项目根目录创建存储实例。

        Args:
            root: 剧本拆解项目根目录。
        """
        self.root = root.resolve()
        self.source_dir = self.root / "source"
        self.artifacts_dir = self.root / "artifacts"
        self.exports_dir = self.root / "exports"
        self.project_path = self.root / "project.json"
        self.manifest_path = self.artifacts_dir / "manifest.json"

    def initialize(
        self,
        source_path: Path,
        config: ProjectConfig,
        stage_versions: dict[str, str],
    ) -> tuple[ProjectDocument, RunManifest]:
        """创建项目目录、复制源文件并初始化阶段清单。

        Args:
            source_path: 待复制到项目内的源剧本路径。
            config: 需要持久化的项目分析配置。
            stage_versions: 阶段名称与版本映射。

        Returns:
            新建的项目描述和运行清单。

        Raises:
            ProjectExistsError: 目标目录已经包含项目描述。
        """
        if self.project_path.exists():
            raise ProjectExistsError(f"项目已存在：{self.root}，请使用 resume 命令。")
        self.source_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.exports_dir.mkdir(parents=True, exist_ok=True)

        source = source_path.resolve()
        target = self.source_dir / source.name
        if source != target.resolve():
            shutil.copy2(source, target)
        project = ProjectDocument(
            id=uuid4().hex,
            title=source.stem,
            source_relative_path=target.relative_to(self.root).as_posix(),
            config=config,
        )
        manifest = RunManifest(
            project_id=project.id,
            stages={
                name: StageRecord(name=name, version=version, status=StageStatus.PENDING)
                for name, version in stage_versions.items()
            },
        )
        self.write_model(self.project_path, project)
        self.save_manifest(manifest)
        return project, manifest

    def load_project(self) -> ProjectDocument:
        """读取并严格校验项目描述文件。

        Returns:
            当前项目描述。

        Raises:
            InvalidProjectError: 项目描述文件不存在。
        """
        if not self.project_path.is_file():
            raise InvalidProjectError(f"不是有效的电影拆解项目：{self.root}")
        return self.read_model(self.project_path, ProjectDocument)

    def load_manifest(self) -> RunManifest:
        """读取并严格校验运行清单。

        Returns:
            当前项目的流水线运行清单。

        Raises:
            InvalidProjectError: 运行清单不存在。
        """
        if not self.manifest_path.is_file():
            raise InvalidProjectError(f"项目缺少运行清单：{self.manifest_path}")
        return self.read_model(self.manifest_path, RunManifest)

    def save_manifest(self, manifest: RunManifest) -> None:
        """更新时间并原子保存运行清单。

        Args:
            manifest: 待持久化的运行清单。
        """
        manifest.updated_at = utc_now()
        self.write_model(self.manifest_path, manifest)

    def source_path(self, project: ProjectDocument | None = None) -> Path:
        """返回项目内源剧本的绝对路径。

        Args:
            project: 已加载的项目描述；省略时从磁盘读取。

        Returns:
            项目内源剧本的绝对路径。
        """
        document = project or self.load_project()
        return self.root / document.source_relative_path

    def artifact_path(self, name: str) -> Path:
        """返回普通 JSON 产物路径。

        Args:
            name: 不含扩展名的稳定产物名称。

        Returns:
            `artifacts` 目录中的 JSON 文件路径。
        """
        return self.artifacts_dir / f"{name}.json"

    def write_artifact[T: BaseModel](self, name: str, artifact: Artifact[T]) -> None:
        """原子写入包含元数据的 JSON 产物。

        Args:
            name: 不含扩展名的稳定产物名称。
            artifact: 待写入的带元数据产物。
        """
        self.write_model(self.artifact_path(name), artifact)

    def read_artifact[T: BaseModel](self, name: str, data_type: type[T]) -> Artifact[T]:
        """读取并按指定业务模型校验 JSON 产物。

        Args:
            name: 不含扩展名的稳定产物名称。
            data_type: 产物业务数据的 Pydantic 类型。

        Returns:
            通过元数据和业务类型校验的产物。
        """
        return self.read_model(self.artifact_path(name), Artifact[data_type])

    def write_jsonl(self, name: str, records: list[BaseModel]) -> None:
        """把逐场记录原子写为 UTF-8 JSONL。

        Args:
            name: 不含扩展名的稳定产物名称。
            records: 按期望文件顺序排列的 Pydantic 记录。
        """
        lines = [
            json.dumps(
                record.model_dump(mode="json", exclude_computed_fields=True),
                ensure_ascii=False,
                sort_keys=True,
            )
            for record in records
        ]
        self._atomic_write(self.artifacts_dir / f"{name}.jsonl", "\n".join(lines) + "\n")

    def read_jsonl[T: BaseModel](self, name: str, model_type: type[T]) -> list[T]:
        """逐行读取并严格校验 JSONL 记录。

        Args:
            name: 不含扩展名的稳定产物名称。
            model_type: 每一行应满足的 Pydantic 类型。

        Returns:
            按文件顺序完成校验的记录列表；文件不存在时返回空列表。
        """
        path = self.artifacts_dir / f"{name}.jsonl"
        if not path.exists():
            return []
        return [
            model_type.model_validate_json(line) for line in path.read_text("utf-8").splitlines()
        ]

    def write_model(self, path: Path, model: BaseModel) -> None:
        """把 Pydantic 模型以易读 JSON 原子写入。

        Args:
            path: 目标 JSON 文件路径。
            model: 待序列化的 Pydantic 模型。
        """
        content = json.dumps(
            model.model_dump(mode="json", exclude_computed_fields=True),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        self._atomic_write(path, content + "\n")

    def write_export(self, name: str, content: str) -> Path:
        """原子写入用户可见的导出文件。

        Args:
            name: 包含扩展名且不含目录的导出文件名。
            content: 待写入的 UTF-8 文本内容。

        Returns:
            已写入导出文件的绝对路径。

        Raises:
            ValueError: 文件名试图跳出 `exports` 目录。
        """
        if Path(name).name != name:
            raise ValueError("导出文件名不能包含目录。")
        path = self.exports_dir / name
        self._atomic_write(path, content)
        return path

    def read_model[T: BaseModel](self, path: Path, model_type: type[T]) -> T:
        """从 UTF-8 JSON 文件读取指定 Pydantic 模型。

        Args:
            path: 待读取的 JSON 文件路径。
            model_type: 目标 Pydantic 模型类型。

        Returns:
            完成严格校验的模型实例。
        """
        return model_type.model_validate_json(path.read_text(encoding="utf-8"))

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
