"""模型原始结构在进入严格 Pydantic 边界前的有限净化。"""

from __future__ import annotations

from typing import Any

_COMPLEXITY_LEVEL_BY_SCORE = {
    1: "low",
    2: "medium",
    3: "medium",
    4: "high",
    5: "critical",
}


def truncate_evidence_excerpts(value: Any, limit: int = 300) -> Any:
    """递归截断模型偶尔返回的超长证据摘录。

    Args:
        value: JSON 兼容的模型原始返回值。
        limit: Evidence Schema 允许的最大字符数。

    Returns:
        保持原结构且证据摘录不超过限制的新值。
    """
    if isinstance(value, list):
        return [truncate_evidence_excerpts(item, limit) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: truncate_evidence_excerpts(item, limit) for key, item in value.items()}
    if {
        "scene_id",
        "source_span",
        "excerpt",
    } <= result.keys() and isinstance(result["excerpt"], str):
        result["excerpt"] = result["excerpt"][:limit]
    return result


def normalize_character_biography_payload(value: Any) -> Any:
    """在人物小传校验前修复模型常见的有界列表偏差。

    该净化只处理可确定恢复的冗余或超量引用，不修改人物声明内容，
    以便核心 ``claims`` 继续由严格 Schema 判断是否有效。

    Args:
        value: JSON 兼容的人物小传原始返回值。

    Returns:
        保持输入语义且满足可恢复列表约束的新值；非字典值原样返回。
    """
    if not isinstance(value, dict):
        return value

    result = dict(value)
    result["context_scene_ids"] = _unique_limited(result.get("context_scene_ids"), 8)
    result["key_relationship_ids"] = _unique_limited(
        result.get("key_relationship_ids"),
        6,
    )

    representative_lines = result.get("representative_lines")
    if isinstance(representative_lines, list):
        result["representative_lines"] = representative_lines[:3]

    unknowns = result.get("unknowns")
    if isinstance(unknowns, list):
        claimed_categories = _claim_categories(result.get("claims"))
        blocked_categories = ["overview", *claimed_categories]
        result["unknowns"] = [
            category for category in _unique_values(unknowns) if category not in blocked_categories
        ]
    return result


def normalize_production_payload(value: Any) -> Any:
    """在制片 Schema 校验前回填由分数唯一决定的复杂度等级。

    只修复 ``complexity.level`` 这个冗余派生字段，不改变模型给出的复杂度
    分数、因素或任何制片判断；未知分数和异常结构继续交给严格 Schema 拒绝。

    Args:
        value: JSON 兼容的单场制片模型原始返回值。

    Returns:
        复杂度等级与合法分数一致的新字典；非字典值原样返回。
    """
    if not isinstance(value, dict):
        return value
    result = dict(value)
    complexity = result.get("complexity")
    if not isinstance(complexity, dict):
        return result
    score = complexity.get("score")
    if isinstance(score, bool) or score not in _COMPLEXITY_LEVEL_BY_SCORE:
        return result
    result["complexity"] = {
        **complexity,
        "level": _COMPLEXITY_LEVEL_BY_SCORE[score],
    }
    return result


def _unique_limited(value: Any, limit: int) -> Any:
    """对列表去重保序并限制长度，其他类型留给 Schema 报错。

    Args:
        value: 待处理的模型字段值。
        limit: 列表允许保留的最大元素数。

    Returns:
        去重并截断后的新列表，或未修改的非列表值。
    """
    if not isinstance(value, list):
        return value
    return _unique_values(value)[:limit]


def _unique_values(values: list[Any]) -> list[Any]:
    """使用相等性比较为任意 JSON 列表去重并保持首次出现顺序。

    Args:
        values: 可能包含重复项的 JSON 兼容列表。

    Returns:
        仅保留每个值首次出现位置的新列表。
    """
    unique: list[Any] = []
    for value in values:
        if value not in unique:
            unique.append(value)
    return unique


def _claim_categories(value: Any) -> list[Any]:
    """提取结构正确的普通人物声明分类并保持首次出现顺序。

    Args:
        value: 模型返回的 ``claims`` 字段值。

    Returns:
        可识别声明中的唯一分类列表；结构异常项由后续 Schema 处理。
    """
    if not isinstance(value, list):
        return []
    categories = [item.get("category") for item in value if isinstance(item, dict)]
    return _unique_values([category for category in categories if category is not None])
