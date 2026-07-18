"""独立制片元素拆解的应用层流水线门面。"""

from __future__ import annotations

from dataclasses import dataclass

from movie_breakdown.application.production_exporting import ProductionExportFormat
from movie_breakdown.application.production_local_output import (
    ProductionLocalOutputService,
    production_stage_identity,
)
from movie_breakdown.application.production_ports import ProductionAnalyzer
from movie_breakdown.domain.production_catalog import (
    ProductionBreakdown,
    ProductionValidationReport,
)
from movie_breakdown.domain.production_run import ProductionConfig, ProductionProject
from movie_breakdown.domain.run import Artifact, RunManifest
from movie_breakdown.domain.source import Screenplay
from movie_breakdown.infrastructure.fingerprint import hash_bytes
from movie_breakdown.infrastructure.production_storage import ProductionStore
from movie_breakdown.pipeline.production_definitions import (
    get_production_stage,
    reconcile_production_manifest_stages,
)
from movie_breakdown.pipeline.production_export_stage import ProductionExportStageService
from movie_breakdown.pipeline.production_local_gate import mark_successful_stages_stale
from movie_breakdown.pipeline.production_output_stages import ProductionOutputStageService
from movie_breakdown.pipeline.production_scene_stages import (
    ProductionSceneStageService,
)
from movie_breakdown.pipeline.runtime import PipelineStageError, ProgressCallback, StageRuntime


class ProductionPipelineConfigurationError(ValueError):
    """表示完整制片分析缺少模型实现或共享场景已经过期。"""


@dataclass(frozen=True, slots=True)
class ProductionPipelineRunResult:
    """完整制片流水线成功后的用户可见结果。

    Attributes:
        project: 独立制片项目配置。
        manifest: 四个制片阶段的独立运行清单。
        validation: 最终确定性一致性校验报告。
        exports: 格式名称到导出文件绝对路径的映射。
    """

    project: ProductionProject
    manifest: RunManifest
    validation: ProductionValidationReport
    exports: dict[str, str]


