"""制片校验共享的问题构造、ID 引用和证据检查。"""

from __future__ import annotations

from collections.abc import Iterable

from movie_breakdown.domain.base import Severity
from movie_breakdown.domain.run import ValidationIssue
from movie_breakdown.domain.scene_analysis import Evidence
from movie_breakdown.domain.source import Scene


def production_issue(
    severity: Severity,
    code: str,
    message: str,
    reference: str | None = None,
) -> ValidationIssue:
    """构造字段一致的制片校验问题。

    Args:
        severity: 问题严重程度。
        code: 稳定机器错误码。
        message: 中文问题说明。
        reference: 可选的业务对象定位。

    Returns:
        可写入制片校验报告的问题。
    """
    return ValidationIssue(severity=severity, code=code, message=message, reference=reference)


def check_unique(
    values: list[str],
    code: str,
    message: str,
    issues: list[ValidationIssue],
    reference: str | None = None,
) -> None:
    """把重复稳定 ID 追加为错误。

    Args:
        values: 待检查的稳定 ID。
        code: 重复时使用的机器错误码。
        message: 重复时使用的中文说明。
        issues: 接收问题的可变列表。
        reference: 可选的业务对象定位。
    """
    if len(values) != len(set(values)):
        issues.append(production_issue(Severity.ERROR, code, message, reference))


def check_refs(
    values: Iterable[str],
    known: set[str],
    code: str,
    reference: str,
    issues: list[ValidationIssue],
) -> None:
    """把一组悬空 ID 追加为可定位错误。

    Args:
        values: 待检查的引用 ID。
        known: 当前允许引用的 ID 集合。
        code: 悬空引用使用的机器错误码。
        reference: 引用来源的业务定位。
        issues: 接收问题的可变列表。
    """
    for value in values:
        if value not in known:
            issues.append(
                production_issue(
                    Severity.ERROR,
                    code,
                    f"引用未知 ID：{value}",
                    reference,
                )
            )


def validate_production_evidence(
    evidence_items: list[Evidence],
    scenes: dict[str, Scene],
    allowed_scene_ids: set[str],
    issues: list[ValidationIssue],
    reference: str,
) -> None:
    """要求证据位于允许场景、行号范围内且逐字匹配。

    Args:
        evidence_items: 待验证的证据列表。
        scenes: 共享场景索引。
        allowed_scene_ids: 当前结论允许引用的场景集合。
        issues: 接收问题的可变列表。
        reference: 当前结论的业务定位。
    """
    for evidence in evidence_items:
        scene = scenes.get(evidence.scene_id)
        if scene is None:
            issues.append(
                production_issue(
                    Severity.ERROR,
                    "production.evidence_scene",
                    "证据引用未知场景。",
                    reference,
                )
            )
            continue
        if evidence.scene_id not in allowed_scene_ids:
            issues.append(
                production_issue(
                    Severity.ERROR,
                    "production.evidence_scope",
                    "证据超出当前制片项的场景范围。",
                    reference,
                )
            )
        span = evidence.source_span
        if (
            span.line_start < scene.source_span.line_start
            or span.line_end > scene.source_span.line_end
        ):
            issues.append(
                production_issue(
                    Severity.ERROR,
                    "production.evidence_span",
                    "证据行号超出场景范围。",
                    reference,
                )
            )
            continue
        start = span.line_start - scene.source_span.line_start
        end = span.line_end - scene.source_span.line_start + 1
        expected_excerpt = "\n".join(scene.text.splitlines()[start:end]).strip()[:300].rstrip()
        if not evidence.excerpt.strip() or evidence.excerpt.strip() != expected_excerpt:
            issues.append(
                production_issue(
                    Severity.ERROR,
                    "production.evidence_excerpt",
                    "证据摘录与声明行号的场景原文不一致。",
                    reference,
                )
            )
