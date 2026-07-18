from pathlib import Path

import pytest

from movie_breakdown.application.correction_workflow import (
    CorrectionWorkflowError,
    ManualCorrectionWorkflow,
)
from movie_breakdown.domain.manual_correction import (
    CorrectionField,
    CorrectionReceipt,
    CorrectionSet,
    NarrativeCorrection,
)
from movie_breakdown.domain.quality import HumanReviewAnswers, ReviewResponse, ReviewVerdict
from movie_breakdown.domain.scene_analysis import Evidence
from movie_breakdown.domain.source import SourceSpan
from movie_breakdown.infrastructure.fingerprint import content_fingerprint
from movie_breakdown.infrastructure.storage import ProjectStore
from tests.factories import make_breakdown


def _answers(breakdown, **updates) -> HumanReviewAnswers:
    values = {
        "analysis_fingerprint": content_fingerprint(breakdown),
        "rubric_version": "1.1",
        "reviewer": "叙事顾问",
        "responses": [
            ReviewResponse(
                target_id="scene-summary:scene-0001",
                verdict=ReviewVerdict.PARTIALLY_SUPPORTED,
                ratings=[],
                notes="原摘要遗漏人物即将离乡的行动目的。",
                proposed_correction="小王抵达车站，准备离乡。",
            )
        ],
    }
    values.update(updates)
    return HumanReviewAnswers(**values)


def _correction_set(breakdown, answers: HumanReviewAnswers, **updates) -> CorrectionSet:
    correction = NarrativeCorrection(
        id="correction-scene-0001",
        review_target_id="scene-summary:scene-0001",
        field=CorrectionField.SCENE_SUMMARY,
        object_id="scene-0001",
        expected_value_fingerprint=content_fingerprint("小王进站。"),
        replacement="小王抵达车站，准备离乡。",
        rationale="专家结合后续启程动作补足场景目的。",
        evidence=[
            Evidence(
                scene_id="scene-0001",
                source_span=SourceSpan(line_start=2, line_end=2),
                excerpt="小王进站。",
                confidence="high",
            )
        ],
    )
    values = {
        "source_fingerprint": breakdown.screenplay.source_fingerprint,
        "base_analysis_fingerprint": content_fingerprint(breakdown),
        "rubric_version": answers.rubric_version,
        "review_answers_fingerprint": content_fingerprint(answers),
        "reviewer": answers.reviewer,
        "corrections": [correction],
    }
    values.update(updates)
    return CorrectionSet(**values)


def _workflow(tmp_path: Path) -> ManualCorrectionWorkflow:
    return ManualCorrectionWorkflow(ProjectStore(tmp_path / "project"))


def test_preview_validates_and_applies_without_writing_files(tmp_path: Path) -> None:
    breakdown = make_breakdown()
    answers = _answers(breakdown)
    workflow = _workflow(tmp_path)

    corrected, receipt = workflow.preview(
        breakdown,
        _correction_set(breakdown, answers),
        answers,
    )

    assert corrected.scene_analyses[0].summary == "小王抵达车站，准备离乡。"
    assert breakdown.scene_analyses[0].summary == "小王进站。"
    assert receipt.applied_correction_ids == ["correction-scene-0001"]
    assert not (tmp_path / "project").exists()


def test_activate_atomically_writes_all_workflow_artifacts(tmp_path: Path) -> None:
    breakdown = make_breakdown()
    answers = _answers(breakdown)
    correction_set = _correction_set(breakdown, answers)
    store = ProjectStore(tmp_path / "project")
    workflow = ManualCorrectionWorkflow(store)

    corrected, receipt = workflow.activate(breakdown, correction_set, answers)

    assert store.read_model(store.root / "corrections/active.json", CorrectionSet) == correction_set
    assert (
        store.read_model(store.root / "corrections/review_answers.json", HumanReviewAnswers)
        == answers
    )
    assert store.read_model(store.artifact_path("correction_receipt"), CorrectionReceipt) == receipt
    persisted = store.read_model(store.artifact_path("corrected_breakdown"), type(corrected))
    assert content_fingerprint(persisted) == content_fingerprint(corrected)


