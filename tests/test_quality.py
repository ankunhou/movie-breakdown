from pathlib import Path

import pytest
from pydantic import ValidationError

from movie_breakdown.application.quality import (
    RUBRIC_VERSION,
    NarrativeQualityService,
    StaleReviewAnswersError,
)
from movie_breakdown.application.quality_exporting import SemanticQualityExporter
from movie_breakdown.application.quality_signals import collect_automatic_signals
from movie_breakdown.application.quality_targets import collect_review_candidates
from movie_breakdown.domain.global_analysis import StoryEvent
from movie_breakdown.domain.quality import (
    DimensionRating,
    HumanReviewAnswers,
    QualityDimension,
    ReviewResponse,
    ReviewVerdict,
)
from movie_breakdown.infrastructure.storage import ProjectStore
from tests.factories import make_breakdown


def _signal(report, code):
    return next(item for item in report.automatic_signals if item.code == code)


def test_dimension_rating_rejects_score_outside_five_point_scale() -> None:
    with pytest.raises(ValidationError):
        DimensionRating(dimension=QualityDimension.SOURCE_FIDELITY, score=6)


def test_review_is_stable_and_separates_proxy_from_human_judgment() -> None:
    breakdown = make_breakdown()
    service = NarrativeQualityService()

    first = service.review(breakdown, sample_size=6)
    second = service.review(breakdown, sample_size=6)

    assert [item.id for item in first.human_review.targets] == [
        item.id for item in second.human_review.targets
    ]
    assert len(first.human_review.targets) == 6
    assert len({item.id for item in first.human_review.targets}) == 6
    assert first.human_summary.reviewed_count == 0
    assert first.human_summary.coverage == 0
    assert _signal(first, "evidence_presence_rate").value == pytest.approx(2 / 16)
    assert _signal(first, "act_exclusive_assignment_rate").value == 1
    assert any("不是叙事判断正确率" in item for item in first.limitations)


def test_partial_human_answers_are_merged_and_summarized() -> None:
    breakdown = make_breakdown()
    service = NarrativeQualityService()
    pending = service.review(breakdown, sample_size=6)
    target = pending.human_review.targets[0]
    answer = ReviewResponse(
        target_id=target.id,
        verdict=ReviewVerdict.SUPPORTED,
        ratings=[DimensionRating(dimension=target.dimensions[0], score=4)],
        notes="与原文一致。",
    )
    answers = HumanReviewAnswers(
        analysis_fingerprint=pending.analysis_fingerprint,
        rubric_version=RUBRIC_VERSION,
        reviewer="编剧顾问",
        responses=[answer],
    )

    reviewed = service.review(breakdown, sample_size=6, answers=answers)

    assert reviewed.human_summary.reviewed_count == 1
    assert reviewed.human_summary.coverage == pytest.approx(1 / 6)
    assert reviewed.human_summary.dimension_averages[target.dimensions[0]] == 4
    assert reviewed.human_review.reviewer == "编剧顾问"


def test_stale_answers_are_rejected() -> None:
    answers = HumanReviewAnswers(
        analysis_fingerprint="old",
        rubric_version=RUBRIC_VERSION,
        responses=[],
    )

    with pytest.raises(StaleReviewAnswersError, match="分析指纹已经过期"):
        NarrativeQualityService().review(make_breakdown(), sample_size=6, answers=answers)


def test_structural_overlap_and_reverse_cause_are_flagged() -> None:
    breakdown = make_breakdown()
    breakdown.structure.acts[1].scene_ids.append("scene-0001")
    cause = StoryEvent(
        id="event-late-cause",
        summary="较晚发生的原因。",
        scene_id="scene-0003",
        participant_ids=["char-xiaowang"],
        cause_event_ids=[],
        consequences=[],
        evidence=[],
    )
    effect = breakdown.events.events[0].model_copy(
        update={"scene_id": "scene-0001", "cause_event_ids": [cause.id]}
    )
    breakdown.events.events = [cause, effect]

    signals = {item.code: item for item in collect_automatic_signals(breakdown)}

    assert signals["act_exclusive_assignment_rate"].status.value == "attention"
    assert "scene-0001" in signals["act_exclusive_assignment_rate"].references
    assert signals["causal_chronology_rate"].value == 0
    assert signals["causal_chronology_rate"].references == [
        "event:event-late-cause->event-departure"
    ]