class ProductionPipeline:
    """对 CLI 暴露制片初始化、分析、恢复、校验和导出用例。

    Attributes:
        store: 与叙事存储隔离的制片仓库。
        analyzer: 可选单场制片模型策略；纯校验和导出不需要。
        progress: 可选中文进度回调。
    """

    def __init__(
        self,
        store: ProductionStore,
        analyzer: ProductionAnalyzer | None = None,
        progress: ProgressCallback | None = None,
    ) -> None:
        """创建独立制片流水线门面。

        Args:
            store: 组合主项目只读输入的独立制片仓库。
            analyzer: 完整分析使用的单场制片模型策略。
            progress: 接收阶段名和中文消息的进度回调。
        """
        self.store = store
        self.analyzer = analyzer
        self.progress = progress

    def initialize(self, config: ProductionConfig | None = None) -> ProductionProject:
        """验证主项目共享场景并创建独立制片作用域。

        Args:
            config: 可选制片模型配置；省略时从主项目复制相关参数。

        Returns:
            新创建的独立制片项目描述。

        Raises:
            ProductionPipelineConfigurationError: 共享场景缺失或相对源剧本已过期。
            ProductionAlreadyInitializedError: 制片作用域已经存在。
        """
        parent = self.store.project_store.load_project()
        self._load_screenplay(parent.id)
        project, _ = self.store.initialize(
            parent,
            config or ProductionConfig.from_project_config(parent.config),
        )
        return project

    def run(self) -> ProductionPipelineRunResult:
        """执行或恢复逐场模型、总表、校验和默认全格式导出。

        Returns:
            独立项目、清单、校验报告和导出路径。

        Raises:
            ProductionPipelineConfigurationError: 没有配置制片模型分析器。
            PipelineStageError: 任一阶段失败或一致性校验不通过。
        """
        if self.analyzer is None:
            raise ProductionPipelineConfigurationError("完整制片分析需要可用的 DeepSeek 分析器。")
        project = self.store.load_project()
        screenplay = self._load_screenplay(project.parent_project_id)
        runtime = self._runtime()
        previous_scene_identity = production_stage_identity(
            runtime.manifest.stages["production_scene_analysis"]
        )
        try:
            scenes = ProductionSceneStageService(runtime, self.analyzer).analyze(
                project,
                screenplay,
            )
        except Exception:
            mark_successful_stages_stale(
                runtime,
                ("production_catalog", "production_validation", "production_export"),
                "制片逐场阶段失败，下游产物已过期。",
            )
            raise
        if previous_scene_identity != production_stage_identity(
            runtime.manifest.stages["production_scene_analysis"]
        ):
            mark_successful_stages_stale(
                runtime,
                ("production_catalog", "production_validation", "production_export"),
                "制片逐场结果已变更，下游产物已过期。",
            )
        output = ProductionOutputStageService(runtime)
        previous_catalog_identity = production_stage_identity(
            runtime.manifest.stages["production_catalog"]
        )
        try:
            catalog = output.build_catalog(screenplay, scenes)
        except Exception:
            mark_successful_stages_stale(
                runtime,
                ("production_validation", "production_export"),
                "制片目录阶段失败，下游产物已过期。",
            )
            raise
        if previous_catalog_identity != production_stage_identity(
            runtime.manifest.stages["production_catalog"]
        ):
            mark_successful_stages_stale(
                runtime,
                ("production_validation", "production_export"),
                "制片目录已变更，需要重新校验和导出。",
            )
        previous_validation_identity = production_stage_identity(
            runtime.manifest.stages["production_validation"]
        )
        try:
            validation = output.validate(screenplay, scenes.records, catalog)
        except Exception:
            mark_successful_stages_stale(
                runtime,
                ("production_export",),
                "制片校验执行失败，旧导出已过期。",
            )
            raise
        validation_changed = previous_validation_identity != production_stage_identity(
            runtime.manifest.stages["production_validation"]
        )
        if validation_changed:
            mark_successful_stages_stale(
                runtime,
                ("production_export",),
                "制片校验结果已变更，旧导出已过期。",
            )
        if not validation.report.valid:
            mark_successful_stages_stale(
                runtime,
                ("production_export",),
                "制片校验未通过，旧导出已过期。",
            )
            raise PipelineStageError(
                "制片一致性校验未通过，请检查 production/artifacts/validation.json。"
            )
        breakdown = output.aggregate(
            screenplay,
            scenes.records,
            catalog.artifact.data,
            validation.report,
        )
        exports = ProductionExportStageService(runtime).export(breakdown, "all")
        return ProductionPipelineRunResult(
            project,
            runtime.manifest,
            validation.report,
            exports,
        )

    def validate_only(self) -> ProductionValidationReport:
        """不调用模型地重建可用总表并重新执行一致性校验。

        Returns:
            最新制片覆盖率、目录规模和全部问题。
        """
        project = self.store.load_project()
        screenplay = self._load_screenplay(project.parent_project_id)
        runtime = self._runtime()
        return ProductionLocalOutputService(
            self.store,
            runtime,
            project,
            screenplay,
        ).validate()

    def export_only(
        self,
        export_format: ProductionExportFormat = "all",
    ) -> dict[str, str]:
        """重新校验现有制片产物并导出指定格式。

        Args:
            export_format: `markdown`、`json`、`csv` 或 `all`。

        Returns:
            格式名称到导出文件绝对路径的映射。

        Raises:
            PipelineStageError: 逐场产物不完整或校验未通过。
        """
        breakdown, runtime = self._load_breakdown()
        return ProductionExportStageService(runtime).export(breakdown, export_format)

    def status(self) -> RunManifest:
        """读取独立制片阶段运行清单。

        Returns:
            只包含 `production_*` 阶段的严格运行清单。
        """
        return self._load_manifest()

    def _load_breakdown(self) -> tuple[ProductionBreakdown, StageRuntime]:
        """从现有逐场产物重建、校验并聚合正式拆解。"""
        project = self.store.load_project()
        screenplay = self._load_screenplay(project.parent_project_id)
        runtime = self._runtime()
        breakdown = ProductionLocalOutputService(
            self.store,
            runtime,
            project,
            screenplay,
        ).load_breakdown()
        return breakdown, runtime

    def _runtime(self) -> StageRuntime:
        """为一次命令创建绑定独立注册表和清单的运行时。"""
        return StageRuntime(
            self.store,
            self._load_manifest(),
            self.progress,
            stage_lookup=get_production_stage,
        )

    def _load_manifest(self) -> RunManifest:
        """读取制片清单并为旧项目补齐新增阶段。"""
        manifest = self.store.load_manifest()
        if reconcile_production_manifest_stages(manifest):
            self.store.save_manifest(manifest)
        return manifest

    def _load_screenplay(self, parent_project_id: str) -> Artifact[Screenplay]:
        """读取共享场景并拒绝父项目错配或源文件过期。"""
        parent = self.store.project_store.load_project()
        if parent.id != parent_project_id:
            raise ProductionPipelineConfigurationError("制片作用域与当前主项目 ID 不一致。")
        screenplay = self.store.project_store.read_artifact("scenes", Screenplay)
        source_fingerprint = hash_bytes(self.store.project_store.source_path(parent).read_bytes())
        if screenplay.data.source_fingerprint != source_fingerprint:
            raise ProductionPipelineConfigurationError(
                "主项目共享场景已相对源剧本过期，请先运行叙事 resume 更新 scenes.json。"
            )
        return screenplay
