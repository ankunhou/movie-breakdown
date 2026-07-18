"""组合自动代理信号、风险抽样和人工答案的叙事质量服务。"""

from __future__ import annotations

from collections import defaultdict

from movie_breakdown.application.quality_sampling import sample_review_targets
from movie_breakdown.application.quality_signals import collect_automatic_signals
from movie_breakdown.domain.export import NarrativeBreakdown
from movie_breakdown.domain.quality import (
    DimensionRating,
    HumanReviewAnswers,
    HumanReviewSheet,
    QualityDimension,
    ReviewResponse,
    ReviewSummary,
    ReviewTarget,
    ReviewVerdict,
    SemanticQualityReport,
)
from movie_breakdown.infrastructure.fingerprint import content_fingerprint

RUBRIC_VERSION = "1.1"


class StaleReviewAnswersError(ValueError):
    """人工答案属于其他分析版本或旧评测标准。"""


class NarrativeQualityService:
    """生成不伪装成正确率的叙事质量代理信号与人工抽检表。"""

    def review(
        self,
        breakdown: NarrativeBreakdown,
        sample_size: int = 16,
        answers: HumanReviewAnswers | None = None,
    ) -> SemanticQualityReport:
        """评估自动风险并合并与当前分析指纹匹配的人工答案。

        Args:
            breakdown: 已通过确定性一致性校验的完整叙事拆解。
            sample_size: 稳定风险抽样的目标数量，范围为 6 到 50。
            answers: 可选的人工评测答案；允许只完成部分目标。

        Returns:
            自动信号和人工结论严格分离的语义质量报告。

        Raises:
            StaleReviewAnswersError: 答案指纹、标准版本或目标 ID 已过期。
            ValueError: 抽样数量不在允许范围内。
        """
        fingerprint = content_fingerprint(breakdown)
        signals = collect_automatic_signals(breakdown)
        targets = sample_review_targets(breakdown, signals, sample_size)
        responses, reviewer = self._merge_answers(targets, fingerprint, answers)
        sheet = HumanReviewSheet(
            analysis_fingerprint=fingerprint,
            rubric_version=RUBRIC_VERSION,
            reviewer=reviewer,
            targets=targets,
            responses=responses,
        )
        return SemanticQualityReport(
            analysis_fingerprint=fingerprint,
            source_fingerprint=breakdown.screenplay.source_fingerprint,
            rubric_version=RUBRIC_VERSION,
            automatic_signals=signals,
            human_review=sheet,
            human_summary=self._summarize(responses, len(targets)),
            limitations=[
                "自动信号是风险代理，不是叙事判断正确率。",
                "主题、人物弧光和转折点是否成立，必须由理解剧本语境的评审者判断。",
                "人物小传已区分剧本呈现、角色转述和分析推断，但分类与人物理解仍需人工核对。",
                "抽检只代表当前样本；未抽中的结论不能视为已经人工确认。",
            ],
        )

    def answers_template(self, report: SemanticQualityReport) -> HumanReviewAnswers:
        """从报告生成可填写并可再次导入的严格人工答案模板。

        Args:
            report: 已生成的叙事语义质量报告。

        Returns:
            绑定分析指纹与评测标准版本的答案模板。
        """
        return HumanReviewAnswers(
            analysis_fingerprint=report.analysis_fingerprint,
            rubric_version=report.rubric_version,
            reviewer=report.human_review.reviewer,
            responses=report.human_review.responses,
        )

    def _merge_answers(
        self,
        targets: list[ReviewTarget],
        fingerprint: str,
        answers: HumanReviewAnswers | None,
    ) -> tuple[list[ReviewResponse], str]:
        """验证答案新鲜度并为未填写目标保留空白响应。"""
        defaults = {target.id: self._empty_response(target) for target in targets}
        if answers is None:
            return list(defaults.values()), ""
        if answers.analysis_fingerprint != fingerprint:
            raise StaleReviewAnswersError("人工评测答案对应的分析指纹已经过期。")
        if answers.rubric_version != RUBRIC_VERSION:
            raise StaleReviewAnswersError("人工评测答案使用的评分标准版本已经过期。")
        unknown = {response.target_id for response in answers.responses} - set(defaults)
        if unknown:
            names = "、".join(sorted(unknown))
            raise StaleReviewAnswersError(f"人工评测答案引用了当前抽样之外的目标：{names}")
        target_by_id = {target.id: target for target in targets}
        for response in answers.responses:
            allowed = set(target_by_id[response.target_id].dimensions)
            supplied = [rating.dimension for rating in response.ratings]
            if len(supplied) != len(set(supplied)) or not set(supplied) <= allowed:
                raise StaleReviewAnswersError(
                    f"人工评测答案包含重复或不适用维度：{response.target_id}"
                )
            defaults[response.target_id] = response
        return list(defaults.values()), answers.reviewer

    @staticmethod
    def _empty_response(target: ReviewTarget) -> ReviewResponse:
        """为单个目标创建各适用维度均待填写的响应。"""
        return ReviewResponse(
            target_id=target.id,
            ratings=[DimensionRating(dimension=dimension) for dimension in target.dimensions],
        )

    @staticmethod
    def _summarize(responses: list[ReviewResponse], target_count: int) -> ReviewSummary:
        """统计人工覆盖率、结论分布和已填写维度均分。"""
        counts = {verdict: 0 for verdict in ReviewVerdict}
        scores: defaultdict[QualityDimension, list[int]] = defaultdict(list)
        flagged = []
        reviewed = 0
        for response in responses:
            counts[response.verdict] += 1
            if response.verdict != ReviewVerdict.UNREVIEWED:
                reviewed += 1
            if response.verdict in {
                ReviewVerdict.PARTIALLY_SUPPORTED,
                ReviewVerdict.UNSUPPORTED,
                ReviewVerdict.UNCERTAIN,
            }:
                flagged.append(response.target_id)
            for rating in response.ratings:
                if rating.score is not None:
                    scores[rating.dimension].append(rating.score)
        averages = {
            dimension: round(sum(values) / len(values), 2)
            for dimension, values in scores.items()
            if values
        }
        return ReviewSummary(
            reviewed_count=reviewed,
            target_count=target_count,
            coverage=reviewed / target_count if target_count else 0,
            verdict_counts=counts,
            dimension_averages=averages,
            flagged_target_ids=flagged,
        )
