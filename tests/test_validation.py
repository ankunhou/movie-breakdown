from movie_breakdown.application.validation import ValidationService
from movie_breakdown.domain.base import Confidence, StageStatus
from movie_breakdown.domain.scene_analysis import Evidence
from movie_breakdown.domain.source import SourceSpan
from tests.factories import (
    make_biographies,
    make_dossiers,
    make_global_result,
    make_records,
    make_screenplay,
)


def test_valid_complete_artifacts_pass() -> None:
    screenplay = make_screenplay()
    global_result = make_global_result()
    report = ValidationService().validate(
        screenplay,
        make_records(screenplay),
        global_result,
        make_biographies(),
        make_dossiers(screenplay, global_result),
    )

    assert report.valid
    assert report.coverage == 1
    assert report.issues == []


def test_failed_scene_reduces_coverage() -> None:
    screenplay = make_screenplay()
    records = make_records(screenplay)
    records[1].status = StageStatus.FAILED
    records[1].analysis = None
    records[1].error = "模型输出无效"
    global_result = make_global_result()

    report = ValidationService().validate(
        screenplay,
        records,
        global_result,
        make_biographies(),
        make_dossiers(screenplay, global_result),
    )

    assert not report.valid
    assert report.coverage == 2 / 3
    assert {issue.code for issue in report.issues} >= {"analysis.failed", "scene.coverage"}


def test_unknown_global_reference_is_error() -> None:
    screenplay = make_screenplay()
    global_result = make_global_result()
    global_result.events.events[0].participant_ids = ["char-missing"]

    report = ValidationService().validate(
        screenplay,
        make_records(screenplay),
        global_result,
        make_biographies(),
        make_dossiers(screenplay, global_result),
    )

    assert not report.valid
    assert "event.character_ref" in {issue.code for issue in report.issues}


def test_out_of_scene_evidence_is_error() -> None:
    screenplay = make_screenplay()
    records = make_records(screenplay)
    records[0].analysis.evidence = [
        Evidence(
            scene_id="scene-0001",
            source_span=SourceSpan(line_start=99, line_end=99),
            excerpt="不存在",
            confidence=Confidence.LOW,
        )
    ]
    global_result = make_global_result()

    report = ValidationService().validate(
        screenplay,
        records,
        global_result,
        make_biographies(),
        make_dossiers(screenplay, global_result),
    )

    assert not report.valid
    assert "evidence.span" in {issue.code for issue in report.issues}


def test_global_only_validation_does_not_require_biographies() -> None:
    screenplay = make_screenplay()

    report = ValidationService().validate(
        screenplay,
        make_records(screenplay),
        make_global_result(),
        require_biographies=False,
        require_dossiers=False,
    )

    assert report.valid


def test_duplicate_act_assignment_is_error() -> None:
    screenplay = make_screenplay()
    global_result = make_global_result()
    global_result.structure.acts[1].scene_ids.append("scene-0001")

    report = ValidationService().validate(
        screenplay,
        make_records(screenplay),
        global_result,
        make_biographies(),
        make_dossiers(screenplay, global_result),
    )

    assert not report.valid
    assert "structure.duplicate_assignment" in {issue.code for issue in report.issues}


def test_non_monotonic_act_assignment_is_error() -> None:
    screenplay = make_screenplay()
    global_result = make_global_result()
    global_result.structure.acts[0].scene_ids = ["scene-0002"]
    global_result.structure.acts[1].scene_ids = ["scene-0001"]

    report = ValidationService().validate(
        screenplay,
        make_records(screenplay),
        global_result,
        make_biographies(),
        make_dossiers(screenplay, global_result),
    )

    assert not report.valid
    assert "structure.act_order" in {issue.code for issue in report.issues}
