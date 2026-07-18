"""原子校验并应用与专家答案绑定的结构化制片规划修正。"""

from __future__ import annotations

from collections import Counter

from movie_breakdown.application.production_correction_operations import (
    ProductionCorrectionOperationApplier,
)
from movie_breakdown.application.production_plan_validation import (
    ProductionPlanValidationService,
)
from movie_breakdown.application.production_plan_validation_support import (
    evidence_is_located,
)
from movie_breakdown.application.production_review import ProductionReviewService
from movie_breakdown.domain.production_catalog import ProductionBreakdown
from movie_breakdown.domain.production_correction import (
    ProductionCorrectionOperation,
    ProductionCorrectionReceipt,
    ProductionCorrectionSet,
    ReplaceEntityRegistryCorrection,
    ReplacePlannedQuantitiesCorrection,
    ReplaceResourceClassesCorrection,
    ReplaceSafetyApprovalsCorrection,
    ReplaceSafetyMethodsCorrection,
    ReplaceSceneQuantitiesCorrection,
    ReplaceShootingUnitsCorrection,
)
from movie_breakdown.domain.production_planning import ProductionPlan
from movie_breakdown.domain.production_planning_validation import (
    ProductionPlanningIssue,
    ProductionReadinessLevel,
)
from movie_breakdown.domain.production_review import (
    ProductionReviewAnswers,
    ProductionReviewVerdict,
)
from movie_breakdown.domain.source import Screenplay
from movie_breakdown.infrastructure.fingerprint import content_fingerprint


class ProductionCorrectionError(ValueError):
    """制片规划修正无法安全应用时的基础错误。"""


class StaleProductionCorrectionError(ProductionCorrectionError):
    """修正集、专家答案或基础规划已经过期。"""


class ProductionCorrectionBindingError(ProductionCorrectionError):
    """修正操作没有与专家目标及答案形成双向绑定。"""


class ProductionCorrectionTargetError(ProductionCorrectionError):
    """修正作用域或声明的旧值指纹与当前规划不一致。"""


class ProductionCorrectionEvidenceError(ProductionCorrectionError):
    """修正依据无法在当前剧本按行号和摘录逐字定位。"""


class ProductionCorrectionValidationError(ProductionCorrectionError):
    """应用修正后的规划无法通过最低草稿级确定性校验。"""


