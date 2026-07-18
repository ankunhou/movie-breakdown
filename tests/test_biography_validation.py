from movie_breakdown.application.validation import ValidationService
from movie_breakdown.domain.base import Confidence
from movie_breakdown.domain.scene_analysis import Evidence
from movie_breakdown.domain.source import SourceSpan
from tests.factories import (
    make_biographies,
    make_dossiers,
    make_global_result,
    make_records,
    make_screenplay,
)


def test_validation_rejects_missing_biography_artifact() -> None:
    screenplay = make_screenplay()
    global_result = make_global_result()

    report = ValidationService().validate(
        screenplay,
        make_records(screenplay),
        global_result,
        dossiers=make_dossiers(screenplay, global_result),
    )

    assert not report.valid
    assert "biography.missing" in {issue.code for issue in report.issues}


def test_validation_checks_biography_relationship_and_evidence() -> None:
    screenplay = make_screenplay()
    biographies = make_biographies()
    global_result = make_global_result()
    biography = biographies.biographies[0]
    biography.key_relationship_ids = ["relation-missing"]
    biography.summary.evidence[0].source_span = SourceSpan(line_start=99, line_end=99)
    biography.representative_lines = [
        Evidence(
            scene_id="scene-0002",
            source_span=SourceSpan(line_start=3, line_end=4),
            excerpt="小王登上月台。",
            confidence=Confidence.HIGH,
        )
    ]

    report = ValidationService().validate(
        screenplay,
        make_records(screenplay),
        global_result,
        biographies,
        make_dossiers(screenplay, global_result),
    )
    codes = {issue.code for issue in report.issues}

    assert not report.valid
    assert "biography.relationship_ref" in codes
    assert "biography.evidence.span" in codes
    assert "biography.line_context" in codes
