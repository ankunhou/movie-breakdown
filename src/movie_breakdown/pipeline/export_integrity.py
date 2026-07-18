"""导出文件缓存的确定性内容完整性校验。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path


def exported_contents_match(
    exports_dir: Path,
    filenames: Mapping[str, str],
    expected_contents: Mapping[str, str],
) -> bool:
    """核对一组现有导出文件是否逐字节等于当前渲染结果。

    Args:
        exports_dir: 导出文件所在目录。
        filenames: 格式名称到固定文件名的映射。
        expected_contents: 格式名称到当前确定性渲染文本的映射。

    Returns:
        文件集合、存在性和 UTF-8 字节内容均完全一致时返回真。
    """
    if set(filenames) != set(expected_contents):
        return False
    try:
        return all(
            exports_dir.joinpath(filenames[kind]).read_bytes() == content.encode("utf-8")
            for kind, content in expected_contents.items()
        )
    except OSError:
        return False