class ProductionCorrectionService:
    """预检整组制片修正并在规划深拷贝上原子应用。"""

    def __init__(
        self,
        applier: ProductionCorrectionOperationApplier | None = None,
        validator: ProductionPlanValidationService | None = None,
        reviewer: ProductionReviewService | None = None,
    ) -> None:
        """创建可替换操作器、校验器和评审器的修正服务。

        Args:
            applier: 负责具体结构替换和安全候选重建的操作器。
            validator: 负责修正后完整规划校验的服务。
            reviewer: 负责重建当前强制目标并校验答案指纹的服务。
        """
        self._applier = applier or ProductionCorrectionOperationApplier()
        self._validator = validator or ProductionPlanValidationService()
        self._reviewer = reviewer or ProductionReviewService()

    def apply(
        self,
        screenplay: Screenplay,
        breakdown: ProductionBreakdown,
        base_plan: ProductionPlan,
        correction_set: ProductionCorrectionSet,
        answers: ProductionReviewAnswers,
    ) -> tuple[ProductionPlan, ProductionCorrectionReceipt]:
        """预检并应用与当前强制评审答案绑定的完整累计修正集。

        Args:
            screenplay: 当前共享剧本及可定位原文。
            breakdown: 修正规划所绑定的只读基础制片拆解。
            base_plan: 尚未应用本修正集的当前基础规划。
            correction_set: 带旧值指纹、证据和目标引用的结构化修正集。
            answers: 触发这些修正的完整专家答案。

        Returns:
            独立的修正规划及完整指纹审计回执。

        Raises:
            StaleProductionCorrectionError: 任一输入或政策指纹已经过期。
            ProductionCorrectionBindingError: 修正与评审答案没有双向绑定。
            ProductionCorrectionTargetError: 作用域或旧值指纹不匹配。
            ProductionCorrectionEvidenceError: 修正证据无法逐字定位。
            ProductionCorrectionValidationError: 修正后规划不满足草稿级门禁。
        """
        self._validate_bindings(screenplay, base_plan, correction_set, answers)
        report = self._reviewer.review(screenplay, breakdown, base_plan, answers)
        self._validate_review_bindings(report, correction_set)
        self._preflight(screenplay, base_plan, correction_set.corrections)
        working = base_plan.model_copy(deep=True)
        corrected = self._applier.apply(working, correction_set.corrections, breakdown.scenes)
        validation = self._validator.validate(screenplay, breakdown, corrected)
        if not validation.draft_valid:
            errors = _blocking_issue_summary(
                validation.issues,
                ProductionReadinessLevel.DRAFT_VALID,
            )
            raise ProductionCorrectionValidationError(
                f"修正后规划未通过 draft_valid：{', '.join(errors)}"
            )
        receipt = ProductionCorrectionReceipt(
            source_fingerprint=correction_set.source_fingerprint,
            base_plan_fingerprint=correction_set.base_plan_fingerprint,
            corrected_plan_fingerprint=content_fingerprint(corrected),
            target_set_fingerprint=correction_set.target_set_fingerprint,
            correction_set_fingerprint=content_fingerprint(correction_set),
            review_answers_fingerprint=correction_set.review_answers_fingerprint,
            rubric_version=correction_set.rubric_version,
            safety_policy_version=correction_set.safety_policy_version,
            reviewer=correction_set.reviewer,
            reviewer_kind=correction_set.reviewer_kind,
            applied_correction_ids=[item.id for item in correction_set.corrections],
            applied_count=len(correction_set.corrections),
        )
        return corrected, receipt

    @staticmethod
    def _validate_bindings(
        screenplay: Screenplay,
        base_plan: ProductionPlan,
        correction_set: ProductionCorrectionSet,
        answers: ProductionReviewAnswers,
    ) -> None:
        """拒绝来源、规划、答案、政策或评审者不一致的修正集。"""
        expected = (
            correction_set.source_fingerprint == screenplay.source_fingerprint
            and correction_set.source_fingerprint == base_plan.source_fingerprint
            and correction_set.base_plan_fingerprint == content_fingerprint(base_plan)
            and correction_set.review_answers_fingerprint == content_fingerprint(answers)
            and correction_set.target_set_fingerprint == answers.target_set_fingerprint
            and correction_set.rubric_version == answers.rubric_version
            and correction_set.safety_policy_version == answers.safety_policy_version
            and correction_set.reviewer == answers.reviewer
            and correction_set.reviewer_kind == answers.reviewer_kind
            and answers.plan_fingerprint == content_fingerprint(base_plan)
        )
        if not expected:
            raise StaleProductionCorrectionError(
                "制片修正集的剧本、规划、答案、政策或评审者绑定已经过期。"
            )

    @staticmethod
    def _validate_review_bindings(report, correction_set: ProductionCorrectionSet) -> None:
        """要求每条修正与需要修正的目标形成严格双向引用。"""
        responses = {item.target_id: item for item in report.responses}
        corrections = {item.id: item for item in correction_set.corrections}
        for correction in correction_set.corrections:
            for target_id in correction.review_target_ids:
                response = responses.get(target_id)
                if (
                    response is None
                    or response.verdict != ProductionReviewVerdict.NEEDS_CORRECTION
                    or correction.id not in response.correction_ids
                ):
                    raise ProductionCorrectionBindingError(
                        f"修正 {correction.id} 未与需要修正的评审目标 {target_id} 双向绑定。"
                    )
        for response in report.responses:
            for correction_id in response.correction_ids:
                correction = corrections.get(correction_id)
                if correction is None or response.target_id not in correction.review_target_ids:
                    raise ProductionCorrectionBindingError(
                        f"评审目标 {response.target_id} 引用了未双向绑定的修正 {correction_id}。"
                    )

    def _preflight(
        self,
        screenplay: Screenplay,
        plan: ProductionPlan,
        operations: list[ProductionCorrectionOperation],
    ) -> None:
        """在任何替换前一次性检查全部旧值指纹与逐字证据。"""
        scenes = {item.id: item for item in screenplay.scenes}
        for operation in operations:
            expected_value = _current_scope_value(plan, operation)
            if content_fingerprint(expected_value) != operation.expected_value_fingerprint:
                raise ProductionCorrectionTargetError(
                    f"修正 {operation.id} 的作用域不存在或旧值指纹已经过期。"
                )
            for evidence in operation.evidence:
                scene = scenes.get(evidence.scene_id)
                if scene is None or not evidence_is_located(scene, evidence):
                    raise ProductionCorrectionEvidenceError(
                        f"修正 {operation.id} 的证据无法在 {evidence.scene_id} 逐字定位。"
                    )


def _current_scope_value(
    plan: ProductionPlan,
    operation: ProductionCorrectionOperation,
):
    """返回一种结构化修正当前完整作用域的稳定旧值。"""
    if isinstance(operation, ReplaceShootingUnitsCorrection):
        return [item for item in plan.shooting_units if item.scene_id == operation.scene_id]
    if isinstance(operation, ReplaceEntityRegistryCorrection):
        return plan.entities
    if isinstance(operation, ReplaceResourceClassesCorrection):
        return plan.resource_classes
    if isinstance(operation, ReplaceSceneQuantitiesCorrection):
        occurrence_ids = {
            item.id for item in plan.occurrences if item.scene_id == operation.scene_id
        }
        return [item for item in plan.quantity_facts if item.occurrence_id in occurrence_ids]
    if isinstance(operation, ReplacePlannedQuantitiesCorrection):
        return plan.planned_quantities
    if isinstance(operation, ReplaceSafetyMethodsCorrection):
        return plan.safety_method_decisions
    if isinstance(operation, ReplaceSafetyApprovalsCorrection):
        return plan.safety_approvals
    raise ProductionCorrectionTargetError("不支持的制片修正操作。")


def _blocking_issue_summary(
    issues: list[ProductionPlanningIssue],
    level: ProductionReadinessLevel,
) -> list[str]:
    """按代码汇总真正阻断指定准备度的问题，避免跨级噪声。"""
    counts = Counter(item.code for item in issues if level in item.blocks_levels)
    return [f"{code}×{count}" if count > 1 else code for code, count in sorted(counts.items())]
