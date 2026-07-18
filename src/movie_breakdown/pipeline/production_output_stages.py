"""制片总表、校验、聚合与正式导出的本地阶段。"""

from __future__ import annotations

from dataclasses import dataclass

from movie_breakdown.application.production_aggregation import (
    ConservativeProductionCatalogBuilder,
    ProductionCatalogBuilder,
)
from movie_breakdown.application.production_validation import ProductionValidationService
from movie_breakdown.domain.production_catalog import (
    GlobalProductionCatalog,
    ProductionBreakdown,
    ProductionValidationReport,
)
from movie_breakdown.domain.production_scene import SceneProductionRecord
from movie_breakdown.domain.run import Artifact
from movie_breakdown.domain.source import Screenplay
from movie_breakdown.infrastructure.fingerprint import (
    cache_fingerprint,
    content_fingerprint,
    schema_fingerprint,
)
from movie_breakdown.pipeline.production_artifacts import make_production_artifact
from movie_breakdown.pipeline.production_definitions import get_production_stage
from movie_breakdown.pipeline.production_scene_stages import ProductionSceneStageResult
from movie_breakdown.pipeline.runtime import PipelineStageError, StageRuntime

_CATALOG_CONTRACT_VERSION = "conservative-1.0"


@dataclass(frozen=True, slots=True)
class ProductionCatalogStageResult:
    """制片总表产物及其内容指纹。

    Attributes:
        artifact: 带完整本地派生链的制片目录产物。
        artifact_fingerprint: 目录业务内容指纹。
    """

    artifact: Artifact[GlobalProductionCatalog]
    artifact_fingerprint: str


@dataclass(frozen=True, slots=True)
class ProductionValidationStageResult:
    """制片校验报告及其内容指纹。

    Attributes:
        report: 覆盖率、目录规模与全部问题。
        artifact_fingerprint: 校验报告内容指纹。
    """

    report: ProductionValidationReport
    artifact_fingerprint: str


