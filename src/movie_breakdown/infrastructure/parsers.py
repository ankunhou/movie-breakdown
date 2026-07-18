"""TXT、Markdown 和文本型 PDF 的导入与规范化。"""

from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

from movie_breakdown.domain.source import NormalizedDocument, SourceDocument, SourceLine
from movie_breakdown.infrastructure.fingerprint import hash_bytes

SUPPORTED_SUFFIXES = {".txt", ".md", ".pdf"}


class UnsupportedSourceError(ValueError):
    """源文件格式不在 MVP 支持范围内。"""


class EmptySourceError(ValueError):
    """源文件中没有足够的可分析文本。"""


@dataclass(frozen=True, slots=True)
class _RawPage:
    number: int | None
    text: str


def read_and_normalize(path: Path) -> NormalizedDocument:
    """读取支持的剧本文件并保留页码映射地规范化文本。

    Args:
        path: 待导入的 TXT、Markdown 或文本型 PDF 路径。

    Returns:
        带源文件指纹和位置映射的规范化文档。

    Raises:
        FileNotFoundError: 剧本路径不存在或不是文件。
        UnsupportedSourceError: 文件格式不在支持范围内。
        EmptySourceError: 没有提取到足够的可分析文本。
        UnicodeError: 文本编码无法识别。
    """
    source_path = path.resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"找不到剧本文件：{source_path}")
    suffix = source_path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise UnsupportedSourceError(
            f"暂不支持 {suffix or '无扩展名'} 文件，仅支持 TXT、Markdown 和文本型 PDF。"
        )

    raw_bytes = source_path.read_bytes()
    pages = _read_pdf(source_path) if suffix == ".pdf" else [_RawPage(None, _decode(raw_bytes))]
    lines = _normalize_pages(pages)
    visible = sum(len(line.text.strip()) for line in lines)
    if visible < 20:
        detail = "PDF 可能是扫描件，需要先进行 OCR。" if suffix == ".pdf" else "文件内容过少。"
        raise EmptySourceError(f"没有提取到足够的剧本文本；{detail}")

    title = (
        source_path.stem
        if suffix == ".pdf"
        else next((line.text.lstrip("# ") for line in lines if line.text.strip()), source_path.stem)
    )
    media_type = mimetypes.guess_type(source_path.name)[0] or "text/plain"
    page_count = len(pages) if suffix == ".pdf" else None
    source = SourceDocument(
        original_name=source_path.name,
        media_type=media_type,
        fingerprint=hash_bytes(raw_bytes),
        size_bytes=len(raw_bytes),
        page_count=page_count,
        line_count=len(lines),
    )
    return NormalizedDocument(source=source, title=title[:200], lines=lines)


def _read_pdf(path: Path) -> list[_RawPage]:
    reader = PdfReader(path)
    return [
        _RawPage(
            number=index,
            text=page.extract_text(extraction_mode="layout") or "",
        )
        for index, page in enumerate(reader.pages, start=1)
    ]


def _decode(raw_bytes: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeError("无法识别文本编码，请将剧本转换为 UTF-8。")


def _normalize_pages(pages: list[_RawPage]) -> list[SourceLine]:
    result: list[SourceLine] = []
    blank_count = 0
    for page in pages:
        normalized = page.text.replace("\r\n", "\n").replace("\r", "\n")
        normalized = normalized.replace("\u00a0", " ").replace("\u3000", " ")
        for page_line_number, raw_line in enumerate(normalized.split("\n"), start=1):
            text = re.sub(r"[ \t]+$", "", raw_line)
            if not text:
                blank_count += 1
                if blank_count > 2:
                    continue
            else:
                blank_count = 0
            result.append(
                SourceLine(
                    number=len(result) + 1,
                    text=text,
                    page_number=page.number,
                    page_line_number=page_line_number if page.number else None,
                )
            )
    while result and not result[-1].text:
        result.pop()
    return [line.model_copy(update={"number": index}) for index, line in enumerate(result, start=1)]
