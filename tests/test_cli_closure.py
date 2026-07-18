import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from movie_breakdown import cli, cli_closure
from movie_breakdown.application.closure import CorrectionRunResult, ReleaseRunResult
from movie_breakdown.cli_types import ExitCode
from movie_breakdown.domain.manual_correction import CorrectionReceipt
from movie_breakdown.domain.release import (
    ReleaseGateCheck,
    ReleaseGateCheckCode,
    ReleaseGateReport,
)

runner = CliRunner()


def _receipt() -> CorrectionReceipt:
    return CorrectionReceipt(
        source_fingerprint="source",
        base_analysis_fingerprint="base",
        corrected_analysis_fingerprint="corrected",
        correction_set_fingerprint="correction-set",
        rubric_version="1.1",
        review_answers_fingerprint="answers",
        reviewer="叙事顾问",
        applied_correction_ids=["correction-001"],
        applied_count=1,
    )


def _report(stable: bool) -> ReleaseGateReport:
    checks = [
        ReleaseGateCheck(
            code=code,
            name=f"检查 {index}",
            passed=stable or index > 0,
            message="已通过。" if stable or index > 0 else "需要修正。",
        )
        for index, code in enumerate(ReleaseGateCheckCode)
    ]
    return ReleaseGateReport(
        analysis_fingerprint="official-analysis",
        stable=stable,
        checks=checks,
    )


def test_correct_json_reports_success_and_zero_exit_code(
    tmp_path: Path,
    monkeypatch,
) -> None:
    expected = CorrectionRunResult(
        dry_run=False,
        analysis_fingerprint="official-analysis",
        receipt=_receipt(),
        exports={"json": "breakdown.json", "markdown": "report.md"},
    )

    class Service:
        def __init__(self, store) -> None:
            self.store = store

        def apply_corrections(self, correction_path, answers_path, *, dry_run=False):
            assert correction_path == tmp_path / "corrections.json"
            assert answers_path == tmp_path / "answers.json"
            assert dry_run is False
            return expected

    monkeypatch.setattr(cli_closure, "NarrativeClosureService", Service)

    result = runner.invoke(
        cli.app,
        [
            "correct",
            str(tmp_path / "project"),
            str(tmp_path / "corrections.json"),
            "--answers",
            str(tmp_path / "answers.json"),
            "--json",
        ],
    )

    assert result.exit_code == ExitCode.SUCCESS, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["dry_run"] is False
    assert payload["receipt"]["applied_count"] == 1
    assert payload["exports"] == expected.exports


@pytest.mark.parametrize(
    ("stable", "exit_code"),
    [(True, ExitCode.SUCCESS), (False, ExitCode.VALIDATION_FAILED)],
)
def test_finalize_json_uses_stable_exit_codes(
    tmp_path: Path,
    monkeypatch,
    stable: bool,
    exit_code: ExitCode,
) -> None:
    report = _report(stable)

    class Service:
        def __init__(self, store) -> None:
            self.store = store

        def finalize(self) -> ReleaseRunResult:
            return ReleaseRunResult(
                report=report,
                exports={"artifact": "release_gate.json", "markdown": "release-gate.md"},
            )

    monkeypatch.setattr(cli_closure, "NarrativeClosureService", Service)

    result = runner.invoke(cli.app, ["finalize", str(tmp_path / "project"), "--json"])

    assert result.exit_code == exit_code, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is stable
    assert payload["report"]["stable"] is stable
    assert set(payload["exports"]) == {"artifact", "markdown"}


def test_correct_json_reports_input_error_with_error_exit_code(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class Service:
        def __init__(self, store) -> None:
            self.store = store

        def apply_corrections(self, *args, **kwargs):
            raise ValueError("专家评审答案对应的基础分析指纹已经过期。")

    monkeypatch.setattr(cli_closure, "NarrativeClosureService", Service)

    result = runner.invoke(
        cli.app,
        [
            "correct",
            str(tmp_path / "project"),
            str(tmp_path / "corrections.json"),
            "--answers",
            str(tmp_path / "answers.json"),
            "--json",
        ],
    )

    assert result.exit_code == ExitCode.ERROR
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "基础分析指纹已经过期" in payload["error"]