class ProductionOutputStageService:
    """执行不调用模型的制片总表、校验、聚合和导出阶段。"""

    def __init__(
        self,
        runtime: StageRuntime,
        builder: ProductionCatalogBuilder | None = None,
        validator: ProductionValidationService | None = None,
    ) -> None:
        """创建可替换本地策略的制片输出服务。

        Args:
            runtime: 绑定独立制片清单的阶段运行时。
            builder: 可选的确定性目录构建策略。
            validator: 可选的制片一致性校验服务。
        """
        self.runtime = runtime
        self.builder = builder or ConservativeProductionCatalogBuilder()
        self.validator = validator or ProductionValidationService()

    def build_catalog(
        self,
        screenplay: Artifact[Screenplay],
        scenes: ProductionSceneStageResult,
    ) -> ProductionCatalogStageResult:
        """从完整逐场记录构建或复用确定性制片总表。

        Args:
            screenplay: 主项目共享的只读场景产物。
            scenes: 制片逐场记录及其聚合指纹。

        Returns:
            严格制片总表产物和内容指纹。

        Raises:
            PipelineStageError: 逐场记录不完整或目录构建失败。
        """
        cache_key = cache_fingerprint(
            screenplay.metadata.artifact_fingerprint,
            scenes.artifact_fingerprint,
            get_production_stage("production_catalog").version,
            _CATALOG_CONTRACT_VERSION,
            schema_fingerprint(GlobalProductionCatalog),
        )
        cached = self.runtime.load_cached(
            "production_catalog",
            "catalog",
            GlobalProductionCatalog,
            cache_key,
        )
        if cached:
            return ProductionCatalogStageResult(cached, cached.metadata.artifact_fingerprint)
        self.runtime.start("production_catalog", cache_key, "确定性汇总全剧制片目录")
        try:
            analyses = [record.analysis for record in scenes.records if record.analysis is not None]
            catalog = self.builder.build(screenplay.data, analyses)
            artifact = make_production_artifact(
                stage_name="production_catalog",
                cache_key=cache_key,
                data=catalog,
                source_fingerprint=screenplay.data.source_fingerprint,
                upstream_fingerprints=[
                    screenplay.metadata.artifact_fingerprint,
                    scenes.artifact_fingerprint,
                ],
                model_parameters={"catalog_contract": _CATALOG_CONTRACT_VERSION},
            )
            self.runtime.store.write_artifact("catalog", artifact)
            self.runtime.success(
                "production_catalog",
                cache_key,
                artifact.metadata.artifact_fingerprint,
            )
            return ProductionCatalogStageResult(
                artifact,
                artifact.metadata.artifact_fingerprint,
            )
        except Exception as error:
            self.runtime.fail("production_catalog", error)
            raise PipelineStageError(f"制片总表构建失败：{error}") from error

    def validate(
        self,
        screenplay: Artifact[Screenplay],
        records: list[SceneProductionRecord],
        catalog: ProductionCatalogStageResult | None,
        *,
        force: bool = False,
    ) -> ProductionValidationStageResult:
        """校验逐场覆盖、证据、引用和目录完整性。

        Args:
            screenplay: 主项目共享的只读场景产物。
            records: 当前独立制片逐场记录。
            catalog: 可选的最新确定性总表。
            force: 是否忽略有效校验缓存重新执行。

        Returns:
            即使不通过也会保存的制片校验报告。
        """
        records_fingerprint = content_fingerprint(records)
        catalog_fingerprint = catalog.artifact_fingerprint if catalog else "catalog-missing"
        cache_key = cache_fingerprint(
            screenplay.metadata.artifact_fingerprint,
            records_fingerprint,
            catalog_fingerprint,
            get_production_stage("production_validation").version,
            schema_fingerprint(ProductionValidationReport),
        )
        if not force:
            cached = self.runtime.load_cached(
                "production_validation",
                "validation",
                ProductionValidationReport,
                cache_key,
            )
            if cached and cached.data.valid:
                return ProductionValidationStageResult(
                    cached.data,
                    cached.metadata.artifact_fingerprint,
                )
        self.runtime.start("production_validation", cache_key, "执行制片一致性校验")
        report = self.validator.validate(
            screenplay.data,
            records,
            catalog.artifact.data if catalog else None,
        )
        artifact = make_production_artifact(
            stage_name="production_validation",
            cache_key=cache_key,
            data=report,
            source_fingerprint=screenplay.data.source_fingerprint,
            upstream_fingerprints=[
                screenplay.metadata.artifact_fingerprint,
                records_fingerprint,
                catalog_fingerprint,
            ],
        )
        self.runtime.store.write_artifact("validation", artifact)
        if report.valid:
            self.runtime.success(
                "production_validation",
                cache_key,
                artifact.metadata.artifact_fingerprint,
            )
        else:
            errors = sum(issue.severity.value == "error" for issue in report.issues)
            self.runtime.fail("production_validation", f"制片校验发现 {errors} 个错误。")
        return ProductionValidationStageResult(report, artifact.metadata.artifact_fingerprint)

    def aggregate(
        self,
        screenplay: Artifact[Screenplay],
        records: list[SceneProductionRecord],
        catalog: GlobalProductionCatalog,
        validation: ProductionValidationReport,
    ) -> ProductionBreakdown:
        """把已验证分散产物组合为正式制片拆解。

        Args:
            screenplay: 主项目共享的只读场景产物。
            records: 成功且按剧本顺序排列的逐场记录。
            catalog: 确定性全剧制片目录。
            validation: 与当前输入对应的校验报告。

        Returns:
            可供 JSON、Markdown 和 CSV 导出的完整模型。
        """
        return ProductionBreakdown(
            title=screenplay.data.title,
            source_fingerprint=screenplay.data.source_fingerprint,
            scenes=[record.analysis for record in records if record.analysis is not None],
            catalog=catalog,
            validation=validation,
        )
