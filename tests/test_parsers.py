from pathlib import Path

import pytest
from pypdf import PdfWriter

from movie_breakdown.infrastructure.parsers import (
    EmptySourceError,
    UnsupportedSourceError,
    read_and_normalize,
)


def test_read_utf8_text_and_normalize_blank_lines(tmp_path: Path) -> None:
    source = tmp_path / "剧本.txt"
    source.write_bytes(
        "片名\r\n\r\n\r\n\r\n1、地点 日 外   \r\n这是一段足够长度的剧本正文内容。".encode()
    )

    document = read_and_normalize(source)

    assert document.title == "片名"
    assert document.source.line_count == 5
    assert document.lines[-2].text == "1、地点 日 外"
    assert document.source.fingerprint


def test_reject_unsupported_source(tmp_path: Path) -> None:
    source = tmp_path / "剧本.docx"
    source.write_text("这是一份不支持的剧本文档内容", encoding="utf-8")

    with pytest.raises(UnsupportedSourceError, match="暂不支持"):
        read_and_normalize(source)


def test_reject_empty_source(tmp_path: Path) -> None:
    source = tmp_path / "空.txt"
    source.write_text("空", encoding="utf-8")

    with pytest.raises(EmptySourceError, match="内容过少"):
        read_and_normalize(source)


def test_pdf_uses_filename_as_title(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "六个凶手.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with source.open("wb") as output:
        writer.write(output)
    monkeypatch.setattr(
        "movie_breakdown.infrastructure.parsers._read_pdf",
        lambda _path: [
            type(
                "Page",
                (),
                {"number": 1, "text": "1\n第一场 日 内\n人物走入房间，发现桌上放着一封信。"},
            )()
        ],
    )

    document = read_and_normalize(source)

    assert document.title == "六个凶手"
