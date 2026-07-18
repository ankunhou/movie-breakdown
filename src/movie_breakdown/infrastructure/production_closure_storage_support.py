"""制片闭环仓库共享的路径、严格读取与不可变写入能力。"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ValidationError

from movie_breakdown.infrastructure.fingerprint import content_fingerprint
from movie_breakdown.infrastructure.production_storage import ProductionStore


class ProductionClosureStorageError(ValueError):
    """制片闭环文件缺失、损坏、过期或违反不可变约束。"""


class _ProductionStorageSupport:
    """为闭环仓库各职责模块提供统一路径和低层原子操作。"""

    def __init__(self, store: ProductionStore) -> None:
        """建立路径映射但不提前创建任何目录。

        Args:
            store: 当前项目的独立制片仓库。
        """
        self.store = store
        self.planning_dir = store.root / "planning"
        self.reviews_dir = store.root / "reviews"
        self.corrections_dir = store.root / "corrections"
        self.generations_dir = self.corrections_dir / "generations"
        self.active_path = self.corrections_dir / "active.json"
        self.releases_dir = store.root / "releases"
        self.release_report_path = self.releases_dir / "report.json"

    def _write_immutable(self, path: Path, model: BaseModel) -> None:
        """仅创建新内容或接受完全相同的既有严格 JSON。"""
        if path.is_file():
            try:
                existing = type(model).model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValidationError, ValueError) as error:
                raise ProductionClosureStorageError(f"不可变制片文件已经损坏：{path}") from error
            if content_fingerprint(existing) != content_fingerprint(model):
                raise ProductionClosureStorageError(f"不可变制片文件发生内容冲突：{path}")
            return
        self.store.project_store.write_model(path, model)

    def _read_required[T: BaseModel](self, path: Path, model_type: type[T], label: str) -> T:
        """读取必需严格模型并转换为带文件定位的存储错误。"""
        if not path.is_file():
            raise ProductionClosureStorageError(f"{label}不存在：{path}")
        try:
            return self.store.project_store.read_model(path, model_type)
        except (OSError, ValidationError, ValueError) as error:
            raise ProductionClosureStorageError(f"{label}无效：{path}；{error}") from error
