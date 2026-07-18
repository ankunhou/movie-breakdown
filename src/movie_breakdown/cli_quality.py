"""离线叙事语义质量评测的 Typer 子命令。"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, NoReturn

import typer
from rich.console import Console

from movie_breakdown.application.pipeline import AnalysisPipeline
from movie_breakdown.cli_render import print_json
from movie_breakdown.cli_types import ExitCode
from movie_breakdown.domain.quality import HumanReviewAnswers
from movie_breakdown.infrastructure.storage import ProjectStore

console = Console()
error_console = Console(stderr=True)


def review_project(
    project: Annotated[Path, typer.Argument(help="已有拆解项目目录。")],
    sample_size: Annotated[
        int,
        typer.Option("--sample-size", help="人工风险抽检目标数。", min=6, max=50),
    ] = 16,
    answers: Annotated[
        Path | None,
        typer.Option("--answers", help="已填写的人工评测 JSON；允许部分完成。"),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="输出机器可读 JSON。"),
    ] = False,
) -> None:
    """离线生成自动风险信号、人工抽检表并可选合并专家答案。

    Args:
        project: 已有且完成叙事分析的项目目录。
        sample_size: 稳定风险抽样的目标数，范围为 6 到 50。
        answers: 可选的已填写人工答案文件，必须匹配当前分析指纹。
        json_output: 是否只在标准输出写入机器可读结果。
    """
    try:
        parsed_answers = _load_answers(answers)
        result = AnalysisPipeline(ProjectStore(project)).review_only(
            sample_size,
            parsed_answers,
        )
    except Exception as error:
        _fail(error, json_output)
    summary = result.report.human_summary
    if json_output:
        print_json(
            {
                "ok": True,
                "analysis_fingerprint": result.report.analysis_fingerprint,
                "automatic_signals": [
                    signal.model_dump(mode="json") for signal in result.report.automatic_signals
                ],
                "human_summary": summary.model_dump(mode="json"),
                "exports": result.exports,
            },
            console,
        )
        return
    console.print("[green]叙事语义质量评测已生成[/green]")
    console.print("自动信号用于风险筛查，不是叙事正确率。")
    console.print(
        f"人工覆盖：{summary.reviewed_count}/{summary.target_count} ({summary.coverage:.1%})"
    )
    for kind, path in result.exports.items():
        console.print(f"[green]{kind}[/green]：{path}")


def _load_answers(path: Path | None) -> HumanReviewAnswers | None:
    """读取可选人工答案并执行严格 Pydantic 校验。"""
    if path is None:
        return None
    return HumanReviewAnswers.model_validate_json(path.read_text(encoding="utf-8"))


def _fail(error: Exception, json_output: bool) -> NoReturn:
    """以稳定格式输出评测命令错误并退出。"""
    message = f"{type(error).__name__}: {error}"
    if json_output:
        print_json({"ok": False, "error": message}, console)
    else:
        error_console.print(f"[red]错误[/red]：{message}")
    raise typer.Exit(ExitCode.ERROR)
