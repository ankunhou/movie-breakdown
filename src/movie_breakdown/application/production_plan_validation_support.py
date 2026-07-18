"""制片规划分级校验使用的通用辅助函数。"""

from __future__ import annotations

from movie_breakdown.domain.base import Severity
from movie_breakdown.domain.production_planning_validation import (
    ProductionPlanningIssue,
    ProductionReadinessLevel,
)
from movie_breakdown.domain.scene_analysis import Evidence
from movie_breakdown.domain.source import Scene


def planning_issue(
    code: str,
    message: str,
    blocks_levels: list[ProductionReadinessLevel],
    reference: str | None = None,
) -> ProductionPlanningIssue:
    """构造严重程度与阻断层级一致的规划问题。

    Args:
        code: 稳定机器码。
        message: 中文可操作说明。
        blocks_levels: 被该问题阻断的准备度层级。
        reference: 可选场景、单元或资源引用。

    Returns:
        可直接写入规划校验报告的问题。
    """
    severity = (
        Severity.ERROR
        if ProductionReadinessLevel.DRAFT_VALID in blocks_levels
        else Severity.WARNING
    )
    return ProductionPlanningIssue(
        severity=severity,
        code=code,
        message=message,
        reference=reference,
        blocks_levels=blocks_levels,
    )


def append_duplicate_issues(
    values: list[str],
    code: str,
    label: str,
    issues: list[ProductionPlanningIssue],
) -> None:
    """为重复稳定 ID 增加草稿级阻断。

    Args:
        values: 待检查的 ID 列表。
        code: 重复问题机器码。
        label: 用户可见对象名称。
        issues: 原地追加问题的结果列表。
    """
    duplicates = sorted({value for value in values if values.count(value) > 1})
    for value in duplicates:
        issues.append(
            planning_issue(
                code,
                f"{label} ID 重复。",
                list(ProductionReadinessLevel),
                value,
            )
        )


def evidence_is_located(scene: Scene, evidence: Evidence) -> bool:
    """检查规划证据能否按当前共享场景逐字定位。

    Args:
        scene: 证据声明所属的共享场景。
        evidence: 待定位的证据。

    Returns:
        场景、行号和摘录全部匹配时为真。
    """
    if evidence.scene_id != scene.id:
        return False
    start = evidence.source_span.line_start - scene.source_span.line_start
    end = evidence.source_span.line_end - scene.source_span.line_start + 1
    lines = scene.text.splitlines()
    if start < 0 or end > len(lines) or start >= end:
        return False
    excerpt = "\n".join(lines[start:end]).strip()[:300].rstrip()
    return bool(excerpt) and excerpt == evidence.excerpt


def level_is_clear(
    issues: list[ProductionPlanningIssue],
    levels: set[ProductionReadinessLevel],
) -> bool:
    """判断给定层级集合是否没有任何阻断问题。

    Args:
        issues: 全部规划校验问题。
        levels: 要求同时无阻断的问题层级。

    Returns:
        所有问题均不阻断指定层级时为真。
    """
    return not any(levels.intersection(issue.blocks_levels) for issue in issues)
