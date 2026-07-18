"""本地一致性校验与正式报告导出阶段。"""

from __future__ import annotations

from dataclasses import dataclass

from movie_breakdown.application.exporting import ExportFormat, ExportService
from movie_breakdown.application.validation import ValidationService
from movie_breakdown.domain.base import StageStatus
from movie_breakdown.domain.export import NarrativeBreakdown
from movie_breakdown.domain.run import Artifact, ValidationReport
from movie_breakdown.domain.scene_analysis import SceneAnalysisRecord
from movie_breakdown.domain.source import Screenplay
from movie_breakdown.infrastructure.fingerprint import (
    cache_fingerprint,
    content_fingerprint,
    schema_fingerprint,
)
from movie_breakdown.pipeline.artifacts import make_artifact
from movie_breakdown.pipeline.biography_stages import BiographyStageResult
from movie_breakdown.pipeline.definitions import get_stage
from movie_breakdown.pipeline.dossier_stages import CharacterDossierStageResult
from movie_breakdown.pipeline.export_integrity import exported_contents_match
from movie_breakdown.pipeline.narrative_stages import (
    GlobalStageResult,
    SceneStageResult,
)
from movie_breakdown.pipeline.runtime import PipelineStageError, StageRuntime


@dataclass(frozen=True, slots=True)
class ValidationStageResult:
    """校验报告及其内容指纹。

    Attributes:
        report: 不调用模型生成的一致性校验报告。
        artifact_fingerprint: 校验报告的内容指纹。
    """

    report: ValidationReport
    artifact_fingerprint: str


