import inspect
import json
from pathlib import Path
from types import SimpleNamespace

from rich.text import Text
from typer.testing import CliRunner

from movie_breakdown import cli, cli_production, cli_production_closure
from movie_breakdown.cli_types import ExitCode
from movie_breakdown.domain.production_correction import (
    ProductionCorrectionReceipt,
    ProductionCorrectionSet,
)
from movie_breakdown.domain.production_planning_validation import (
    ProductionPlanningValidationReport,
)
from movie_breakdown.domain.production_release import (
    ProductionReleaseCheck,
    ProductionReleaseCheckCode,
    ProductionReleaseProfile,
    ProductionReleaseReport,
    ProductionReleaseState,
)
from movie_breakdown.domain.production_review import (
    ProductionReviewAnswers,
    ProductionReviewerKind,
)

runner = CliRunner()


def _validation(draft_valid: bool = True) -> ProductionPlanningValidationReport:
    return ProductionPlanningValidationReport(
        plan_fingerprint="plan",
        draft_valid=draft_valid,
        catalog_ready=False,
        shoot_ready=False,
        scene_count=1,
        shooting_unit_count=1,
        resource_class_count=1,
        entity_count=0,
        unresolved_entity_count=1,
        unknown_unit_count=0,
        hazard_count=1,
        qualified_approval_count=0,
        issues=[],
    )


def _release_report(releasable: bool) -> ProductionReleaseReport:
    checks = [
        ProductionReleaseCheck(
            code=code,
            name=f"检查 {index}",
            passed=releasable or index > 0,
            message="已通过。" if releasable or index > 0 else "未通过。",
        )
        for index, code in enumerate(ProductionReleaseCheckCode)
    ]
    return ProductionReleaseReport(
        profile=ProductionReleaseProfile.EVALUATION,
        plan_fingerprint="plan",
        review_target_set_fingerprint="targets",
        state=(
            ProductionReleaseState.EVALUATION_READY
            if releasable
            else ProductionReleaseState.BLOCKED
        ),
        releasable=releasable,
        checks=checks,
        limitations=[],
    )


def _receipt() -> ProductionCorrectionReceipt:
    return ProductionCorrectionReceipt(
        source_fingerprint="source",
        base_plan_fingerprint="base",
        corrected_plan_fingerprint="corrected",
        target_set_fingerprint="targets",
        correction_set_fingerprint="corrections",
        review_answers_fingerprint="answers",
        rubric_version="1.0",
        safety_policy_version="1.0",
        reviewer="制片专家",
        reviewer_kind=ProductionReviewerKind.HUMAN_EXPERT,
        applied_correction_ids=["correction-001"],
        applied_count=1,
    )


def test_production_closure_commands_are_registered_in_help() -> None:
    result = runner.invoke(cli.app, ["production", "--help"])

    assert result.exit_code == ExitCode.SUCCESS
    for command in ("plan", "review", "correct", "finalize"):
        assert command in result.stdout
    finalize_help = runner.invoke(
        cli.app,
        ["production", "finalize", "--help"],
        env={"FORCE_COLOR": "1"},
    )
    assert finalize_help.exit_code == ExitCode.SUCCESS
    plain_help = Text.from_ansi(finalize_help.stdout).plain
    assert "--profile" in plain_help
    assert "evaluation" in plain_help


def test_plan_blocked_is_rendered_before_exit_code_two(tmp_path: Path, monkeypatch) -> None:
    class Service:
        def __init__(self, store) -> None:
            self.store = store

        def plan(self):
            return SimpleNamespace(validation=_validation(False), exports={"json": "plan.json"})

    monkeypatch.setattr(cli_production_closure, "ProductionClosureService", Service)

    result = runner.invoke(
        cli.app,
        ["production", "plan", str(tmp_path / "project"), "--json"],
    )

    assert result.exit_code == ExitCode.VALIDATION_FAILED
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["validation"]["draft_valid"] is False
    assert payload["exports"] == {"json": "plan.json"}


