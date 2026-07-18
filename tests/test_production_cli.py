import json
from pathlib import Path

from pydantic import SecretStr
from typer.testing import CliRunner

from movie_breakdown import cli, cli_production
from movie_breakdown.config import AppSettings
from movie_breakdown.infrastructure.production_storage import ProductionStore
from tests.test_production_pipeline import _FakeProductionAnalyzer, _parent_store

runner = CliRunner()


def _settings(key: str | None = "secret") -> AppSettings:
    return AppSettings.model_construct(
        deepseek_api_key=SecretStr(key) if key else None,
        model="deepseek-v4-pro",
        max_retries=2,
        concurrency=2,
        thinking_enabled=True,
        reasoning_effort="high",
        request_timeout_seconds=30,
    )


def test_production_help_is_independent_and_complete() -> None:
    root_help = runner.invoke(cli.app, ["--help"])
    production_help = runner.invoke(cli.app, ["production", "--help"])

    assert root_help.exit_code == 0
    assert "production" in root_help.stdout
    assert production_help.exit_code == 0
    for command in ("analyze", "resume", "status", "validate", "export"):
        assert command in production_help.stdout
    assert "制片复杂度" in production_help.stdout


def test_production_cli_full_flow_uses_separate_manifest(tmp_path: Path, monkeypatch) -> None:
    parent = _parent_store(tmp_path)
    narrative_manifest = parent.manifest_path.read_bytes()
    analyzer = _FakeProductionAnalyzer()
    monkeypatch.setattr(cli_production, "get_settings", _settings)
    monkeypatch.setattr(
        cli_production,
        "AgnoProductionAnalyzer",
        lambda *args, **kwargs: analyzer,
    )

    analyzed = runner.invoke(
        cli.app,
        ["production", "analyze", str(parent.root), "--json"],
    )
    status = runner.invoke(
        cli.app,
        ["production", "status", str(parent.root), "--json"],
    )
    validated = runner.invoke(
        cli.app,
        ["production", "validate", str(parent.root), "--json"],
    )
    exported = runner.invoke(
        cli.app,
        [
            "production",
            "export",
            str(parent.root),
            "--format",
            "csv",
            "--json",
        ],
    )
    resumed = runner.invoke(
        cli.app,
        ["production", "resume", str(parent.root), "--json"],
    )

    assert analyzed.exit_code == 0, analyzed.output
    analyzed_payload = json.loads(analyzed.stdout)
    assert analyzed_payload["ok"] is True
    assert analyzed_payload["validation"]["catalog_item_count"] == 5
    assert status.exit_code == 0
    assert set(json.loads(status.stdout)["stages"]) == {
        "production_scene_analysis",
        "production_catalog",
        "production_validation",
        "production_export",
    }
    assert validated.exit_code == 0
    assert json.loads(validated.stdout)["valid"] is True
    assert exported.exit_code == 0
    assert set(json.loads(exported.stdout)["exports"]) == {"scenes_csv", "catalog_csv"}
    assert resumed.exit_code == 0
    assert analyzer.calls == 3
    assert parent.manifest_path.read_bytes() == narrative_manifest


def test_local_production_commands_never_read_settings(tmp_path: Path, monkeypatch) -> None:
    parent = _parent_store(tmp_path)
    analyzer = _FakeProductionAnalyzer()
    monkeypatch.setattr(cli_production, "get_settings", _settings)
    monkeypatch.setattr(
        cli_production,
        "AgnoProductionAnalyzer",
        lambda *args, **kwargs: analyzer,
    )
    analyzed = runner.invoke(
        cli.app,
        ["production", "analyze", str(parent.root), "--json"],
    )
    assert analyzed.exit_code == 0, analyzed.output

    def fail_settings():
        raise AssertionError("本地制片命令不应读取环境密钥")

    monkeypatch.setattr(cli_production, "get_settings", fail_settings)

    status = runner.invoke(cli.app, ["production", "status", str(parent.root), "--json"])
    validated = runner.invoke(
        cli.app,
        ["production", "validate", str(parent.root), "--json"],
    )
    exported = runner.invoke(
        cli.app,
        ["production", "export", str(parent.root), "--format", "json", "--json"],
    )

    assert status.exit_code == validated.exit_code == exported.exit_code == 0


def test_production_validate_uses_exit_code_two_for_incomplete_data(
    tmp_path: Path,
    monkeypatch,
) -> None:
    parent = _parent_store(tmp_path)
    analyzer = _FakeProductionAnalyzer()
    monkeypatch.setattr(cli_production, "get_settings", _settings)
    monkeypatch.setattr(
        cli_production,
        "AgnoProductionAnalyzer",
        lambda *args, **kwargs: analyzer,
    )
    runner.invoke(cli.app, ["production", "analyze", str(parent.root), "--json"])
    ProductionStore(parent).write_jsonl("scene_elements", [])

    result = runner.invoke(
        cli.app,
        ["production", "validate", str(parent.root), "--json"],
    )

    assert result.exit_code == 2
    assert json.loads(result.stdout)["valid"] is False


def test_production_analyze_reports_missing_key_without_writes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    parent = _parent_store(tmp_path)
    monkeypatch.setattr(cli_production, "get_settings", lambda: _settings(None))

    result = runner.invoke(
        cli.app,
        ["production", "analyze", str(parent.root), "--json"],
    )

    assert result.exit_code == 1
    assert "DEEPSEEK_API_KEY" in json.loads(result.stdout)["error"]
    assert not (parent.root / "production").exists()