class OutputStageService:
    """执行本地校验并生成用户可见导出文件。

    Attributes:
        runtime: 当前项目的阶段运行时。
        validator: 本地引用和证据校验服务。
        exporter: JSON 与 Markdown 渲染服务。
    """

    def __init__(
        self,
        runtime: StageRuntime,
        validator: ValidationService | None = None,
        exporter: ExportService | None = None,
    ) -> None:
        """创建输出阶段服务。

        Args:
            runtime: 当前流水线运行时。
            validator: 可替换的本地校验服务。
            exporter: 可替换的报告导出服务。
        """
        self.runtime = runtime
        self.validator = validator or ValidationService()
        self.exporter = exporter or ExportService()

    def validate(
        self,
        screenplay: Artifact[Screenplay],
        scene_result: SceneStageResult,
        global_result: GlobalStageResult,
        dossiers: CharacterDossierStageResult,
        biographies: BiographyStageResult,
        *,
        force: bool = False,
    ) -> ValidationStageResult:
        """校验逐场和全局产物并保存报告。

        Args:
            screenplay: 场景切分产物。
            scene_result: 逐场分析记录及聚合指纹。
            global_result: 全局叙事分析及聚合指纹。
            dossiers: 全部已归一人物的分级档案目录。
            biographies: 人物小传目录及聚合指纹。
            force: 是否忽略已有校验缓存并重新执行。

        Returns:
            本地校验报告及其内容指纹。
        """
        cache_key = cache_fingerprint(
            screenplay.metadata.artifact_fingerprint,
            scene_result.artifact_fingerprint,
            global_result.artifact_fingerprint,
            dossiers.artifact_fingerprint,
            biographies.artifact_fingerprint,
            get_stage("validation").version,
            schema_fingerprint(ValidationReport),
        )
        if not force:
            cached = self.runtime.load_cached(
                "validation", "validation", ValidationReport, cache_key
            )
            if cached and cached.data.valid:
                return ValidationStageResult(
                    cached.data,
                    cached.metadata.artifact_fingerprint,
                )
        self.runtime.start("validation", cache_key, "执行本地一致性校验")
        report = self.validator.validate(
            screenplay.data,
            scene_result.records,
            global_result.content,
            biographies.content,
            dossiers.content,
        )
        artifact = make_artifact(
            stage_name="validation",
            cache_key=cache_key,
            data=report,
            source_fingerprint=screenplay.data.source_fingerprint,
            upstream_fingerprints=[
                screenplay.metadata.artifact_fingerprint,
                scene_result.artifact_fingerprint,
                global_result.artifact_fingerprint,
                dossiers.artifact_fingerprint,
                biographies.artifact_fingerprint,
            ],
        )
        self.runtime.store.write_artifact("validation", artifact)
        if report.valid:
            self.runtime.success(
                "validation",
                cache_key,
                artifact.metadata.artifact_fingerprint,
            )
        else:
            errors = sum(issue.severity.value == "error" for issue in report.issues)
            self._invalidate_analysis_on_errors(report)
            self.runtime.fail("validation", f"一致性校验发现 {errors} 个错误。")
        return ValidationStageResult(report, artifact.metadata.artifact_fingerprint)

    def aggregate(
        self,
        screenplay: Artifact[Screenplay],
        scene_records: list[SceneAnalysisRecord],
        global_result: GlobalStageResult,
        dossiers: CharacterDossierStageResult,
        biographies: BiographyStageResult,
        validation: ValidationReport,
    ) -> NarrativeBreakdown:
        """把分散阶段产物聚合为稳定导出模型。

        Args:
            screenplay: 场景切分产物。
            scene_records: 按剧本顺序排列的逐场记录。
            global_result: 全局叙事分析结果。
            dossiers: 全部已归一人物的分级档案目录。
            biographies: 已验证的人物小传目录。
            validation: 当前产物对应的本地校验报告。

        Returns:
            可生成 JSON 和 Markdown 的完整叙事拆解。
        """
        analyses = [record.analysis for record in scene_records if record.analysis]
        content = global_result.content
        return NarrativeBreakdown(
            screenplay=screenplay.data,
            scene_analyses=analyses,
            entities=content.entities,
            events=content.events,
            relationships=content.relationships,
            dossiers=dossiers.content,
            biographies=biographies.content,
            structure=content.structure,
            validation=validation,
        )

    def export(
        self,
        breakdown: NarrativeBreakdown,
        export_format: ExportFormat = "all",
    ) -> dict[str, str]:
        """导出报告并更新 export 阶段状态。

        Args:
            breakdown: 已通过一致性校验的完整叙事拆解。
            export_format: `markdown`、`json` 或 `all`。

        Returns:
            格式名称到导出文件绝对路径的映射。

        Raises:
            PipelineStageError: 校验未通过或文件写入失败。
        """
        breakdown_fingerprint = content_fingerprint(breakdown)
        cache_key = cache_fingerprint(
            breakdown_fingerprint,
            get_stage("export").version,
            export_format,
        )
        paths = self._expected_paths(export_format)
        expected_contents = self.exporter.render_contents(breakdown, export_format)
        record = self.runtime.manifest.stages["export"]
        if record.cache_key == cache_key and exported_contents_match(
            self.runtime.store.exports_dir,
            paths,
            expected_contents,
        ):
            self.runtime.cached("export", cache_key, breakdown_fingerprint)
            return {
                kind: str(self.runtime.store.exports_dir / name) for kind, name in paths.items()
            }
        self.runtime.start("export", cache_key, "生成 JSON 与 Markdown 报告")
        try:
            exported = self.exporter.export(
                self.runtime.store,
                breakdown,
                export_format,
            )
            self.runtime.success("export", cache_key, breakdown_fingerprint)
            return exported
        except Exception as error:
            self.runtime.fail("export", error)
            raise PipelineStageError(f"报告导出失败：{error}") from error

    @staticmethod
    def _expected_paths(export_format: ExportFormat) -> dict[str, str]:
        """返回指定导出格式应生成的固定文件名。"""
        paths = {"json": "breakdown.json", "markdown": "report.md"}
        if export_format == "all":
            return paths
        return {export_format: paths[export_format]}

    def _invalidate_analysis_on_errors(self, report: ValidationReport) -> None:
        """只让产生确定性错误的对应模型阶段在 resume 时重算。"""
        global_prefixes = (
            "global.",
            "character.",
            "location.",
            "event.",
            "relationship.",
            "relation.",
            "arc.",
            "beat.",
            "plot.",
            "foreshadow.",
            "structure.",
        )
        if any(
            issue.severity.value == "error" and issue.code.startswith(global_prefixes)
            for issue in report.issues
        ):
            record = self.runtime.manifest.stages["global_analysis"]
            record.status = StageStatus.STALE
            record.error = "下游一致性校验发现全局产物错误，需要重新分析。"
            self.runtime.store.save_manifest(self.runtime.manifest)
        if any(
            issue.severity.value == "error" and issue.code.startswith("dossier.")
            for issue in report.issues
        ):
            record = self.runtime.manifest.stages["character_dossiers"]
            record.status = StageStatus.STALE
            record.error = "下游一致性校验发现人物档案错误，需要本地重建。"
            self.runtime.store.save_manifest(self.runtime.manifest)
        if any(
            issue.severity.value == "error" and issue.code.startswith("biography.")
            for issue in report.issues
        ):
            record = self.runtime.manifest.stages["character_biographies"]
            record.status = StageStatus.STALE
            record.error = "下游一致性校验发现人物小传错误，需要重新分析。"
            self.runtime.store.save_manifest(self.runtime.manifest)
