from pathlib import Path
from types import SimpleNamespace

from pydantic import SecretStr

from movie_breakdown.application.doctor import DoctorService
from movie_breakdown.config import AppSettings


def _settings(key: str | None = "secret") -> AppSettings:
    return AppSettings.model_construct(
        deepseek_api_key=SecretStr(key) if key else None,
        model="deepseek-v4-pro",
        request_timeout_seconds=30,
    )


def test_doctor_online_model_check_without_leaking_key(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = DoctorService(_settings())
    client = SimpleNamespace(
        models=SimpleNamespace(
            list=lambda: SimpleNamespace(data=[SimpleNamespace(id="deepseek-v4-pro")])
        )
    )
    monkeypatch.setattr(service, "_create_client", lambda: client)

    report = service.run(tmp_path, online=True)

    assert report.ok
    assert "secret" not in report.model_dump_json()


def test_doctor_reports_missing_api_key(tmp_path: Path) -> None:
    report = DoctorService(_settings(None)).run(tmp_path, online=False)

    assert not report.ok
    assert any("DEEPSEEK_API_KEY" in check.message for check in report.checks)


def test_doctor_reports_unavailable_configured_model(tmp_path: Path, monkeypatch) -> None:
    service = DoctorService(_settings())
    client = SimpleNamespace(
        models=SimpleNamespace(
            list=lambda: SimpleNamespace(data=[SimpleNamespace(id="deepseek-v4-flash")])
        )
    )
    monkeypatch.setattr(service, "_create_client", lambda: client)

    report = service.run(tmp_path, online=True)

    assert not report.ok
    assert any("模型列表中没有" in check.message for check in report.checks)
