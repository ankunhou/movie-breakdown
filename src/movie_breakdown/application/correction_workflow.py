"""持久化并严格重放由专家评审驱动的人工叙事修正。"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ValidationError

from movie_breakdown.application.manual_corrections import NarrativeCorrectionService
from movie_breakdown.domain.export import NarrativeBreakdown
from movie_breakdown.domain.manual_correction import CorrectionReceipt, CorrectionSet
from movie_breakdown.domain.quality import HumanReviewAnswers, ReviewVerdict
from movie_breakdown.infrastructure.fingerprint import content_fingerprint
from movie_breakdown.infrastructure.storage import ProjectStore

_CORRECTABLE_VERDICTS = {
    ReviewVerdict.PARTIALLY_SUPPORTED,
    ReviewVerdict.UNSUPPORTED,
    ReviewVerdict.UNCERTAIN,
}


class CorrectionWorkflowError(ValueError):
    """人工修正的评审绑定或持久化状态不满足安全应用条件。"""


class ManualCorrectionWorkflow:
    """协调评审答案校验、修正预览、激活持久化与确定性重放。

    该工作流只处理本地 Pydantic 产物，不调用模型。修正语义由
    :class:`NarrativeCorrectionService` 负责，本类负责保证专家答案、基础分析和
    修正集合来自同一次评审，并将激活状态写入项目目录。
    """

    def __init__(self, store: ProjectStore) -> None:
        """创建绑定单个拆解项目的人工修正工作流。

        Args:
            store: 提供原子 Pydantic JSON 读写能力的项目存储。
        """
        self._store = store
        self._service = NarrativeCorrectionService()
        self._corrections_dir = store.root / "corrections"

    def preview(
        self,
        base_breakdown: NarrativeBreakdown,
        correction_set: CorrectionSet,
        answers: HumanReviewAnswers,
    ) -> tuple[NarrativeBreakdown, CorrectionReceipt]:
        """纯校验并在内存副本上预览一组专家修正。

        Args:
            base_breakdown: 尚未应用人工修正的基础叙事拆解。
            correction_set: 绑定基础分析和评审答案的修正集合。
            answers: 产生本次修正建议的完整专家评审答案。

        Returns:
            修正后的独立拆解副本及其审计回执。

        Raises:
            CorrectionWorkflowError: 答案指纹、评审身份、标准版本、目标或结论无效。
            CorrectionApplicationError: 修正集合无法安全应用到基础拆解。
        """
        self._validate_review_bindings(base_breakdown, correction_set, answers)
        return self._service.apply(base_breakdown, correction_set)

    def activate(
        self,
        base_breakdown: NarrativeBreakdown,
        correction_set: CorrectionSet,
        answers: HumanReviewAnswers,
    ) -> tuple[NarrativeBreakdown, CorrectionReceipt]:
        """完整预检后原子写入激活输入、审计回执与修正后快照。

        Args:
            base_breakdown: 尚未应用人工修正的基础叙事拆解。
            correction_set: 待设为当前激活版本的修正集合。
            answers: 与修正集合指纹及评审身份严格匹配的答案。

        Returns:
            已持久化的修正后拆解及审计回执。

        Raises:
            CorrectionWorkflowError: 评审绑定不完整或不一致。
            CorrectionApplicationError: 修正集合无法安全应用到基础拆解。
            OSError: 任一原子文件写入失败。
        """
        corrected, receipt = self.preview(base_breakdown, correction_set, answers)
        self._store.write_model(self._store.artifact_path("corrected_breakdown"), corrected)
        self._store.write_model(self._store.artifact_path("correction_receipt"), receipt)
        self._store.write_model(self._corrections_dir / "review_answers.json", answers)
        self._store.write_model(self._corrections_dir / "active.json", correction_set)
        return corrected, receipt

    def apply_active(
        self,
        base_breakdown: NarrativeBreakdown,
    ) -> tuple[NarrativeBreakdown, CorrectionReceipt | None]:
        """读取当前激活输入并在给定基础分析上严格重放。

        Args:
            base_breakdown: 当前未经人工修正的基础叙事拆解。

        Returns:
            有激活修正时返回重放结果及新回执，否则原样返回基础对象和 ``None``。

        Raises:
            CorrectionWorkflowError: 激活状态缺失配套答案或 JSON 无法通过严格校验。
            CorrectionApplicationError: 激活修正已经过期或无法安全重放。
        """
        active_path = self._corrections_dir / "active.json"
        if not active_path.is_file():
            return base_breakdown, None
        correction_set = self._read_required(active_path, CorrectionSet, "激活修正集合")
        answers = self._read_required(
            self._corrections_dir / "review_answers.json",
            HumanReviewAnswers,
            "激活修正对应的评审答案",
        )
        return self.preview(base_breakdown, correction_set, answers)

    @staticmethod
    def _validate_review_bindings(
        base_breakdown: NarrativeBreakdown,
        correction_set: CorrectionSet,
        answers: HumanReviewAnswers,
    ) -> None:
        """校验修正集合只引用同一次分析中需要修正的专家结论。"""
        base_fingerprint = content_fingerprint(base_breakdown)
        if correction_set.review_answers_fingerprint != content_fingerprint(answers):
            raise CorrectionWorkflowError("人工修正集合绑定的评审答案指纹不匹配。")
        if answers.analysis_fingerprint != base_fingerprint:
            raise CorrectionWorkflowError("专家评审答案对应的基础分析指纹已经过期。")
        if answers.analysis_fingerprint != correction_set.base_analysis_fingerprint:
            raise CorrectionWorkflowError("专家评审答案与人工修正集合的分析指纹不匹配。")
        if answers.rubric_version != correction_set.rubric_version:
            raise CorrectionWorkflowError("专家评审答案与人工修正集合的评分标准版本不匹配。")
        if answers.reviewer != correction_set.reviewer:
            raise CorrectionWorkflowError("专家评审答案与人工修正集合的评审者不匹配。")

        responses = {response.target_id: response for response in answers.responses}
        for correction in correction_set.corrections:
            response = responses.get(correction.review_target_id)
            if response is None:
                raise CorrectionWorkflowError(
                    f"修正 {correction.id} 引用的评审目标不存在：{correction.review_target_id}"
                )
            if response.verdict not in _CORRECTABLE_VERDICTS:
                raise CorrectionWorkflowError(
                    f"修正 {correction.id} 对应的评审结论不允许产生修正：{response.verdict.value}"
                )
            if not (response.proposed_correction or "").strip():
                raise CorrectionWorkflowError(
                    f"修正 {correction.id} 对应的评审答案缺少 proposed_correction。"
                )

    def _read_required[T: BaseModel](
        self,
        path: Path,
        model_type: type[T],
        label: str,
    ) -> T:
        """读取必需的严格模型文件，并把底层错误转换为可操作提示。"""
        if not path.is_file():
            raise CorrectionWorkflowError(f"{label}不存在：{path}")
        try:
            return self._store.read_model(path, model_type)
        except (OSError, ValidationError, ValueError) as error:
            raise CorrectionWorkflowError(f"{label}无效：{path}；{error}") from error
