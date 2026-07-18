import pytest
from pydantic import ValidationError

from movie_breakdown.application.quality import RUBRIC_VERSION, NarrativeQualityService
from movie_breakdown.application.release_gate import ReleaseGateService
from movie_breakdown.domain.quality import (
    DimensionRating,
    HumanReviewAnswers,
    ReviewResponse,
    ReviewVerdict,
)
from movie_breakdown.domain.release import ReleaseGateCheck, ReleaseGateCheckCode, ReleaseGateReport
from tests.factories import make_breakdown


def _complete_quality(verdict=ReviewVerdict.SUPPORTED, notes=""):
    breakdown = make_breakdown()
    breakdown.structure.themes.append("离别")
    quality_service = NarrativeQualityService()
    pending = quality_service.review(breakdown, sample_size=16)
    responses = [
        ReviewResponse(
            target_id=target.id,
            verdict=verdict if index == 0 else ReviewVerdict.SUPPORTED,
            ratings=[DimensionRating(dimension=item, score=4) for item in target.dimensions],
            notes=notes if index == 0 else "经核对可放行。",
        )
        for index, target in enumerate(pending.human_review.targets)
    ]
    answers = HumanReviewAnswers(
        analysis_fingerprint=pending.analysis_fingerprint,
        rubric_version=RUBRIC_VERSION,
        reviewer="叙事顾问模拟评审",
        responses=responses,
    )
    return breakdown, quality_service.review(breakdown, sample_size=16, answers=answers)


def _check(report, code):
    return next(item for item in report.checks if item.code == code)


def test_complete_expert_review_passes_release_gate() -> None:
    breakdown, quality = _complete_quality()

    report = ReleaseGateService().evaluate(breakdown, quality)

    assert report.stable is True
    assert len(report.checks) == len(ReleaseGateCheckCode)
    assert all(check.passed for check in report.checks)


def test_gate_rejects_invalid_structure_and_stale_quality() -> None:
    breakdown, quality = _complete_quality()
    breakdown.validation.valid = False
    quality.analysis_fingerprint = "old"

    report = ReleaseGateService().evaluate(breakdown, quality)

    assert report.stable is False
    assert not _check(report, ReleaseGateCheckCode.STRUCTURAL_VALIDATION).passed
    assert not _check(report, ReleaseGateCheckCode.ANALYSIS_FINGERPRINT).passed


def test_gate_requires_named_reviewer_and_sixteen_targets() -> None:
    breakdown = make_breakdown()
    quality_service = NarrativeQualityService()
    pending = quality_service.review(breakdown, sample_size=6)
    responses = [
        ReviewResponse(
            target_id=target.id,
            verdict=ReviewVerdict.SUPPORTED,
            ratings=[DimensionRating(dimension=item, score=4) for item in target.dimensions],
        )
        for target in pending.human_review.targets
    ]
    answers = HumanReviewAnswers(
        analysis_fingerprint=pending.analysis_fingerprint,
        rubric_version=RUBRIC_VERSION,
        reviewer="   ",
        responses=responses,
    )
    quality = quality_service.review(breakdown, sample_size=6, answers=answers)

    report = ReleaseGateService().evaluate(breakdown, quality)

    assert not _check(report, ReleaseGateCheckCode.REVIEWER_IDENTITY).passed
    assert not _check(report, ReleaseGateCheckCode.TARGET_COUNT).passed


def test_gate_cross_checks_completion_summary_with_actual_responses() -> None:
    breakdown, quality = _complete_quality()
    quality.human_summary.reviewed_count = 15
    quality.human_summary.coverage = 15 / 16

    report = ReleaseGateService().evaluate(breakdown, quality)

    assert not _check(report, ReleaseGateCheckCode.REVIEW_COMPLETION).passed


def test_gate_rejects_unsupported_verdict() -> None:
    breakdown, quality = _complete_quality(ReviewVerdict.UNSUPPORTED)

    report = ReleaseGateService().evaluate(breakdown, quality)

    check = _check(report, ReleaseGateCheckCode.REVIEW_VERDICTS)
    assert check.passed is False
    assert quality.human_review.responses[0].target_id in check.references


def test_gate_requires_every_applicable_dimension_to_be_scored() -> None:
    breakdown, quality = _complete_quality()
    target = quality.human_review.targets[0]
    quality.human_review.responses[0].ratings[0].score = None

    report = ReleaseGateService().evaluate(breakdown, quality)

    check = _check(report, ReleaseGateCheckCode.DIMENSION_RATINGS)
    assert check.passed is False
    assert target.id in check.references


@pytest.mark.parametrize("verdict", [ReviewVerdict.PARTIALLY_SUPPORTED, ReviewVerdict.UNCERTAIN])
def test_conditional_verdict_requires_explicit_risk_acceptance(verdict) -> None:
    breakdown, quality = _complete_quality(verdict, "需要保留判断。")

    report = ReleaseGateService().evaluate(breakdown, quality)

    assert not _check(report, ReleaseGateCheckCode.ACCEPTED_RISKS).passed


@pytest.mark.parametrize("notes", ["接受风险：", "接受风险：   "])
def test_risk_acceptance_requires_nonempty_explanation(notes) -> None:
    breakdown, quality = _complete_quality(ReviewVerdict.PARTIALLY_SUPPORTED, notes)

    report = ReleaseGateService().evaluate(breakdown, quality)

    assert not _check(report, ReleaseGateCheckCode.ACCEPTED_RISKS).passed


@pytest.mark.parametrize("verdict", [ReviewVerdict.PARTIALLY_SUPPORTED, ReviewVerdict.UNCERTAIN])
def test_documented_conditional_verdict_can_pass(verdict) -> None:
    breakdown, quality = _complete_quality(
        verdict,
        "接受风险：该判断有多种解读，已在稳定版限制中披露。",
    )

    report = ReleaseGateService().evaluate(breakdown, quality)

    assert report.stable is True


def test_release_report_rejects_decision_inconsistent_with_checks() -> None:
    with pytest.raises(ValidationError, match="发布决策"):
        ReleaseGateReport(
            analysis_fingerprint="fingerprint",
            stable=True,
            checks=[
                ReleaseGateCheck(
                    code=ReleaseGateCheckCode.STRUCTURAL_VALIDATION,
                    name="结构校验",
                    passed=False,
                    message="未通过。",
                )
            ],
        )


def test_release_report_requires_complete_checklist() -> None:
    with pytest.raises(ValidationError, match="完整覆盖"):
        ReleaseGateReport(
            analysis_fingerprint="fingerprint",
            stable=True,
            checks=[
                ReleaseGateCheck(
                    code=ReleaseGateCheckCode.STRUCTURAL_VALIDATION,
                    name="结构校验",
                    passed=True,
                    message="已通过。",
                )
            ],
        )
