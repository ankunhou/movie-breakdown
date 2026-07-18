"""Typer 命令共享的中文终端与 JSON 展示。"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel
from rich.console import Console
from rich.table import Table

from movie_breakdown.domain.doctor import CheckStatus, DoctorReport
from movie_breakdown.domain.production_catalog import ProductionValidationReport
from movie_breakdown.domain.run import RunManifest, ValidationReport

_STATUS_LABELS = {
    "pending": "等待",
    "running": "运行中",
    "success": "成功",
    "failed": "失败",
    "stale": "已过期",
}


def print_json(value: BaseModel | dict[str, Any] | list[Any], console: Console) -> None:
    """把模型或普通结构以稳定中文 JSON 输出。

    Args:
        value: Pydantic 模型或可 JSON 序列化的数据。
        console: 接收标准输出的 Rich Console。
    """
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", exclude_computed_fields=True)
    console.print_json(
        json.dumps(value, ensure_ascii=False, sort_keys=True),
        highlight=False,
    )


def render_status(
    manifest: RunManifest,
    console: Console,
    title: str = "剧本拆解阶段状态",
) -> None:
    """把运行清单渲染为紧凑中文表格。

    Args:
        manifest: 当前项目运行清单。
        console: 接收标准输出的 Rich Console。
        title: 表格标题。
    """
    table = Table(title=title)
    table.add_column("阶段")
    table.add_column("状态")
    table.add_column("版本")
    table.add_column("Token", justify="right")
    table.add_column("说明")
    for stage in manifest.stages.values():
        table.add_row(
            stage.name,
            _STATUS_LABELS[stage.status.value],
            stage.version,
            str(stage.usage.total_tokens),
            stage.error or "",
        )
    console.print(table)


def render_production_validation(
    report: ProductionValidationReport,
    console: Console,
) -> None:
    """展示制片覆盖率、目录规模和问题列表。

    Args:
        report: 最新独立制片一致性校验报告。
        console: 接收标准输出的 Rich Console。
    """
    state = "[green]通过[/green]" if report.valid else "[red]未通过[/red]"
    console.print(
        f"制片校验：{state}；场景覆盖：{report.analyzed_scene_count}/{report.scene_count} "
        f"({report.coverage:.1%})；目录项目：{report.catalog_item_count}"
    )
    if not report.issues:
        return
    table = Table(title="制片校验问题")
    table.add_column("级别")
    table.add_column("代码")
    table.add_column("位置")
    table.add_column("说明")
    for issue in report.issues:
        table.add_row(issue.severity.value, issue.code, issue.reference or "", issue.message)
    console.print(table)


def render_validation(report: ValidationReport, console: Console) -> None:
    """展示校验结论、覆盖率和问题列表。

    Args:
        report: 最新本地一致性校验报告。
        console: 接收标准输出的 Rich Console。
    """
    state = "[green]通过[/green]" if report.valid else "[red]未通过[/red]"
    console.print(
        f"校验结果：{state}；场景覆盖：{report.analyzed_scene_count}/{report.scene_count} "
        f"({report.coverage:.1%})"
    )
    if not report.issues:
        return
    table = Table(title="校验问题")
    table.add_column("级别")
    table.add_column("代码")
    table.add_column("位置")
    table.add_column("说明")
    for issue in report.issues:
        table.add_row(issue.severity.value, issue.code, issue.reference or "", issue.message)
    console.print(table)


def render_doctor(report: DoctorReport, console: Console) -> None:
    """把环境诊断报告渲染为状态表格。

    Args:
        report: 本地和可选在线诊断报告。
        console: 接收标准输出的 Rich Console。
    """
    styles = {
        CheckStatus.PASS: "green",
        CheckStatus.WARNING: "yellow",
        CheckStatus.FAIL: "red",
        CheckStatus.SKIPPED: "dim",
    }
    table = Table(title="movie-breakdown 环境诊断")
    table.add_column("检查项")
    table.add_column("状态")
    table.add_column("说明")
    for check in report.checks:
        style = styles[check.status]
        table.add_row(check.name, f"[{style}]{check.status.value}[/{style}]", check.message)
    console.print(table)
