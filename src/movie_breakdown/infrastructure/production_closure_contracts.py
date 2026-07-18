"""制片闭环内容寻址 generation 与不可变发布归档契约。"""

from __future__ import annotations

from typing import Self

from pydantic import Field, model_validator

from movie_breakdown.domain.base import StrictModel
from movie_breakdown.domain.production_correction import (
    ProductionCorrectionReceipt,
    ProductionCorrectionSet,
)
from movie_breakdown.domain.production_planning import ProductionPlan
from movie_breakdown.domain.production_planning_validation import (
    ProductionPlanningValidationReport,
)
from movie_breakdown.domain.production_release import (
    ProductionReleaseProfile,
    ProductionReleaseReport,
)
from movie_breakdown.domain.production_review import (
    ProductionReviewAnswers,
    ProductionReviewReport,
)
from movie_breakdown.infrastructure.fingerprint import content_fingerprint

_FINGERPRINT_PATTERN = r"^[0-9a-f]{64}$"


class ProductionGenerationManifest(StrictModel):
    """记录一个完整人工修正 generation 的全部内容指纹。"""

    schema_version: str = "1.0"
    generation_id: str = Field(pattern=_FINGERPRINT_PATTERN)
    source_fingerprint: str = Field(min_length=1, max_length=128)
    base_plan_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    target_set_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    correction_set_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    review_answers_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    receipt_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    official_plan_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    validation_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def _validate_generation_id(self) -> Self:
        """拒绝与清单其余字段不一致的 generation ID。"""
        payload = self.model_dump(mode="json", exclude={"generation_id"})
        if self.generation_id != content_fingerprint(payload):
            raise ValueError("制片修正 generation ID 与内容指纹不一致。")
        return self


class ProductionCorrectionGeneration(StrictModel):
    """聚合并交叉校验一次人工修正 generation 的全部严格产物。"""

    manifest: ProductionGenerationManifest
    correction_set: ProductionCorrectionSet
    review_answers: ProductionReviewAnswers
    receipt: ProductionCorrectionReceipt
    official_plan: ProductionPlan
    validation: ProductionPlanningValidationReport

    @model_validator(mode="after")
    def _validate_bindings(self) -> Self:
        """校验输入、回执、正式规划与清单的全部指纹绑定。"""
        fingerprints = {
            "correction_set_fingerprint": content_fingerprint(self.correction_set),
            "review_answers_fingerprint": content_fingerprint(self.review_answers),
            "receipt_fingerprint": content_fingerprint(self.receipt),
            "official_plan_fingerprint": content_fingerprint(self.official_plan),
            "validation_fingerprint": content_fingerprint(self.validation),
        }
        for name, value in fingerprints.items():
            if getattr(self.manifest, name) != value:
                raise ValueError(f"制片修正 generation 的 {name} 不匹配。")
        self._validate_audit_chain(fingerprints)
        return self

    def _validate_audit_chain(self, fingerprints: dict[str, str]) -> None:
        """校验领域产物之间不能绕过的审计链。"""
        correction = self.correction_set
        answers = self.review_answers
        receipt = self.receipt
        plan_fingerprint = fingerprints["official_plan_fingerprint"]
        shared = (
            correction.target_set_fingerprint,
            answers.target_set_fingerprint,
            receipt.target_set_fingerprint,
            self.manifest.target_set_fingerprint,
        )
        if len(set(shared)) != 1:
            raise ValueError("制片修正 generation 的评审目标集指纹不一致。")
        sources = {
            correction.source_fingerprint,
            receipt.source_fingerprint,
            self.official_plan.source_fingerprint,
            self.manifest.source_fingerprint,
        }
        if len(sources) != 1:
            raise ValueError("制片修正 generation 的来源指纹不一致。")
        if correction.base_plan_fingerprint != answers.plan_fingerprint:
            raise ValueError("制片修正与专家答案绑定的基础规划不一致。")
        if correction.base_plan_fingerprint != receipt.base_plan_fingerprint:
            raise ValueError("制片修正回执绑定的基础规划不一致。")
        if correction.review_answers_fingerprint != fingerprints["review_answers_fingerprint"]:
            raise ValueError("制片修正集合绑定的专家答案指纹不一致。")
        if receipt.correction_set_fingerprint != fingerprints["correction_set_fingerprint"]:
            raise ValueError("制片修正回执绑定的修正集合指纹不一致。")
        if receipt.review_answers_fingerprint != fingerprints["review_answers_fingerprint"]:
            raise ValueError("制片修正回执绑定的专家答案指纹不一致。")
        if receipt.corrected_plan_fingerprint != plan_fingerprint:
            raise ValueError("制片修正回执绑定的正式规划指纹不一致。")
        if self.validation.plan_fingerprint != plan_fingerprint:
            raise ValueError("制片规划校验报告已经过期。")
        if self.manifest.base_plan_fingerprint != correction.base_plan_fingerprint:
            raise ValueError("制片修正清单绑定的基础规划指纹不一致。")
        expected_ids = [item.id for item in correction.corrections]
        if receipt.applied_correction_ids != expected_ids:
            raise ValueError("制片修正回执没有按集合顺序覆盖全部修正。")
        _validate_review_identity(correction, answers, receipt)


