"""纯本地评估叙事拆解是否满足稳定版发布门禁。"""

from __future__ import annotations

from movie_breakdown.domain.export import NarrativeBreakdown
from movie_breakdown.domain.quality import (
    ReviewResponse,
    ReviewTarget,
    ReviewVerdict,
    SemanticQualityReport,
)
from movie_breakdown.domain.release import (
    ReleaseGateCheck,
    ReleaseGateCheckCode,
    ReleaseGateReport,
)
from movie_breakdown.infrastructure.fingerprint import content_fingerprint

_RISK_PREFIX = "接受风险："
_CONDITIONAL_VERDICTS = {
    ReviewVerdict.PARTIALLY_SUPPORTED,
    ReviewVerdict.UNCERTAIN,
}
_BLOCKING_VERDICTS = {
    ReviewVerdict.UNREVIEWED,
    ReviewVerdict.UNSUPPORTED,
}


class ReleaseGateService:
    """使用确定性规则聚合结构校验与专家抽检结果。

    服务只读叙事拆解和语义质量报告，不调用模型，也不写入文件。
    """

    def evaluate(
        self,
        breakdown: NarrativeBreakdown,
        quality: SemanticQualityReport,
    ) -> ReleaseGateReport:
        """评估当前叙事拆解能否封版为稳定版。

        Args:
            breakdown: 待封版的完整叙事拆解。
            quality: 绑定专家抽检结果的语义质量报告。

        Returns:
            含逐条检查与总体稳定版决策的发布门禁报告。
        """
        fingerprint = content_fingerprint(breakdown)
        checks = [
            self._structural_validation_check(breakdown),
            self._fingerprint_check(quality, fingerprint),
            self._reviewer_check(quality),
            self._target_count_check(quality),
            self._completion_check(quality),
            self._verdict_check(quality),
            self._dimension_check(quality),
            self._accepted_risk_check(quality),
        ]
        return ReleaseGateReport(
            analysis_fingerprint=fingerprint,
            stable=all(check.passed for check in checks),
            checks=checks,
        )

    @staticmethod
    def _structural_validation_check(breakdown: NarrativeBreakdown) -> ReleaseGateCheck:
        """检查确定性结构校验是否通过。"""
        references = [issue.code for issue in breakdown.validation.issues]
        return _check(
            ReleaseGateCheckCode.STRUCTURAL_VALIDATION,
            "结构校验",
            breakdown.validation.valid,
            "确定性结构校验已通过。" if breakdown.validation.valid else "确定性结构校验未通过。",
            references,
        )

    @staticmethod
    def _fingerprint_check(
        quality: SemanticQualityReport,
        fingerprint: str,
    ) -> ReleaseGateCheck:
        """检查质量报告和人工评审表是否属于当前分析。"""
        report_matches = quality.analysis_fingerprint == fingerprint
        sheet_matches = quality.human_review.analysis_fingerprint == fingerprint
        passed = report_matches and sheet_matches
        references = [] if passed else [quality.analysis_fingerprint]
        return _check(
            ReleaseGateCheckCode.ANALYSIS_FINGERPRINT,
            "分析指纹",
            passed,
            "质量报告与当前叙事拆解指纹一致。" if passed else "质量报告或人工评审表已过期。",
            references,
        )

    @staticmethod
    def _reviewer_check(quality: SemanticQualityReport) -> ReleaseGateCheck:
        """检查专家评审者身份是否已填写。"""
        reviewer = quality.human_review.reviewer.strip()
        return _check(
            ReleaseGateCheckCode.REVIEWER_IDENTITY,
            "评审者身份",
            bool(reviewer),
            f"评审者已记录为：{reviewer}。" if reviewer else "评审者不能为空。",
        )

    @staticmethod
    def _target_count_check(quality: SemanticQualityReport) -> ReleaseGateCheck:
        """检查实际人工抽检目标数是否至少十六个。"""
        count = len(quality.human_review.targets)
        return _check(
            ReleaseGateCheckCode.TARGET_COUNT,
            "抽检规模",
            count >= 16,
            f"当前抽检 {count} 个目标，要求至少 16 个。",
        )

    @staticmethod
    def _completion_check(quality: SemanticQualityReport) -> ReleaseGateCheck:
        """检查人工评审摘要是否与实际响应一致且覆盖百分之百。"""
        summary = quality.human_summary
        target_count = len(quality.human_review.targets)
        actual_reviewed = sum(
            response.verdict != ReviewVerdict.UNREVIEWED
            for response in quality.human_review.responses
        )
        passed = (
            summary.target_count == target_count
            and summary.reviewed_count == summary.target_count
            and summary.reviewed_count == actual_reviewed
            and summary.coverage == 1
        )
        return _check(
            ReleaseGateCheckCode.REVIEW_COMPLETION,
            "评审完成度",
            passed,
            (
                f"评审摘要为 {summary.reviewed_count}/{summary.target_count}"
                f"，实际已评 {actual_reviewed}/{target_count}"
                f"，覆盖率 {summary.coverage:.1%}。"
            ),
        )

    @staticmethod
    def _verdict_check(quality: SemanticQualityReport) -> ReleaseGateCheck:
        """拒绝未评审或已明确不支持的抽检结论。"""
        blocked = [
            response.target_id
            for response in quality.human_review.responses
            if response.verdict in _BLOCKING_VERDICTS
        ]
        return _check(
            ReleaseGateCheckCode.REVIEW_VERDICTS,
            "评审结论",
            not blocked,
            "不存在未评审或不支持结论。"
            if not blocked
            else f"有 {len(blocked)} 个目标为未评审或不支持。",
            blocked,
        )

    @staticmethod
    def _dimension_check(quality: SemanticQualityReport) -> ReleaseGateCheck:
        """检查每个目标的所有适用维度是否均且仅评分一次。"""
        response_by_id = {
            response.target_id: response for response in quality.human_review.responses
        }
        incomplete = [
            target.id
            for target in quality.human_review.targets
            if not _has_complete_ratings(target, response_by_id[target.id])
        ]
        return _check(
            ReleaseGateCheckCode.DIMENSION_RATINGS,
            "维度评分",
            not incomplete,
            "所有适用维度均已评分。"
            if not incomplete
            else f"有 {len(incomplete)} 个目标的适用维度未完整评分。",
            incomplete,
        )

    @staticmethod
    def _accepted_risk_check(quality: SemanticQualityReport) -> ReleaseGateCheck:
        """检查部分支持或存疑结论是否已明确记录接受风险。"""
        missing = [
            response.target_id
            for response in quality.human_review.responses
            if response.verdict in _CONDITIONAL_VERDICTS
            and not _has_accepted_risk_note(response.notes)
        ]
        return _check(
            ReleaseGateCheckCode.ACCEPTED_RISKS,
            "保留结论风险接受",
            not missing,
            "所有保留结论均已记录具体的风险接受说明。"
            if not missing
            else f"有 {len(missing)} 个保留结论缺少有效的风险接受说明。",
            missing,
        )


def _check(
    code: ReleaseGateCheckCode,
    name: str,
    passed: bool,
    message: str,
    references: list[str] | None = None,
) -> ReleaseGateCheck:
    """构造格式一致的单条门禁检查。"""
    return ReleaseGateCheck(
        code=code,
        name=name,
        passed=passed,
        message=message,
        references=references or [],
    )


def _has_complete_ratings(target: ReviewTarget, response: ReviewResponse) -> bool:
    """判断一个响应是否精确覆盖目标的全部适用维度。"""
    ratings = response.ratings
    dimensions = [rating.dimension for rating in ratings]
    expected = target.dimensions
    return (
        len(dimensions) == len(expected)
        and len(dimensions) == len(set(dimensions))
        and set(dimensions) == set(expected)
        and all(rating.score is not None for rating in ratings)
    )


def _has_accepted_risk_note(notes: str) -> bool:
    """判断备注是否以约定前缀开头且包含具体说明。"""
    return notes.startswith(_RISK_PREFIX) and bool(notes.removeprefix(_RISK_PREFIX).strip())
