"""人工修正与叙事稳定版封版的 Typer 子命令。"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, NoReturn

import typer
from rich.console import Console

from movie_breakdown.application.closure import NarrativeClosureService
from movie_breakdown.cli_render import print_json
from movie_breakdown.cli_types import ExitCode
from movie_breakdown.infrastructure.storage import ProjectStore

console = Console()
error_console = Console(stderr=True)


def correct_project(
    project: Annotated[Path, typer.Argument(help="已有拆解项目目录。")],
    corrections: Annotated[Path, typer.Argument(help="结构化人工修正 JSON。")],
    answers: Annotated[
        Path,
        typer.Option("--answers", help="产生本次修正建议的专家评审答案 JSON。"),
    ],
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="只验证和预览，不激活修正或更新导出。"),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="输出机器可读 JSON。"),
    ] = False,
) -> None:
    """严格预览或激活专家人工修正，全程不调用模型。

    Args:
        project: 已完成叙事分析的项目目录。
        corrections: 与基础分析指纹绑定的结构化修正集合。
        answers: 提出对应修正建议的专家答案。
        dry_run: 是否只执行全量安全检查。
        json_output: 是否只输出机器可读结果。
    """
    try:
        result = NarrativeClosureService(ProjectStore(project)).apply_corrections(
            corrections,
            answers,
            dry_run=dry_run,
        )
    except Exception as error:
        _fail(error, json_output)
    payload = {
        "ok": True,
        "dry_run": result.dry_run,
        "analysis_fingerprint": result.analysis_fingerprint,
        "receipt": result.receipt.model_dump(mode="json"),
        "exports": result.exports,
    }
    if json_output:
        print_json(payload, console)
        return
    action = "预览通过" if result.dry_run else "已激活"
    console.print(f"[green]{action}[/green]：{result.receipt.applied_count} 条人工修正")
    console.print(f"分析指纹：{result.analysis_fingerprint}")
    for kind, path in result.exports.items():
        console.print(f"[green]{kind}[/green]：{path}")


def finalize_project(
    project: Annotated[Path, typer.Argument(help="已有拆解项目目录。")],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="输出机器可读 JSON。"),
    ] = False,
) -> None:
    """根据当前正式分析和专家评审结果执行稳定版门禁。

    Args:
        project: 已完成修正、重导出和专家复核的项目目录。
        json_output: 是否只输出机器可读结果。
    """
    try:
        result = NarrativeClosureService(ProjectStore(project)).finalize()
    except Exception as error:
        _fail(error, json_output)
    if json_output:
        print_json(
            {
                "ok": result.report.stable,
                "report": result.report.model_dump(mode="json"),
                "exports": result.exports,
            },
            console,
        )
    else:
        decision = "稳定，可封版" if result.report.stable else "阻断，不可封版"
        color = "green" if result.report.stable else "red"
        console.print(f"[{color}]{decision}[/{color}]")
        for check in result.report.checks:
            status = "通过" if check.passed else "阻断"
            console.print(f"- {status} · {check.name}：{check.message}")
        for kind, path in result.exports.items():
            console.print(f"[green]{kind}[/green]：{path}")
    if not result.report.stable:
        raise typer.Exit(ExitCode.VALIDATION_FAILED)


def _fail(error: Exception, json_output: bool) -> NoReturn:
    """以稳定格式报告收尾命令错误并退出。"""
    message = f"{type(error).__name__}: {error}"
    if json_output:
        print_json({"ok": False, "error": message}, console)
    else:
        error_console.print(f"[red]错误[/red]：{message}")
    raise typer.Exit(ExitCode.ERROR)