class ProductionReleaseManifest(StrictModel):
    """记录一个不可变制片发布归档的内容指纹。"""

    schema_version: str = "1.0"
    release_id: str = Field(pattern=_FINGERPRINT_PATTERN)
    profile: ProductionReleaseProfile
    report_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    official_plan_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    validation_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    review_report_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    correction_receipt_fingerprint: str | None = Field(
        default=None,
        pattern=_FINGERPRINT_PATTERN,
    )

    @model_validator(mode="after")
    def _validate_release_id(self) -> Self:
        """拒绝与清单其余字段不一致的 release ID。"""
        payload = self.model_dump(mode="json", exclude={"release_id"})
        if self.release_id != content_fingerprint(payload):
            raise ValueError("制片发布 ID 与内容指纹不一致。")
        return self


class ProductionImmutableRelease(StrictModel):
    """聚合并交叉校验一个可发布的不可变制片快照。"""

    manifest: ProductionReleaseManifest
    report: ProductionReleaseReport
    official_plan: ProductionPlan
    validation: ProductionPlanningValidationReport
    review_report: ProductionReviewReport
    correction_receipt: ProductionCorrectionReceipt | None = None

    @model_validator(mode="after")
    def _validate_bindings(self) -> Self:
        """校验发布决定、正式规划、评审和可选回执的完整绑定。"""
        plan_fingerprint = content_fingerprint(self.official_plan)
        component_fingerprints = {
            "report_fingerprint": content_fingerprint(self.report),
            "official_plan_fingerprint": plan_fingerprint,
            "validation_fingerprint": content_fingerprint(self.validation),
            "review_report_fingerprint": content_fingerprint(self.review_report),
            "correction_receipt_fingerprint": (
                content_fingerprint(self.correction_receipt) if self.correction_receipt else None
            ),
        }
        for name, value in component_fingerprints.items():
            if getattr(self.manifest, name) != value:
                raise ValueError(f"不可变制片发布的 {name} 不匹配。")
        if not self.report.releasable:
            raise ValueError("被门禁阻断的制片报告不能建立不可变发布。")
        if self.report.profile != self.manifest.profile:
            raise ValueError("制片发布策略与归档清单不一致。")
        if self.report.plan_fingerprint != plan_fingerprint:
            raise ValueError("制片发布报告绑定的正式规划指纹不一致。")
        if self.validation.plan_fingerprint != plan_fingerprint:
            raise ValueError("制片发布使用的规划校验报告已经过期。")
        if self.review_report.plan_fingerprint != plan_fingerprint:
            raise ValueError("制片发布使用的专家评审报告已经过期。")
        if self.report.review_target_set_fingerprint != self.review_report.target_set_fingerprint:
            raise ValueError("制片发布报告绑定的评审目标集不一致。")
        if (
            self.correction_receipt is not None
            and self.correction_receipt.corrected_plan_fingerprint != plan_fingerprint
        ):
            raise ValueError("制片发布使用的修正回执已经过期。")
        return self


def _validate_review_identity(
    correction: ProductionCorrectionSet,
    answers: ProductionReviewAnswers,
    receipt: ProductionCorrectionReceipt,
) -> None:
    """确保修正集合、专家答案和回执来自同一次评审。"""
    values = {
        (
            item.rubric_version,
            item.safety_policy_version,
            item.reviewer,
            item.reviewer_kind,
        )
        for item in (correction, answers, receipt)
    }
    if len(values) != 1:
        raise ValueError("制片修正 generation 的评审身份或标准版本不一致。")
