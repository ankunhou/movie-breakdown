"""电影剧本叙事结构拆解的 Typer 命令行入口。"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, NoReturn

import typer
from rich.console import Console

from movie_breakdown import __version__
from movie_breakdown.application.doctor import DoctorService
from movie_breakdown.application.pipeline import AnalysisPipeline, PipelineRunResult
from movie_breakdown.cli_closure import correct_project, finalize_project
from movie_breakdown.cli_production import production_app
from movie_breakdown.cli_quality import review_project
from movie_breakdown.cli_render import (
    print_json,
    render_doctor,
    render_status,
    render_validation,
)
from movie_breakdown.cli_types import (
    ExitCode,
    ExportChoice,
    FormatDetection,
    StructureFramework,
)
from movie_breakdown.config import AppSettings, get_settings
from movie_breakdown.infrastructure.llm.agno_analyzer import AgnoNarrativeAnalyzer
from movie_breakdown.infrastructure.storage import ProjectStore

app = typer.Typer(
    name="movie-breakdown",
    help="可恢复、可追溯的电影剧本叙事结构拆解。",
    no_args_is_help=True,
    invoke_without_command=True,
    add_completion=False,
    pretty_exceptions_show_locals=False,
)
app.command(name="review")(review_project)
app.command(name="correct")(correct_project)
app.command(name="finalize")(finalize_project)
app.add_typer(production_app, name="production")
console = Console()
error_console = Console(stderr=True)
DEFAULT_DIRECTORY = Path.cwd()


@app.callback()
def root(
    version: Annotated[
        bool,
        typer.Option("--version", help="显示版本并退出。", is_eager=True),
    ] = False,
) -> None:
    """处理全局 CLI 选项。

    Args:
        version: 是否显示当前程序版本。
    """
    if version:
        console.print(__version__)
        raise typer.Exit(ExitCode.SUCCESS)


@app.command()
def analyze(
    source: Annotated[
        Path,
        typer.Argument(help="TXT、Markdown 或文本型 PDF 剧本路径。"),
    ],
    project: Annotated[
        Path,
        typer.Option("--project", "-p", help="新建的拆解项目目录。"),
    ],
    framework: Annotated[
        StructureFramework,
        typer.Option("--framework", help="叙事结构分析框架。"),
    ] = StructureFramework.THREE_ACT,
    format_detection: Annotated[
        FormatDetection,
        typer.Option("--format-detection", help="场景格式识别策略。"),
    ] = FormatDetection.AUTO,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="输出机器可读 JSON。"),
    ] = False,
) -> None:
    """创建项目并执行完整叙事结构拆解。

    Args:
        source: 待分析的源剧本路径。
        project: 新建项目目录。
        framework: 使用的叙事结构框架。
        format_detection: 本地或模型辅助的格式识别策略。
        json_output: 是否输出机器可读 JSON。
    """
    try:
        settings = get_settings()
        pipeline = _model_pipeline(project, settings, json_output)
        config = settings.to_project_config(framework.value, format_detection.value)
        pipeline.initialize(source, config)
        result = pipeline.run()
    except Exception as error:
        _fail(error, json_output)
    _render_run(result, json_output)


@app.command()
def resume(
    project: Annotated[Path, typer.Argument(help="已有拆解项目目录。")],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="输出机器可读 JSON。"),
    ] = False,
) -> None:
    """从失败、未完成或过期阶段继续分析。

    Args:
        project: 已有拆解项目目录。
        json_output: 是否输出机器可读 JSON。
    """
    try:
        result = _model_pipeline(project, get_settings(), json_output).run()
    except Exception as error:
        _fail(error, json_output)
    _render_run(result, json_output)


@app.command(name="status")
def show_status(
    project: Annotated[Path, typer.Argument(help="已有拆解项目目录。")],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="输出机器可读 JSON。"),
    ] = False,
) -> None:
    """展示全部流水线阶段状态、用量和失败原因。

    Args:
        project: 已有拆解项目目录。
        json_output: 是否输出机器可读 JSON。
    """
    try:
        manifest = AnalysisPipeline(ProjectStore(project)).status()
    except Exception as error:
        _fail(error, json_output)
    if json_output:
        print_json(manifest, console)
    else:
        render_status(manifest, console)


@app.command(name="validate")
def validate_project(
    project: Annotated[Path, typer.Argument(help="已有拆解项目目录。")],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="输出机器可读 JSON。"),
    ] = False,
) -> None:
    """不调用模型地重新执行一致性校验。

    Args:
        project: 已有拆解项目目录。
        json_output: 是否输出机器可读 JSON。
    """
    try:
        report = AnalysisPipeline(ProjectStore(project)).validate_only()
    except Exception as error:
        _fail(error, json_output)
    if json_output:
        print_json(report, console)
    else:
        render_validation(report, console)
    if not report.valid:
        raise typer.Exit(ExitCode.VALIDATION_FAILED)


@app.command(name="export")
def export_project(
    project: Annotated[Path, typer.Argument(help="已有拆解项目目录。")],
    export_format: Annotated[
        ExportChoice,
        typer.Option("--format", help="正式报告格式。"),
    ] = ExportChoice.MARKDOWN,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="输出机器可读 JSON。"),
    ] = False,
) -> None:
    """从已验证产物重新生成指定格式的正式报告。

    Args:
        project: 已有拆解项目目录。
        export_format: Markdown、JSON 或全部格式。
        json_output: 是否输出机器可读 JSON。
    """
    try:
        paths = AnalysisPipeline(ProjectStore(project)).export_only(export_format.value)
    except Exception as error:
        _fail(error, json_output)
    if json_output:
        print_json({"ok": True, "exports": paths}, console)
    else:
        for kind, path in paths.items():
            console.print(f"[green]{kind}[/green]：{path}")


@app.command()
def doctor(
    directory: Annotated[
        Path,
        typer.Option("--directory", help="需要检查写权限的工作目录。"),
    ] = DEFAULT_DIRECTORY,
    online: Annotated[
        bool,
        typer.Option("--online/--no-online", help="是否在线检查 DeepSeek 模型。"),
    ] = True,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="输出机器可读 JSON。"),
    ] = False,
) -> None:
    """检查 Python、依赖、目录、密钥和 DeepSeek 模型。

    Args:
        directory: 用于验证写权限的目录。
        online: 是否调用 DeepSeek 模型列表接口。
        json_output: 是否输出机器可读 JSON。
    """
    report = DoctorService(get_settings()).run(directory, online=online)
    if json_output:
        print_json(report, console)
    else:
        render_doctor(report, console)
    if not report.ok:
        raise typer.Exit(ExitCode.ERROR)


def main() -> None:
    """启动 Typer 应用。"""
    app()


def _model_pipeline(
    project: Path,
    settings: AppSettings,
    json_output: bool,
) -> AnalysisPipeline:
    """构造不泄露密钥的 DeepSeek 分析流水线。"""
    if settings.deepseek_api_key is None:
        raise ValueError("未设置 DEEPSEEK_API_KEY，请先运行 doctor 检查环境。")
    analyzer = AgnoNarrativeAnalyzer(
        settings.deepseek_api_key,
        settings.request_timeout_seconds,
    )
    progress = None if json_output else _show_progress
    return AnalysisPipeline(ProjectStore(project), analyzer, progress)


def _show_progress(stage: str, message: str) -> None:
    """把阶段进度写入标准错误，避免污染 JSON 标准输出。"""
    error_console.print(f"[cyan]{stage}[/cyan] {message}")


def _render_run(result: PipelineRunResult, json_output: bool) -> None:
    """展示完整流水线成功结果。"""
    if json_output:
        print_json(
            {
                "ok": True,
                "project": str(Path(result.exports["json"]).parents[1]),
                "validation": result.validation.model_dump(mode="json"),
                "exports": result.exports,
            },
            console,
        )
        return
    render_validation(result.validation, console)
    for kind, path in result.exports.items():
        console.print(f"[green]{kind}[/green]：{path}")


def _fail(error: Exception, json_output: bool) -> NoReturn:
    """以稳定格式报告命令错误并终止当前命令。"""
    message = f"{type(error).__name__}: {error}"
    if json_output:
        print_json({"ok": False, "error": message}, console)
    else:
        error_console.print(f"[red]错误[/red]：{message}")
    raise typer.Exit(ExitCode.ERROR)