def test_finalize_blocked_report_is_rendered_before_exit_code_two(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class Service:
        def __init__(self, store) -> None:
            self.store = store

        def finalize(self, profile):
            assert profile == ProductionReleaseProfile.EVALUATION
            return SimpleNamespace(
                report=_release_report(False),
                release_id=None,
                exports={"report": "release-evaluation.json"},
            )

    monkeypatch.setattr(cli_production_closure, "ProductionClosureService", Service)

    result = runner.invoke(
        cli.app,
        [
            "production",
            "finalize",
            str(tmp_path / "project"),
            "--profile",
            "evaluation",
            "--json",
        ],
    )

    assert result.exit_code == ExitCode.VALIDATION_FAILED
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["report"]["state"] == "blocked"
    assert payload["exports"] == {"report": "release-evaluation.json"}


def test_correct_dry_run_strictly_loads_both_models(tmp_path: Path, monkeypatch) -> None:
    corrections_path = tmp_path / "corrections.json"
    answers_path = tmp_path / "answers.json"
    corrections_path.write_text('{"kind":"corrections"}', encoding="utf-8")
    answers_path.write_text('{"kind":"answers"}', encoding="utf-8")
    correction_sentinel = object()
    answers_sentinel = object()
    reads: list[tuple[str, str]] = []

    def load_corrections(cls, raw: str):
        reads.append((cls.__name__, raw))
        return correction_sentinel

    def load_answers(cls, raw: str):
        reads.append((cls.__name__, raw))
        return answers_sentinel

    monkeypatch.setattr(
        ProductionCorrectionSet,
        "model_validate_json",
        classmethod(load_corrections),
    )
    monkeypatch.setattr(
        ProductionReviewAnswers,
        "model_validate_json",
        classmethod(load_answers),
    )

    class Service:
        def __init__(self, store) -> None:
            self.store = store

        def correct(self, correction_set, answers, *, dry_run=False):
            assert correction_set is correction_sentinel
            assert answers is answers_sentinel
            assert dry_run is True
            return SimpleNamespace(
                validation=_validation(),
                receipt=_receipt(),
                generation_id=None,
                exports={},
            )

    monkeypatch.setattr(cli_production_closure, "ProductionClosureService", Service)

    result = runner.invoke(
        cli.app,
        [
            "production",
            "correct",
            str(tmp_path / "project"),
            str(corrections_path),
            "--answers",
            str(answers_path),
            "--dry-run",
            "--json",
        ],
    )

    assert result.exit_code == ExitCode.SUCCESS, result.output
    assert [name for name, _ in reads] == ["ProductionCorrectionSet", "ProductionReviewAnswers"]
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["generation_id"] is None
    assert payload["exports"] == {}


def test_review_rejects_invalid_answers_with_stable_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    answers_path = tmp_path / "invalid-answers.json"
    answers_path.write_text("{}", encoding="utf-8")

    class Service:
        def __init__(self, store) -> None:
            raise AssertionError("输入校验失败前不应构造闭环服务")

    monkeypatch.setattr(cli_production_closure, "ProductionClosureService", Service)

    result = runner.invoke(
        cli.app,
        [
            "production",
            "review",
            str(tmp_path / "project"),
            "--answers",
            str(answers_path),
            "--json",
        ],
    )

    assert result.exit_code == ExitCode.ERROR
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "ValidationError" in payload["error"]


def test_local_closure_module_does_not_depend_on_model_settings(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = inspect.getsource(cli_production_closure)
    assert "get_settings" not in source
    assert "movie_breakdown.config" not in source
    assert "Agno" not in source

    def fail_settings():
        raise AssertionError("制片本地闭环不应读取模型设置")

    class Service:
        def __init__(self, store) -> None:
            self.store = store

        def plan(self):
            return SimpleNamespace(validation=_validation(), exports={})

    monkeypatch.setattr(cli_production, "get_settings", fail_settings)
    monkeypatch.setattr(cli_production_closure, "ProductionClosureService", Service)

    result = runner.invoke(
        cli.app,
        ["production", "plan", str(tmp_path / "project"), "--json"],
    )

    assert result.exit_code == ExitCode.SUCCESS, result.output