def test_apply_active_returns_original_or_strictly_replays(tmp_path: Path) -> None:
    breakdown = make_breakdown()
    workflow = _workflow(tmp_path)

    untouched, absent_receipt = workflow.apply_active(breakdown)

    assert untouched is breakdown
    assert absent_receipt is None

    answers = _answers(breakdown)
    workflow.activate(breakdown, _correction_set(breakdown, answers), answers)
    replayed, receipt = workflow.apply_active(breakdown)

    assert replayed.scene_analyses[0].summary == "小王抵达车站，准备离乡。"
    assert receipt is not None
    assert receipt.corrected_analysis_fingerprint == content_fingerprint(replayed)


@pytest.mark.parametrize(
    ("answers_update", "set_update", "message"),
    [
        ({"analysis_fingerprint": "stale"}, {}, "基础分析指纹"),
        ({"rubric_version": "2.0"}, {"rubric_version": "1.1"}, "评分标准版本"),
        ({"reviewer": "另一位顾问"}, {"reviewer": "叙事顾问"}, "评审者"),
    ],
)
def test_preview_rejects_mismatched_review_bindings(
    tmp_path: Path,
    answers_update: dict,
    set_update: dict,
    message: str,
) -> None:
    breakdown = make_breakdown()
    answers = _answers(breakdown, **answers_update)
    correction_set = _correction_set(breakdown, answers, **set_update)

    with pytest.raises(CorrectionWorkflowError, match=message):
        _workflow(tmp_path).preview(breakdown, correction_set, answers)


def test_preview_rejects_unbound_answers_fingerprint(tmp_path: Path) -> None:
    breakdown = make_breakdown()
    answers = _answers(breakdown)
    correction_set = _correction_set(
        breakdown,
        answers,
        review_answers_fingerprint="other-answers",
    )

    with pytest.raises(CorrectionWorkflowError, match="评审答案指纹"):
        _workflow(tmp_path).preview(breakdown, correction_set, answers)


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (None, "评审目标不存在"),
        (
            ReviewResponse(
                target_id="scene-summary:scene-0001",
                verdict=ReviewVerdict.SUPPORTED,
                ratings=[],
                proposed_correction="不应采用",
            ),
            "不允许产生修正",
        ),
        (
            ReviewResponse(
                target_id="scene-summary:scene-0001",
                verdict=ReviewVerdict.UNCERTAIN,
                ratings=[],
                proposed_correction="   ",
            ),
            "缺少 proposed_correction",
        ),
    ],
)
def test_preview_requires_actionable_review_response(
    tmp_path: Path,
    response: ReviewResponse | None,
    message: str,
) -> None:
    breakdown = make_breakdown()
    answers = _answers(breakdown, responses=[] if response is None else [response])
    correction_set = _correction_set(breakdown, answers)

    with pytest.raises(CorrectionWorkflowError, match=message):
        _workflow(tmp_path).preview(breakdown, correction_set, answers)


def test_apply_active_reports_missing_or_invalid_review_answers(tmp_path: Path) -> None:
    breakdown = make_breakdown()
    answers = _answers(breakdown)
    correction_set = _correction_set(breakdown, answers)
    store = ProjectStore(tmp_path / "project")
    workflow = ManualCorrectionWorkflow(store)
    store.write_model(store.root / "corrections/active.json", correction_set)

    with pytest.raises(CorrectionWorkflowError, match=r"评审答案.*不存在"):
        workflow.apply_active(breakdown)

    invalid_path = store.root / "corrections/review_answers.json"
    invalid_path.write_text('{"reviewer": 42}', encoding="utf-8")
    with pytest.raises(CorrectionWorkflowError, match=r"评审答案.*无效"):
        workflow.apply_active(breakdown)
