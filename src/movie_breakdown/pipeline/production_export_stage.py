"""保存完整制片拆解并管理多格式正式导出阶段。"""

from __future__ import annotations

from movie_breakdown.application.production_exporting import (
    ProductionExportFormat,
    ProductionExportService,
)
from movie_breakdown.domain.production_catalog import ProductionBreakdown
from movie_breakdown.infrastructure.fingerprint import (
    cache_fingerprint,
    content_fingerprint,
    schema_fingerprint,
)
from movie_breakdown.pipeline.export_integrity import exported_contents_match
from movie_breakdown.pipeline.production_artifacts import make_production_artifact
from movie_breakdown.pipeline.production_definitions import get_production_stage
from movie_breakdown.pipeline.runtime import PipelineStageError, StageRuntime


class ProductionExportStageService:
    """管理制片完整产物、文件缓存和正式导出状态。"""

    def __init__(
        self,
        runtime: StageRuntime,
        exporter: ProductionExportService | None = None,
    ) -> None:
        """创建独立制片导出阶段。

        Args:
            runtime: 绑定独立制片清单的阶段运行时。
            exporter: 可选的 JSON、Markdown 与 CSV 渲染服务。
        """
        self.runtime = runtime
        self.exporter = exporter or ProductionExportService()

    def export(
        self,
        breakdown: ProductionBreakdown,
        export_format: ProductionExportFormat = "all",
    ) -> dict[str, str]:
        """保存完整拆解产物并生成独立制片报告文件。

        Args:
            breakdown: 已通过一致性校验的完整制片拆解。
            export_format: `markdown`、`json`、`csv` 或 `all`。

        Returns:
            格式名称到导出文件绝对路径的映射。

        Raises:
            PipelineStageError: 校验未通过或文件导出失败。
        """
        fingerprint = content_fingerprint(breakdown)
        artifact_key = cache_fingerprint(
            fingerprint,
            get_production_stage("production_export").version,
            schema_fingerprint(ProductionBreakdown),
        )
        artifact = make_production_artifact(
            stage_name="production_export",
            cache_key=artifact_key,
            data=breakdown,
            source_fingerprint=breakdown.source_fingerprint,
            upstream_fingerprints=[
                content_fingerprint(breakdown.scenes),
                content_fingerprint(breakdown.catalog),
                content_fingerprint(breakdown.validation),
            ],
        )
        self.runtime.store.write_artifact("breakdown", artifact)
        cache_key = cache_fingerprint(artifact_key, export_format)
        paths = self._expected_paths(export_format)
        expected_contents = self.exporter.render_contents(breakdown, export_format)
        record = self.runtime.manifest.stages["production_export"]
        if record.cache_key == cache_key and exported_contents_match(
            self.runtime.store.exports_dir,
            paths,
            expected_contents,
        ):
            self.runtime.cached("production_export", cache_key, fingerprint)
            return {
                kind: str(self.runtime.store.exports_dir / name) for kind, name in paths.items()
            }
        self.runtime.start("production_export", cache_key, "生成制片 JSON、Markdown 与 CSV")
        try:
            exported = self.exporter.export(self.runtime.store, breakdown, export_format)
            self.runtime.success("production_export", cache_key, fingerprint)
            return exported
        except Exception as error:
            self.runtime.fail("production_export", error)
            raise PipelineStageError(f"制片报告导出失败：{error}") from error

    @staticmethod
    def _expected_paths(export_format: ProductionExportFormat) -> dict[str, str]:
        """返回一个制片导出格式对应的固定文件名。"""
        paths = {
            "json": "breakdown.json",
            "markdown": "report.md",
            "scenes_csv": "scenes.csv",
            "catalog_csv": "catalog.csv",
        }
        if export_format == "all":
            return paths
        if export_format == "csv":
            return {key: paths[key] for key in ("scenes_csv", "catalog_csv")}
        return {export_format: paths[export_format]}
