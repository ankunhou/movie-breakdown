"""组合各阶段服务的剧本叙事拆解应用门面。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from movie_breakdown.application.artifact_loading import (
    ExistingAnalysisArtifacts,
    load_existing_artifacts,
    load_validated_base_breakdown,
)
from movie_breakdown.application.exporting import ExportFormat
from movie_breakdown.application.ports import NarrativeAnalyzer
from movie_breakdown.application.quality import NarrativeQualityService
from movie_breakdown.application.quality_exporting import SemanticQualityExporter
from movie_breakdown.application.splitting import AdaptiveSceneSplitter
from movie_breakdown.domain.export import NarrativeBreakdown
from movie_breakdown.domain.quality import (
    HumanReviewAnswers,
    SemanticQualityReport,
)
from movie_breakdown.domain.run import ProjectConfig, ProjectDocument, RunManifest, ValidationReport
from movie_breakdown.infrastructure.storage import ProjectStore
from movie_breakdown.pipeline.biography_stages import BiographyStageService
from movie_breakdown.pipeline.correction_stages import ManualCorrectionStageService
from movie_breakdown.pipeline.definitions import reconcile_manifest_stages, stage_versions
from movie_breakdown.pipeline.dossier_stages import CharacterDossierStageService
from movie_breakdown.pipeline.local_stages import LocalStageService
from movie_breakdown.pipeline.narrative_stages import NarrativeStageService
from movie_breakdown.pipeline.output_stages import OutputStageService
from movie_breakdown.pipeline.runtime import (
    PipelineStageError,
    ProgressCallback,
    StageRuntime,
)


class PipelineConfigurationError(ValueError):
    """完整分析缺少模型实现或必要配置。"""


@dataclass(frozen=True, slots=True)
class PipelineRunResult:
    """完整流水线成功后的用户可见结果。

    Attributes:
        project: 当前项目描述。
        manifest: 全部阶段均已持久化的运行清单。
        validation: 最终本地一致性校验报告。
        exports: 格式名称到导出文件绝对路径的映射。
    """

    project: ProjectDocument
    manifest: RunManifest
    validation: ValidationReport
    exports: dict[str, str]


@dataclass(frozen=True, slots=True)
class QualityReviewRunResult:
    """旁路语义质量评测成功后的报告和产物路径。

    Attributes:
        report: 自动信号与人工抽检结果严格分离的质量报告。
        exports: 报告、Markdown 和人工答案模板的绝对路径。
    """

    report: SemanticQualityReport
    exports: dict[str, str]


class AnalysisPipeline:
    """对 CLI 暴露项目创建、分析、恢复、校验和导出用例。

    Attributes:
        store: 当前剧本拆解项目存储。
        analyzer: 可选的模型分析策略；纯校验和导出不需要。
        progress: 可选中文进度回调。
    """

    def __init__(
        self,
        store: ProjectStore,
        analyzer: NarrativeAnalyzer | None = None,
        progress: ProgressCallback | None = None,
    ) -> None:
        """创建应用流水线门面。

        Args:
            store: 当前项目目录的存储实例。
            analyzer: 完整分析和模型格式识别使用的实现。
            progress: 接收阶段名和中文消息的进度回调。
        """
        self.store = store
        self.analyzer = analyzer
        self.progress = progress

    def initialize(self, source_path: Path, config: ProjectConfig) -> ProjectDocument:
        """创建项目并把源剧本复制到只读输入目录。

        Args:
            source_path: 用户提供的源剧本路径。
            config: 固化到项目并参与缓存的分析配置。

        Returns:
            新创建的项目描述。
        """
        project, _ = self.store.initialize(source_path, config, stage_versions())
        return project

    def run(self) -> PipelineRunResult:
        """执行或恢复完整分析、校验和默认导出流水线。

        Returns:
            项目、最终清单、校验报告和导出路径。

        Raises:
            PipelineConfigurationError: 没有配置模型分析器。
            PipelineStageError: 任一阶段失败或校验不通过。
        """
        if self.analyzer is None:
            raise PipelineConfigurationError("完整分析需要可用的 DeepSeek 分析器。")
        project = self.store.load_project()
        runtime = self._runtime()
        local = LocalStageService(runtime, AdaptiveSceneSplitter(self.analyzer))
        narrative = NarrativeStageService(runtime, self.analyzer)
        biography = BiographyStageService(runtime, self.analyzer)
        dossier = CharacterDossierStageService(runtime)
        output = OutputStageService(runtime)

        normalized = local.normalize(project)
        screenplay = local.split(project, normalized)
        scenes = narrative.analyze_scenes(project, screenplay)
        global_result = narrative.analyze_global(project, screenplay, scenes)
        dossiers = dossier.build(screenplay, global_result)
        biographies = biography.analyze(project, screenplay, scenes, global_result, dossiers)
        validation = output.validate(
            screenplay,
            scenes,
            global_result,
            dossiers,
            biographies,
        )
        if not validation.report.valid:
            raise PipelineStageError("一致性校验未通过，请检查 validation.json。")
        breakdown = output.aggregate(
            screenplay,
            scenes.records,
            global_result,
            dossiers,
            biographies,
            validation.report,
        )
        breakdown = ManualCorrectionStageService(runtime).apply(breakdown)
        exports = output.export(breakdown, "all")
        return PipelineRunResult(project, runtime.manifest, validation.report, exports)

    def validate_only(self) -> ValidationReport:
        """重新校验项目中的现有结构化产物，不调用模型。

        Returns:
            最新本地一致性校验报告。

        Raises:
            PipelineStageError: 项目缺少校验所需的上游产物。
        """
        return self.load_breakdown().validation

    def export_only(self, export_format: ExportFormat = "all") -> dict[str, str]:
        """重新校验现有产物并导出指定格式。

        Args:
            export_format: `markdown`、`json` 或 `all`。

        Returns:
            格式名称到导出文件绝对路径的映射。

        Raises:
            PipelineStageError: 上游产物缺失或校验未通过。
        """
        breakdown = self.load_breakdown()
        return OutputStageService(self._runtime()).export(breakdown, export_format)

    def load_breakdown(self) -> NarrativeBreakdown:
        """重新校验并聚合现有项目，不调用任何模型。

        Returns:
            可供导出或旁路质量评测使用的严格完整拆解。

        Raises:
            PipelineStageError: 上游产物缺失或确定性校验未通过。
        """
        runtime = self._runtime()
        base = self._load_base_breakdown(runtime)
        return ManualCorrectionStageService(runtime).apply(base)

    def load_base_breakdown(self, *, read_only: bool = False) -> NarrativeBreakdown:
        """重新校验并聚合尚未应用人工修正的基础分析。

        Args:
            read_only: 是否仅在内存校验，且不更新校验产物或运行清单。

        Returns:
            仅由模型阶段和确定性档案组成的基础叙事聚合。

        Raises:
            PipelineStageError: 上游产物缺失或确定性校验未通过。
        """
        if not read_only:
            return self._load_base_breakdown(self._runtime())
        return load_validated_base_breakdown(self.store)

    def _load_base_breakdown(self, runtime: StageRuntime) -> NarrativeBreakdown:
        """使用给定运行时构建未应用人工修正的基础聚合。"""
        existing = self._load_existing()
        output = OutputStageService(runtime)
        validation = output.validate(
            existing.screenplay,
            existing.scenes,
            existing.global_result,
            existing.dossiers,
            existing.biographies,
            force=True,
        )
        if not validation.report.valid:
            raise PipelineStageError("一致性校验未通过，不能执行语义质量评测。")
        return output.aggregate(
            existing.screenplay,
            existing.scenes.records,
            existing.global_result,
            existing.dossiers,
            existing.biographies,
            validation.report,
        )

    def review_only(
        self,
        sample_size: int = 16,
        answers: HumanReviewAnswers | None = None,
    ) -> QualityReviewRunResult:
        """离线生成语义风险信号并可选合并人工答案。

        Args:
            sample_size: 稳定风险抽样目标数，范围为 6 到 50。
            answers: 可选且必须匹配当前分析指纹的人工评测答案。

        Returns:
            严格语义质量报告及其持久化路径。

        Raises:
            PipelineStageError: 项目缺少有效分析产物或一致性校验失败。
            StaleReviewAnswersError: 人工答案已经过期或引用未知目标。
        """
        report = NarrativeQualityService().review(
            self.load_breakdown(),
            sample_size,
            answers,
        )
        exports = SemanticQualityExporter().export(self.store, report)
        return QualityReviewRunResult(report=report, exports=exports)

    def status(self) -> RunManifest:
        """读取当前项目的阶段运行清单。

        Returns:
            严格校验后的运行清单。
        """
        return self._load_manifest()

    def _runtime(self) -> StageRuntime:
        """为单次命令创建共享同一 manifest 的阶段运行时。"""
        return StageRuntime(self.store, self._load_manifest(), self.progress)

    def _load_manifest(self) -> RunManifest:
        """读取运行清单并为旧项目补齐新增阶段。"""
        manifest = self.store.load_manifest()
        if reconcile_manifest_stages(manifest):
            self.store.save_manifest(manifest)
        return manifest

    def _load_existing(self) -> ExistingAnalysisArtifacts:
        """严格读取 validate 和 export 所需的全部上游产物。"""
        return load_existing_artifacts(self.store)
