import json
from pathlib import Path

from pydantic import SecretStr
from typer.testing import CliRunner

from movie_breakdown import cli
from movie_breakdown.config import AppSettings
from tests.test_pipeline import _FakeAnalyzer

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


def _source(tmp_path: Path) -> Path:
    path = tmp_path / "示例剧本.txt"
    path.write_text(
        """示例电影
1、车站 日 外
小王进站。
2、月台 日 外
小王登上月台。
3、列车 夜 内
小王乘车离开。
""",
        encoding="utf-8",
    )
    return path


def test_help_and_version_are_available() -> None:
    help_result = runner.invoke(cli.app, ["--help"])
    version_result = runner.invoke(cli.app, ["--version"])

    assert help_result.exit_code == 0
    assert "叙事结构拆解" in help_result.stdout
    assert "analyze" in help_result.stdout
    assert version_result.exit_code == 0
    assert "1.0.0" in version_result.stdout


def test_analyze_status_validate_export_and_resume_json(tmp_path: Path, monkeypatch) -> None:
    analyzer = _FakeAnalyzer()
    monkeypatch.setattr(cli, "get_settings", _settings)
    monkeypatch.setattr(cli, "AgnoNarrativeAnalyzer", lambda *args, **kwargs: analyzer)
    source = _source(tmp_path)
    project = tmp_path / "project"

    analyzed = runner.invoke(
        cli.app,
        ["analyze", str(source), "--project", str(project), "--json"],
    )
    status = runner.invoke(cli.app, ["status", str(project), "--json"])
    validated = runner.invoke(cli.app, ["validate", str(project), "--json"])
    exported = runner.invoke(
        cli.app,
        ["export", str(project), "--format", "all", "--json"],
    )
    reviewed = runner.invoke(
        cli.app,
        ["review", str(project), "--sample-size", "6", "--json"],
    )
    resumed = runner.invoke(cli.app, ["resume", str(project), "--json"])

    assert analyzed.exit_code == 0, analyzed.output
    assert json.loads(analyzed.stdout)["ok"] is True
    assert status.exit_code == 0
    assert "scene_analysis" in json.loads(status.stdout)["stages"]
    assert "character_dossiers" in json.loads(status.stdout)["stages"]
    assert (project / "artifacts" / "character_dossiers.json").is_file()
    assert validated.exit_code == 0
    assert json.loads(validated.stdout)["valid"] is True
    assert exported.exit_code == 0
    assert Path(json.loads(exported.stdout)["exports"]["json"]).is_file()
    assert reviewed.exit_code == 0, reviewed.output
    review_payload = json.loads(reviewed.stdout)
    assert review_payload["ok"] is True
    assert review_payload["human_summary"]["target_count"] == 6
    assert Path(review_payload["exports"]["answers_template"]).is_file()
    assert resumed.exit_code == 0
    assert analyzer.scene_calls == 3
    assert analyzer.global_calls == 1


def test_analyze_reports_missing_key_as_json(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: _settings(None))

    result = runner.invoke(
        cli.app,
        [
            "analyze",
            str(_source(tmp_path)),
            "--project",
            str(tmp_path / "project"),
            "--json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "DEEPSEEK_API_KEY" in payload["error"]
