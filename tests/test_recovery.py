import pytest
from pydantic import ValidationError

from movie_breakdown.domain.base import Confidence
from movie_breakdown.domain.recovery import GlobalEvidenceRecoveryReport
from movie_breakdown.domain.scene_analysis import Evidence
from movie_breakdown.domain.source import SourceSpan


def _dropped_evidence() -> Evidence:
    return Evidence(
        scene_id="scene-0058",
        source_span=SourceSpan(line_start=900, line_end=901),
        excerpt="模型生成但无法在场景中定位的文字。",
        confidence=Confidence.MEDIUM,
    )


def test_recovery_report_records_strict_failure_and_dropped_evidence() -> None:
    report = GlobalEvidenceRecoveryReport(
        source_fingerprint="source-fingerprint",
        cache_key="global-cache-key",
        recovered=True,
        initial_error="证据无法在场景 scene-0058 中定位。",
        dropped_evidence=[_dropped_evidence()],
        result_fingerprint="result-fingerprint",
    )

    restored = GlobalEvidenceRecoveryReport.model_validate_json(report.model_dump_json())

    assert restored == report
    assert restored.dropped_evidence[0].scene_id == "scene-0058"


def test_recovery_report_allows_clean_strict_result() -> None:
    report = GlobalEvidenceRecoveryReport(
        source_fingerprint="source-fingerprint",
        cache_key="global-cache-key",
        recovered=False,
        result_fingerprint="result-fingerprint",
    )

    assert report.initial_error is None
    assert report.dropped_evidence == []


@pytest.mark.parametrize(
    ("recovered", "initial_error", "dropped_evidence"),
    [
        (True, None, [_dropped_evidence()]),
        (True, "证据无法定位。", []),
        (False, "证据无法定位。", []),
        (False, None, [_dropped_evidence()]),
    ],
)
def test_recovery_report_rejects_inconsistent_state(
    recovered: bool,
    initial_error: str | None,
    dropped_evidence: list[Evidence],
) -> None:
    with pytest.raises(ValidationError, match="证据恢复"):
        GlobalEvidenceRecoveryReport(
            source_fingerprint="source-fingerprint",
            cache_key="global-cache-key",
            recovered=recovered,
            initial_error=initial_error,
            dropped_evidence=dropped_evidence,
            result_fingerprint="result-fingerprint",
        )