def test_biography_signals_do_not_treat_inference_as_fact() -> None:
    report = NarrativeQualityService().review(make_breakdown(), sample_size=6)

    assert _signal(report, "biography_claim_evidence_rate").value == 1
    assert _signal(report, "biography_report_attribution_rate").value is None
    assert _signal(report, "biography_inference_rationale_rate").value == 1
    multiscene = _signal(report, "biography_persistent_inference_multiscene_rate")
    assert multiscene.value == 0
    assert multiscene.status.value == "attention"
    inference_share = _signal(report, "biography_inference_share")
    assert inference_share.value == 1
    assert inference_share.status.value == "info"


def test_dossier_signals_do_not_change_biography_claim_denominators() -> None:
    breakdown = make_breakdown()
    report = NarrativeQualityService().review(breakdown, sample_size=6)

    coverage = _signal(report, "character_dossier_coverage_rate")
    distribution = _signal(report, "character_dossier_tier_distribution")

    assert coverage.value == 1
    assert coverage.numerator == len(breakdown.entities.characters)
    assert "核心 1" in distribution.message
    assert _signal(report, "biography_claim_evidence_rate").denominator == 2


def test_biography_targets_expose_basis_rationale_and_human_dimension() -> None:
    breakdown = make_breakdown()
    signals = collect_automatic_signals(breakdown)

    targets = {item.id: item for item in collect_review_candidates(breakdown, signals)}
    overall = targets["biography:char-xiaowang"]
    claim = targets["biography-claim:char-xiaowang:bio-xiaowang-goal"]

    assert overall.kind.value == "character_biography"
    assert QualityDimension.CHARACTER_PORTRAIT_COHERENCE in overall.dimensions
    assert "分析推断" in claim.claim
    assert "推断依据" in claim.claim
    assert len(claim.contexts) <= 6


def test_default_review_sampling_anchors_main_character_biography() -> None:
    breakdown = make_breakdown()
    breakdown.structure.themes = [f"主题{index}" for index in range(10)]
    breakdown.structure.motifs = [f"母题{index}" for index in range(10)]

    report = NarrativeQualityService().review(breakdown, sample_size=16)
    selected = {item.id: item for item in report.human_review.targets}

    assert selected["biography:char-xiaowang"].selection_reason == "anchor"


def test_exporter_preserves_existing_human_template(tmp_path: Path) -> None:
    report = NarrativeQualityService().review(make_breakdown(), sample_size=6)
    store = ProjectStore(tmp_path / "project")
    exporter = SemanticQualityExporter()

    paths = exporter.export(store, report)
    template = Path(paths["answers_template"])
    template.write_text("{}", encoding="utf-8")
    exporter.export(store, report)

    assert Path(paths["artifact"]).is_file()
    assert "自动信号只是风险代理" in Path(paths["markdown"]).read_text("utf-8")
    assert template.read_text("utf-8") == "{}"


def test_markdown_renders_only_filled_human_review_details() -> None:
    breakdown = make_breakdown()
    service = NarrativeQualityService()
    pending = service.review(breakdown, sample_size=6)
    target = pending.human_review.targets[0]
    dimension = target.dimensions[0]
    answer = ReviewResponse(
        target_id=target.id,
        verdict=ReviewVerdict.PARTIALLY_SUPPORTED,
        ratings=[
            DimensionRating(
                dimension=dimension,
                score=3,
                comment="引用准确，但因果推断过强。",
            )
        ],
        notes="需要回看相邻场景。",
        proposed_correction="改为角色可能因此离开。",
    )
    answers = HumanReviewAnswers(
        analysis_fingerprint=pending.analysis_fingerprint,
        rubric_version=RUBRIC_VERSION,
        reviewer="编剧顾问",
        responses=[answer],
    )
    report = service.review(breakdown, sample_size=6, answers=answers)

    markdown = SemanticQualityExporter().render_markdown(report)

    assert "- 评审人：编剧顾问" in markdown
    assert f"- `{dimension.value}`：3/5" in markdown
    assert "  - 评论：引用准确，但因果推断过强。" in markdown
    assert "备注：需要回看相邻场景。" in markdown
    assert "建议修正：改为角色可能因此离开。" in markdown


def test_markdown_does_not_invent_unfilled_review_details() -> None:
    report = NarrativeQualityService().review(make_breakdown(), sample_size=6)

    markdown = SemanticQualityExporter().render_markdown(report)

    assert "- 评审人：" not in markdown
    assert "人工评分：" not in markdown
    assert "评论：" not in markdown
    assert "备注：" not in markdown
    assert "建议修正：" not in markdown
