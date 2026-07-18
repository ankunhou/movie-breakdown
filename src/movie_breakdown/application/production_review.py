"""生成制片规划强制目标并合并与当前指纹匹配的专家答案。"""

from __future__ import annotations

from movie_breakdown.application.production_review_targets import (
    build_production_review_targets,
)
from movie_breakdown.domain.production_catalog import ProductionBreakdown
from movie_breakdown.domain.production_planning import ProductionPlan
from movie_breakdown.domain.production_review import (
    ProductionDimensionRating,
    ProductionReviewAnswers,
    ProductionReviewerKind,
    ProductionReviewReport,
    ProductionReviewResponse,
    ProductionReviewTarget,
    ProductionReviewVerdict,
)
from movie_breakdown.domain.source import Screenplay
from movie_breakdown.infrastructure.fingerprint import content_fingerprint

PRODUCTION_RUBRIC_VERSION = "1.0"
PRODUCTION_SAFETY_POLICY_VERSION = "1.0"


class StaleProductionReviewAnswersError(ValueError):
    """制片专家答案属于其他规划、目标集或旧评审政策。"""


class ProductionReviewService:
    """生成全量强制制片目标并严格合并可外部填写的答案。"""

    def review(
        self,
        screenplay: Screenplay,
        breakdown: ProductionBreakdown,
        plan: ProductionPlan,
        answers: ProductionReviewAnswers | None = None,
    ) -> ProductionReviewReport:
        """生成当前规划的专家表或导入已经填写的答案。

        Args:
            screenplay: 当前共享剧本和场景顺序。
            breakdown: 提供评审风险来源的基础拆解。
            plan: 当前正式制片规划。
            answers: 可选的制片专家答案。

        Returns:
            目标、答案和完成度严格绑定的评审报告。

        Raises:
            StaleProductionReviewAnswersError: 答案指纹、政策或目标已经过期。
        """
        plan_fingerprint = content_fingerprint(plan)
        targets = build_production_review_targets(screenplay, breakdown, plan)
        target_set_fingerprint = content_fingerprint(
            [
                PRODUCTION_RUBRIC_VERSION,
                PRODUCTION_SAFETY_POLICY_VERSION,
                targets,
            ]
        )
        responses, reviewer, reviewer_kind, roles = self._merge_answers(
            targets,
            plan_fingerprint,
            target_set_fingerprint,
            answers,
        )
        reviewed = sum(item.verdict != ProductionReviewVerdict.UNREVIEWED for item in responses)
        blocked = [
            item.target_id
            for item in responses
            if item.verdict
            in {ProductionReviewVerdict.NEEDS_CORRECTION, ProductionReviewVerdict.BLOCKED}
        ]
        return ProductionReviewReport(
            plan_fingerprint=plan_fingerprint,
            target_set_fingerprint=target_set_fingerprint,
            rubric_version=PRODUCTION_RUBRIC_VERSION,
            safety_policy_version=PRODUCTION_SAFETY_POLICY_VERSION,
            reviewer=reviewer,
            reviewer_kind=reviewer_kind,
            reviewer_roles=roles,
            targets=targets,
            responses=responses,
            reviewed_count=reviewed,
            target_count=len(targets),
            coverage=reviewed / len(targets) if targets else 0,
            complete=reviewed == len(targets),
            blocked_target_ids=blocked,
            limitations=[
                "AI 模拟专家只能形成评测封版，不能替代真人专业签署。",
                "高危候选必须由所有指定专业角色对固定范围分别批准，才能进入 shoot_ready。",
                "封版表示制片拆解底稿受控，不代表预算、通告或具体高危拍摄方案已经批准。",
            ],
        )

    def answers_template(self, report: ProductionReviewReport) -> ProductionReviewAnswers:
        """从评审报告生成可填写的严格答案模板。

        Args:
            report: 当前规划的完整制片评审报告。

        Returns:
            绑定规划、目标集和双版本政策的答案模板。
        """
        return ProductionReviewAnswers(
            plan_fingerprint=report.plan_fingerprint,
            target_set_fingerprint=report.target_set_fingerprint,
            rubric_version=report.rubric_version,
            safety_policy_version=report.safety_policy_version,
            reviewer=report.reviewer,
            reviewer_kind=report.reviewer_kind,
            reviewer_roles=report.reviewer_roles,
            responses=report.responses,
        )

    def _merge_answers(
        self,
        targets: list[ProductionReviewTarget],
        plan_fingerprint: str,
        target_set_fingerprint: str,
        answers: ProductionReviewAnswers | None,
    ) -> tuple[list[ProductionReviewResponse], str, ProductionReviewerKind, list[str]]:
        """校验答案身份并为未填写目标保留完整空白维度。"""
        defaults = {item.id: _empty_response(item) for item in targets}
        if answers is None:
            return list(defaults.values()), "", ProductionReviewerKind.AI_SIMULATED, []
        expected = (
            answers.plan_fingerprint == plan_fingerprint
            and answers.target_set_fingerprint == target_set_fingerprint
            and answers.rubric_version == PRODUCTION_RUBRIC_VERSION
            and answers.safety_policy_version == PRODUCTION_SAFETY_POLICY_VERSION
        )
        if not expected:
            raise StaleProductionReviewAnswersError("制片专家答案的规划、目标集或政策已经过期。")
        unknown = {item.target_id for item in answers.responses} - set(defaults)
        if unknown:
            raise StaleProductionReviewAnswersError(
                f"制片专家答案引用未知目标：{'、'.join(sorted(unknown))}"
            )
        target_by_id = {item.id: item for item in targets}
        for response in answers.responses:
            supplied = [item.dimension for item in response.ratings]
            allowed = set(target_by_id[response.target_id].dimensions)
            if len(supplied) != len(set(supplied)) or not set(supplied) <= allowed:
                raise StaleProductionReviewAnswersError(
                    f"制片专家答案包含重复或不适用维度：{response.target_id}"
                )
            defaults[response.target_id] = response
        return (
            list(defaults.values()),
            answers.reviewer,
            answers.reviewer_kind,
            answers.reviewer_roles,
        )


def _empty_response(target: ProductionReviewTarget) -> ProductionReviewResponse:
    """为单个强制目标创建全部维度均待填写的响应。"""
    return ProductionReviewResponse(
        target_id=target.id,
        ratings=[ProductionDimensionRating(dimension=item) for item in target.dimensions],
    )
