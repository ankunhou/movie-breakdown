"""确定性制片总表使用的文本、ID、证据和数量纯函数。"""

from __future__ import annotations

import unicodedata
from collections.abc import Hashable, Iterable

from movie_breakdown.domain.production_common import QuantityBasis, QuantityEstimate
from movie_breakdown.domain.scene_analysis import Evidence
from movie_breakdown.infrastructure.fingerprint import content_fingerprint


def normalize_catalog_key(value: str | None) -> str:
    """对目录合并键执行不改变语义的最小 Unicode 规范化。

    Args:
        value: 原始名称、单位或可选子类型。

    Returns:
        经过 NFKC、空白折叠和大小写折叠的键。
    """
    normalized = unicodedata.normalize("NFKC", value or "")
    return " ".join(normalized.split()).casefold()


def display_text(value: str) -> str:
    """清理展示文本中的兼容字符和连续空白。

    Args:
        value: 需要进入制片总表的原始文本。

    Returns:
        保留语义与标点的可读文本。
    """
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.split())


def stable_catalog_id(prefix: str, key: Hashable) -> str:
    """根据完整规范键生成与输入顺序无关的目录 ID。

    Args:
        prefix: 地点、演员或元素等稳定类别前缀。
        key: 不包含场景顺序的完整目录合并键。

    Returns:
        类别前缀和十二位内容哈希组成的 ID。
    """
    return f"{prefix}-{content_fingerprint([prefix, key])[:12]}"


def unique_texts(values: Iterable[str | None]) -> list[str]:
    """按最小规范键去重文本并保留首个可读写法。

    Args:
        values: 已按剧本顺序排列的可选文本。

    Returns:
        去空值且顺序稳定的可读文本列表。
    """
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value:
            continue
        key = normalize_catalog_key(value)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(display_text(value))
    return result


def unique_values[T: Hashable](values: Iterable[T]) -> list[T]:
    """精确去重任意可哈希值并保留首次出现顺序。

    Args:
        values: 已按剧本顺序排列的可哈希值。

    Returns:
        精确去重后的稳定列表。
    """
    return list(dict.fromkeys(values))


def unique_evidence(
    values: Iterable[Evidence],
    scene_order: dict[str, int],
) -> list[Evidence]:
    """精确去重证据并按场景和行号稳定排序。

    Args:
        values: 来自同一聚合项的证据。
        scene_order: 场景 ID 到剧本顺序的映射。

    Returns:
        不按相似文本误删的可追溯证据列表。
    """
    indexed: dict[str, Evidence] = {}
    for evidence in values:
        indexed.setdefault(content_fingerprint(evidence), evidence)
    return sorted(
        indexed.values(),
        key=lambda item: (
            scene_order[item.scene_id],
            item.source_span.line_start,
            item.source_span.line_end,
            item.excerpt,
            item.confidence.value,
        ),
    )


def peak_quantity(values: Iterable[QuantityEstimate]) -> QuantityEstimate:
    """保守计算跨场景最大同时需求，不执行单位换算或数量求和。

    Args:
        values: 同一个严格聚合项在各场景声明的数量。

    Returns:
        同单位时的峰值边界；单位冲突或完全未知时返回未知数量。
    """
    quantities = list(values)
    units = unique_texts(item.unit for item in quantities)
    if len({normalize_catalog_key(item.unit) for item in quantities}) != 1:
        return QuantityEstimate(unit="单位待确认", basis=QuantityBasis.UNKNOWN)
    unit = units[0]
    known = [item for item in quantities if item.minimum is not None]
    if not known:
        return QuantityEstimate(unit=unit, basis=QuantityBasis.UNKNOWN)
    minimum = max(item.minimum for item in known if item.minimum is not None)
    all_have_upper = all(item.maximum is not None for item in quantities)
    maximum = (
        max(item.maximum for item in quantities if item.maximum is not None)
        if all_have_upper
        else None
    )
    if any(item.basis == QuantityBasis.ESTIMATED for item in quantities):
        basis = QuantityBasis.ESTIMATED
    elif maximum is None:
        basis = QuantityBasis.MINIMUM
    elif minimum == maximum:
        basis = QuantityBasis.EXACT
    else:
        basis = QuantityBasis.RANGE
    return QuantityEstimate(minimum=minimum, maximum=maximum, unit=unit, basis=basis)
