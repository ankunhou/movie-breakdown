import json
from pathlib import Path

from movie_breakdown.application.release_exporting import ReleaseGateExporter
from movie_breakdown.domain.release import ReleaseGateCheck, ReleaseGateCheckCode, ReleaseGateReport
from movie_breakdown.infrastructure.storage import ProjectStore


def _make_report(*, stable: bool) -> ReleaseGateReport:
    checks = [
        ReleaseGateCheck(
            code=code,
            name=f"检查 {index}",
            passed=stable or index > 1,
            message=f"第 {index} 项检查消息。",
            references=["target-001", "scene|0001"] if index == 1 else [],
        )
        for index, code in enumerate(ReleaseGateCheckCode, start=1)
    ]
    return ReleaseGateReport(
        analysis_fingerprint="fingerprint-123",
        stable=stable,
        checks=checks,
    )


def test_export_writes_json_artifact_and_complete_markdown(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "project")
    report = _make_report(stable=True)

    paths = ReleaseGateExporter().export(store, report)

    assert paths == {
        "artifact": str(store.artifacts_dir / "release_gate.json"),
        "markdown": str(store.exports_dir / "release-gate.md"),
    }
    payload = json.loads(Path(paths["artifact"]).read_text("utf-8"))
    markdown = Path(paths["markdown"]).read_text("utf-8")
    assert payload["stable"] is True
    assert payload["analysis_fingerprint"] == "fingerprint-123"
    assert len(payload["checks"]) == 8
    assert "总体结论：**稳定，可封版**" in markdown
    assert "`fingerprint-123`" in markdown
    assert "检查结果：8/8 项通过" in markdown
    assert markdown.count("| `") == 8
    for code in ReleaseGateCheckCode:
        assert f"`{code.value}`" in markdown
    assert "第 1 项检查消息。" in markdown
    assert "target-001、scene\\|0001" in markdown


def test_export_overwrites_previous_decision_with_blocking_report(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "project")
    exporter = ReleaseGateExporter()
    exporter.export(store, _make_report(stable=True))

    paths = exporter.export(store, _make_report(stable=False))

    persisted = store.read_model(Path(paths["artifact"]), ReleaseGateReport)
    markdown = Path(paths["markdown"]).read_text("utf-8")
    assert persisted.stable is False
    assert "总体结论：**阻断，不可封版**" in markdown
    assert "检查结果：7/8 项通过" in markdown
    assert "| 阻断 | 第 1 项检查消息。 |" in markdown
    assert not list(store.artifacts_dir.glob(".release_gate.json.*"))
    assert not list(store.exports_dir.glob(".release-gate.md.*"))
