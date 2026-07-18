"""编排专家人工修正与叙事稳定版封版用例。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from movie_breakdown.application.correction_workflow import ManualCorrectionWorkflow
from movie_breakdown.application.pipeline import AnalysisPipeline
from movie_breakdown.application.release_exporting import ReleaseGateExporter
from movie_breakdown.application.release_gate import ReleaseGateService
from movie_breakdown.domain.manual_correction import CorrectionReceipt, CorrectionSet
from movie_breakdown.domain.quality import HumanReviewAnswers, SemanticQualityReport
from movie_breakdown.domain.release import ReleaseGateReport
from movie_breakdown.infrastructure.fingerprint import content_fingerprint
from movie_breakdown.infrastructure.storage import ProjectStore


@dataclass(frozen=True, slots=True)
class CorrectionRunResult:
    """人工修正预览或激活后的用户可见结果。

    Attributes:
        dry_run: 本次是否只预览而没有写入项目。
        analysis_fingerprint: 嵌入回执后的修正聚合指纹。
        receipt: 已完成全量校验的确定性修正回执。
        exports: 正式激活后更新的报告路径；预览时为空。
    """

    dry_run: bool
    analysis_fingerprint: str
    receipt: CorrectionReceipt
    exports: dict[str, str]


@dataclass(frozen=True, slots=True)
class ReleaseRunResult:
    """稳定版门禁决策及其持久化路径。

    Attributes:
        report: 绑定当前正式聚合指纹的门禁报告。
        exports: 门禁 JSON 与 Markdown 文件路径。
    """

    report: ReleaseGateReport
    exports: dict[str, str]


class NarrativeClosureService:
    """提供不调用模型的专家修正、重导出和稳定版门禁。"""

    def __init__(self, store: ProjectStore) -> None:
        """创建绑定单个拆解项目的收尾服务。

        Args:
            store: 当前剧本拆解项目存储。
        """
        self.store = store

    def apply_corrections(
        self,
        correction_path: Path,
        answers_path: Path,
        *,
        dry_run: bool = False,
    ) -> CorrectionRunResult:
        """预览或激活一组与专家答案严格绑定的人工修正。

        Args:
            correction_path: 待应用的严格 ``CorrectionSet`` JSON。
            answers_path: 产生修正建议的专家答案 JSON。
            dry_run: 是否只验证和预览，不写入项目。

        Returns:
            修正回执、正式指纹和可选导出路径。

        Raises:
            OSError: 输入文件无法读取或正式产物无法写入。
            ValueError: 输入 Schema、指纹、评审绑定或修正目标无效。
        """
        pipeline = AnalysisPipeline(self.store)
        base = pipeline.load_base_breakdown(read_only=dry_run)
        correction_set = self.store.read_model(correction_path, CorrectionSet)
        answers = self.store.read_model(answers_path, HumanReviewAnswers)
        workflow = ManualCorrectionWorkflow(self.store)
        if dry_run:
            corrected, receipt = workflow.preview(base, correction_set, answers)
            official = corrected.model_copy(update={"correction_receipt": receipt})
            return CorrectionRunResult(
                dry_run=True,
                analysis_fingerprint=content_fingerprint(official),
                receipt=receipt,
                exports={},
            )
        workflow.activate(base, correction_set, answers)
        official = pipeline.load_breakdown()
        exports = pipeline.export_only("all")
        if official.correction_receipt is None:
            raise ValueError("人工修正激活后没有生成正式回执。")
        return CorrectionRunResult(
            dry_run=False,
            analysis_fingerprint=content_fingerprint(official),
            receipt=official.correction_receipt,
            exports=exports,
        )

    def finalize(self) -> ReleaseRunResult:
        """使用当前专家评审结果执行并导出稳定版门禁。

        Returns:
            无论通过或阻断都已持久化的门禁决策与路径。

        Raises:
            OSError: 质量报告缺失或门禁产物无法写入。
            ValueError: 质量报告损坏或与当前正式分析不匹配。
        """
        breakdown = AnalysisPipeline(self.store).load_breakdown()
        quality = self.store.read_model(
            self.store.artifact_path("semantic_quality"),
            SemanticQualityReport,
        )
        report = ReleaseGateService().evaluate(breakdown, quality)
        exports = ReleaseGateExporter().export(self.store, report)
        return ReleaseRunResult(report=report, exports=exports)
