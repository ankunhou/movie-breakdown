"""为模型格式识别提取剧本首尾样本。"""

from __future__ import annotations

from movie_breakdown.domain.source import NormalizedDocument


def sample_format_pages(
    document: NormalizedDocument,
    pages_each_side: int = 3,
    lines_each_side: int = 150,
) -> str:
    """提取 PDF 首尾页面或文本首尾行供模型识别格式。

    Args:
        document: 保留页码映射的规范化剧本文档。
        pages_each_side: PDF 首尾各抽取的页数。
        lines_each_side: 无页码文本首尾各抽取的行数。

    Returns:
        带全局行号和可选页码标记的剧本格式样本。
    """
    page_count = document.source.page_count
    if page_count:
        selected = [
            line
            for line in document.lines
            if line.page_number is not None
            and (
                line.page_number <= pages_each_side
                or line.page_number > page_count - pages_each_side
            )
        ]
    else:
        selected = [
            *document.lines[:lines_each_side],
            *document.lines[-lines_each_side:],
        ]
    unique = {line.number: line for line in selected}
    return "\n".join(
        f"{line.number} [{f'第{line.page_number}页' if line.page_number else '文本'}] {line.text}"
        for line in unique.values()
    )
