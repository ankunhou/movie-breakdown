"""制片本地校验与导出命令的可追溯产物组装。"""

from __future__ import annotations

from movie_breakdown.domain.base import StageStatus
from movie_breakdown.domain.production_catalog import (
    ProductionBreakdown,
    ProductionValidationReport,
)
from movie_breakdown.domain.production_run import ProductionProject
from movie_breakdown.domain.production_scene import SceneProductionRecord
from movie_breakdown.domain.run import Artifact, StageRecord
from movie_breakdown.domain.source import Screenplay
from movie_breakdown.infrastructure.production_storage import ProductionStore
from movie_breakdown.pipeline.production_local_gate import (
    inspect_production_records,
    mark_successful_stages_stale,
)
from movie_breakdown.pipeline.production_output_stages import (
    ProductionCatalogStageResult,
    ProductionOutputStageService,
    ProductionValidationStageResult,
)
from movie_breakdown.pipeline.production_scene_stages import ProductionSceneStageResult
from movie_breakdown.pipeline.runtime import PipelineStageError, StageRuntime

_CATALOG_DOWNSTREAM = ("production_validation", "production_export")
_SCENE_DOWNSTREAM = (
    "production_scene_analysis",
    "production_catalog",
    "production_validation",
    "production_export",
)


class ProductionLocalOutputService:
    """在无模型命令中安全重建制片校验和正式拆解。

    Attributes:
        store: 独立制片产物存储。
        runtime: 绑定制片清单的阶段运行时。
        project: 当前制片项目和固化配置。
        screenplay: 已通过源指纹校验的共享场景产物。
    """

    def __init__(
        self,
        store: ProductionStore,
        runtime: StageRuntime,
        project: ProductionProject,
        screenplay: Artifact[Screenplay],
    ) -> None:
        """加载逐场记录并立即失效不可信的成功状态。

        Args:
            store: 独立制片产物存储。
            runtime: 绑定制片清单的阶段运行时。
            project: 当前制片项目和固化配置。
            screenplay: 已通过源指纹校验的共享场景产物。
        """
        self.store = store
        self.runtime = runtime
        self.project = project
        self.screenplay = screenplay
        try:
            records = store.read_jsonl("scene_elements", SceneProductionRecord)
        except (OSError, ValueError) as error:
            mark_successful_stages_stale(
                runtime,
                _SCENE_DOWNSTREAM,
                "制片逐场记录无法安全读取，旧产物已过期。",
            )
            raise PipelineStageError(f"制片逐场记录无法安全读取或校验：{error}") from error
        self.state = inspect_production_records(
            project,
            screenplay,
            records,
            runtime.manifest,
        )
        if not self.state.trusted:
            mark_successful_stages_stale(
                runtime,
                _SCENE_DOWNSTREAM,
                self.state.reason or "制片逐场记录已不可信。",
            )
        self.output = ProductionOutputStageService(runtime)

    def validate(self) -> ProductionValidationReport:
        """重建可用目录并强制校验当前逐场记录。

        Returns:
            当前记录和目录对应的制片校验报告。
        """
        catalog = self._catalog_if_complete()
        return self._validate(catalog).report

    def load_breakdown(self) -> ProductionBreakdown:
        """重建经校验的完整制片拆解。

        Returns:
            场序与共享剧本一致的正式制片拆解。

        Raises:
            PipelineStageError: 逐场产物不可信、不完整或一致性校验失败。
        """
        catalog = self._catalog_if_complete()
        if catalog is None:
            validation = self._validate(None)
            raise PipelineStageError(
                f"制片逐场产物不完整或已过期，覆盖 {validation.report.coverage:.1%}，不能导出。"
            )
        validation = self._validate(catalog)
        if not validation.report.valid:
            raise PipelineStageError("制片一致性校验未通过，不能导出正式报告。")
        return self.output.aggregate(
            self.screenplay,
            self.state.records,
            catalog.artifact.data,
            validation.report,
        )

    def _catalog_if_complete(self) -> ProductionCatalogStageResult | None:
        """只在逐场追溯链完整时构建或复用目录。"""
        if not self.state.trusted:
            return None
        scenes = ProductionSceneStageResult(
            self.state.records,
            self.state.records_fingerprint,
        )
        before = production_stage_identity(self.runtime.manifest.stages["production_catalog"])
        try:
            catalog = self.output.build_catalog(self.screenplay, scenes)
        except Exception:
            mark_successful_stages_stale(
                self.runtime,
                _CATALOG_DOWNSTREAM,
                "制片目录重建失败，下游产物已过期。",
            )
            raise
        after = production_stage_identity(self.runtime.manifest.stages["production_catalog"])
        if before != after:
            mark_successful_stages_stale(
                self.runtime,
                _CATALOG_DOWNSTREAM,
                "制片目录已变更，需要重新校验和导出。",
            )
        return catalog

    def _validate(
        self,
        catalog: ProductionCatalogStageResult | None,
    ) -> ProductionValidationStageResult:
        """执行校验并在报告变更或失败时失效导出。

        Args:
            catalog: 可选的当前制片目录。

        Returns:
            当前输入对应的制片校验阶段结果。
        """
        before = production_stage_identity(self.runtime.manifest.stages["production_validation"])
        try:
            result = self.output.validate(
                self.screenplay,
                self.state.records,
                catalog,
                force=True,
            )
        except Exception:
            mark_successful_stages_stale(
                self.runtime,
                ("production_export",),
                "制片校验执行失败，旧导出已过期。",
            )
            raise
        after = production_stage_identity(self.runtime.manifest.stages["production_validation"])
        if before != after or not result.report.valid:
            mark_successful_stages_stale(
                self.runtime,
                ("production_export",),
                "制片校验结果已变更或未通过，旧导出已过期。",
            )
        return result


def production_stage_identity(
    record: StageRecord,
) -> tuple[str, str | None, str | None, StageStatus]:
    """返回能够判定制片阶段结果是否变更的最小身份。

    Args:
        record: 当前制片阶段清单记录。

    Returns:
        阶段版本、缓存键、产物指纹和状态元组。
    """
    return record.version, record.cache_key, record.artifact_fingerprint, record.status
