"""纯本地加载制片规划所需的共享剧本与基础拆解。"""

from __future__ import annotations

from dataclasses import dataclass

from movie_breakdown.application.production_aggregation import (
    ConservativeProductionCatalogBuilder,
)
from movie_breakdown.application.production_validation import ProductionValidationService
from movie_breakdown.domain.production_catalog import ProductionBreakdown
from movie_breakdown.domain.production_scene import SceneProductionRecord
from movie_breakdown.domain.source import Screenplay
from movie_breakdown.infrastructure.fingerprint import hash_bytes
from movie_breakdown.infrastructure.production_storage import ProductionStore
from movie_breakdown.pipeline.production_local_gate import inspect_production_records


class ProductionPlanningContextError(ValueError):
    """规划输入与当前主项目或源剧本不一致。"""


@dataclass(frozen=True, slots=True)
class ProductionPlanningInputs:
    """本地规划、复核与修正共同消费的只读输入。

    Attributes:
        screenplay: 已核对源文件指纹的共享剧本与场景。
        breakdown: 从现有逐场制片产物重新校验并聚合的基础拆解。
    """

    screenplay: Screenplay
    breakdown: ProductionBreakdown


class ProductionPlanningContextLoader:
    """不加载密钥或模型地重建当前有效制片规划输入。"""

    def __init__(self, store: ProductionStore) -> None:
        """绑定当前独立制片存储。

        Args:
            store: 同时提供主项目只读输入和制片本地产物的仓库。
        """
        self._store = store

    def load(self) -> ProductionPlanningInputs:
        """校验来源、场景、逐场记录和目录后返回规划输入。

        Returns:
            当前共享剧本与确定性重建的基础制片拆解。

        Raises:
            ProductionPlanningContextError: 作用域、共享场景或现有逐场记录无效。
        """
        project = self._store.load_project()
        parent = self._store.project_store.load_project()
        if parent.id != project.parent_project_id:
            raise ProductionPlanningContextError("制片作用域与当前主项目 ID 不一致。")
        screenplay_artifact = self._store.project_store.read_artifact("scenes", Screenplay)
        source_fingerprint = hash_bytes(self._store.project_store.source_path(parent).read_bytes())
        if screenplay_artifact.data.source_fingerprint != source_fingerprint:
            raise ProductionPlanningContextError(
                "主项目共享场景已相对源剧本过期，请先更新场景切分产物。"
            )
        records = self._store.read_jsonl("scene_elements", SceneProductionRecord)
        state = inspect_production_records(
            project,
            screenplay_artifact,
            records,
            self._store.load_manifest(),
        )
        if not state.trusted:
            raise ProductionPlanningContextError(
                state.reason or "制片逐场记录无法形成当前可信输入。"
            )
        analyses = [item.analysis for item in state.records if item.analysis is not None]
        catalog = ConservativeProductionCatalogBuilder().build(
            screenplay_artifact.data,
            analyses,
        )
        validation = ProductionValidationService().validate(
            screenplay_artifact.data,
            state.records,
            catalog,
        )
        if not validation.valid:
            raise ProductionPlanningContextError("现有制片逐场结果未通过本地一致性校验。")
        breakdown = ProductionBreakdown(
            title=screenplay_artifact.data.title,
            source_fingerprint=screenplay_artifact.data.source_fingerprint,
            scenes=analyses,
            catalog=catalog,
            validation=validation,
        )
        return ProductionPlanningInputs(screenplay_artifact.data, breakdown)
