"""以纯本地确定性门禁评估制片规划能否封版。"""

from __future__ import annotations

from collections import Counter

from movie_breakdown.application.production_release_safety import (
    missing_approval_scopes,
    missing_hazard_review_ids,
)
from movie_breakdown.domain.production_correction import ProductionCorrectionReceipt
from movie_breakdown.domain.production_planning import ProductionPlan
from movie_breakdown.domain.production_planning_validation import (
    ProductionPlanningValidationReport,
    ProductionReadinessLevel,
)
from movie_breakdown.domain.production_release import (
    ProductionReleaseCheck,
    ProductionReleaseCheckCode,
    ProductionReleaseProfile,
    ProductionReleaseReport,
    ProductionReleaseState,
)
from movie_breakdown.domain.production_review import (
    ProductionReviewerKind,
    ProductionReviewReport,
    ProductionReviewResponse,
    ProductionReviewTarget,
    ProductionReviewTargetKind,
    ProductionReviewVerdict,
)
from movie_breakdown.infrastructure.fingerprint import content_fingerprint

_BLOCKED = {
    ProductionReviewVerdict.UNREVIEWED,
    ProductionReviewVerdict.NEEDS_CORRECTION,
    ProductionReviewVerdict.BLOCKED,
}


class ProductionReleaseService:
    """纯本地聚合规划、评审和回执，并从明细重算分级封版结论。"""

    def evaluate(
        self,
        plan: ProductionPlan,
        validation: ProductionPlanningValidationReport,
        review: ProductionReviewReport,
        profile: ProductionReleaseProfile,
        correction_receipt: ProductionCorrectionReceipt | None = None,
    ) -> ProductionReleaseReport:
        """评估当前规划是否达到指定封版等级。

        Args:
            plan: 当前待封版的正式制片规划。
            validation: 当前规划的确定性分级校验报告。
            review: 当前规划完整目标集的专家评审报告。
            profile: 请求评测封版或专业稳定版。
            correction_receipt: 可选的人工修正原子应用回执。

        Returns:
            完整覆盖全部门禁项的分级发布报告。
        """
        fingerprint = content_fingerprint(plan)
        checks = [
            self._planning(validation, profile),
            self._fingerprints(fingerprint, validation, review),
            self._targets(review),
            self._completion(review),
            self._verdicts(review),
            self._ratings(review),
            self._corrections(plan, fingerprint, review, correction_receipt),
            self._reviewer(review, profile),
            self._unsafe(validation),
            self._safety(plan, review, profile),
        ]
        releasable = all(item.passed for item in checks)
        limitations = list(review.limitations)
        if review.reviewer_kind == ProductionReviewerKind.AI_SIMULATED:
            limitations.append("AI 模拟专家不能形成专业人员签署。")
        if profile == ProductionReleaseProfile.EVALUATION and not validation.shoot_ready:
            limitations.append("评测封版不代表 shoot_ready，高危拍摄仍须专业批准。")
        return ProductionReleaseReport(
            profile=profile,
            plan_fingerprint=fingerprint,
            review_target_set_fingerprint=review.target_set_fingerprint,
            state=_state(profile, releasable),
            releasable=releasable,
            checks=checks,
            limitations=list(dict.fromkeys(limitations)),
        )

    @staticmethod
    def _planning(
        validation: ProductionPlanningValidationReport,
        profile: ProductionReleaseProfile,
    ) -> ProductionReleaseCheck:
        """要求评测封版目录就绪，专业版开拍就绪。"""
        professional = profile == ProductionReleaseProfile.PROFESSIONAL
        passed = validation.shoot_ready if professional else validation.catalog_ready
        level = (
            ProductionReadinessLevel.SHOOT_READY
            if professional
            else ProductionReadinessLevel.CATALOG_READY
        )
        refs = [
            f"{item.code}:{item.reference}" if item.reference else item.code
            for item in validation.issues
            if level in item.blocks_levels
        ]
        return _gate(
            "planning_validation",
            "规划准备度",
            passed,
            f"要求达到 {level.value}。",
            refs,
        )

    @staticmethod
    def _fingerprints(
        fingerprint: str,
        validation: ProductionPlanningValidationReport,
        review: ProductionReviewReport,
    ) -> ProductionReleaseCheck:
        """核对规划、校验和评审属于同一内容版本。"""
        stale = []
        if validation.plan_fingerprint != fingerprint:
            stale.append("validation")
        if review.plan_fingerprint != fingerprint:
            stale.append("review")
        return _gate("plan_fingerprint", "规划指纹", not stale, "报告须绑定当前规划。", stale)

    @staticmethod
    def _targets(review: ProductionReviewReport) -> ProductionReleaseCheck:
        """重算目标集指纹，拒绝删改目标或政策。"""
        expected = content_fingerprint(
            [review.rubric_version, review.safety_policy_version, review.targets]
        )
        passed = review.target_set_fingerprint == expected
        refs = [] if passed else [review.target_set_fingerprint]
        return _gate("target_set_fingerprint", "目标集指纹", passed, "目标与政策须完整。", refs)

    @staticmethod
    def _completion(review: ProductionReviewReport) -> ProductionReleaseCheck:
        """从明细重算覆盖率并要求所有目标均已评。"""
        targets = [item.id for item in review.targets]
        responses = [item.target_id for item in review.responses]
        reviewed = sum(
            item.verdict != ProductionReviewVerdict.UNREVIEWED for item in review.responses
        )
        passed = (
            len(targets) == len(set(targets))
            and Counter(targets) == Counter(responses)
            and reviewed == len(targets)
            and review.reviewed_count == reviewed
            and review.target_count == len(targets)
            and review.coverage == (1 if targets else 0)
            and review.complete
        )
        return _gate("review_completion", "评审完成度", passed, f"已评 {reviewed}/{len(targets)}。")

    @staticmethod
    def _verdicts(review: ProductionReviewReport) -> ProductionReleaseCheck:
        """拒绝未评、待修正、阻断及高危目标的风险接受。"""
        critical = {
            item.id
            for item in review.targets
            if item.kind
            in {
                ProductionReviewTargetKind.SAFETY_HAZARD,
                ProductionReviewTargetKind.UNSAFE_DEFAULT,
            }
        }
        blocked = [
            item.target_id
            for item in review.responses
            if item.verdict in _BLOCKED
            or (
                item.verdict == ProductionReviewVerdict.ACCEPTED_RISK
                and (item.target_id in critical or not item.notes.strip())
            )
        ]
        return _gate("review_verdicts", "评审结论", not blocked, "结论须全部关闭。", blocked)

    @staticmethod
    def _ratings(review: ProductionReviewReport) -> ProductionReleaseCheck:
        """要求每个目标的适用维度均且仅评分一次。"""
        responses = {item.target_id: item for item in review.responses}
        incomplete = [
            target.id
            for target in review.targets
            if target.id not in responses or not _ratings_complete(target, responses[target.id])
        ]
        return _gate(
            "dimension_ratings", "维度评分", not incomplete, "维度须完整评分。", incomplete
        )

    @staticmethod
    def _corrections(
        plan: ProductionPlan,
        fingerprint: str,
        review: ProductionReviewReport,
        receipt: ProductionCorrectionReceipt | None,
    ) -> ProductionReleaseCheck:
        """要求评审声明的修正绑定当前规划回执。"""
        declared = {value for item in review.responses for value in item.correction_ids}
        passed = (
            not declared
            if receipt is None
            else (
                receipt.source_fingerprint == plan.source_fingerprint
                and receipt.corrected_plan_fingerprint == fingerprint
                and declared <= set(receipt.applied_correction_ids)
            )
        )
        return _gate("correction_receipt", "修正回执", passed, "修正链须绑定当前规划。", declared)

    @staticmethod
    def _reviewer(
        review: ProductionReviewReport,
        profile: ProductionReleaseProfile,
    ) -> ProductionReleaseCheck:
        """要求实名评审，且专业版只能由真人专家签署。"""
        professional = profile == ProductionReleaseProfile.PROFESSIONAL
        passed = bool(review.reviewer.strip()) and (
            not professional or review.reviewer_kind == ProductionReviewerKind.HUMAN_EXPERT
        )
        refs = [] if passed else [review.reviewer_kind.value]
        return _gate("reviewer_identity", "评审者身份", passed, "身份须满足封版等级。", refs)

    @staticmethod
    def _unsafe(
        validation: ProductionPlanningValidationReport,
    ) -> ProductionReleaseCheck:
        """拒绝仍存在或已过期的危险默认实现决定。"""
        codes = {"planning.unsafe_default", "planning.safety_method_stale"}
        refs = [item.reference or item.code for item in validation.issues if item.code in codes]
        return _gate("unsafe_defaults", "危险默认实现", not refs, "危险方法须已明确否决。", refs)

    @staticmethod
    def _safety(
        plan: ProductionPlan,
        review: ProductionReviewReport,
        profile: ProductionReleaseProfile,
    ) -> ProductionReleaseCheck:
        """核对全量高危评审，并为专业版核对逐角色资质批准。"""
        refs = missing_hazard_review_ids(plan, review)
        if profile == ProductionReleaseProfile.PROFESSIONAL:
            refs.extend(missing_approval_scopes(plan))
        return _gate("safety_approvals", "高危安全复核", not refs, "高危范围须闭环。", refs)


def _gate(code, name: str, passed: bool, message: str, references=()) -> ProductionReleaseCheck:
    """构造格式一致的单条门禁检查。"""
    return ProductionReleaseCheck(
        code=ProductionReleaseCheckCode(code),
        name=name,
        passed=passed,
        message=message,
        references=sorted(set(references)),
    )


def _state(profile: ProductionReleaseProfile, passed: bool) -> ProductionReleaseState:
    """把等级和总体结果映射为唯一发布状态。"""
    if not passed:
        return ProductionReleaseState.BLOCKED
    if profile == ProductionReleaseProfile.PROFESSIONAL:
        return ProductionReleaseState.PROFESSIONAL_STABLE
    return ProductionReleaseState.EVALUATION_READY


def _ratings_complete(
    target: ProductionReviewTarget,
    response: ProductionReviewResponse,
) -> bool:
    """判断响应是否精确覆盖目标全部适用维度。"""
    dimensions = [item.dimension for item in response.ratings]
    return (
        len(dimensions) == len(set(dimensions)) == len(target.dimensions)
        and set(dimensions) == set(target.dimensions)
        and all(item.score is not None for item in response.ratings)
    )
