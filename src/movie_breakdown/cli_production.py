"""独立制片元素拆解的 Typer 子命令。"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, NoReturn

import typer
from rich.console import Console

from movie_breakdown.application.production_pipeline import (
    ProductionPipeline,
    ProductionPipelineRunResult,
)
from movie_breakdown.cli_render import (
    print_json,
    render_production_validation,
    render_status,
)
from movie_breakdown.cli_types import ExitCode, ProductionExportChoice
from movie_breakdown.config import AppSettings, get_settings
from movie_breakdown.infrastructure.llm.agno_production_analyzer import (
    AgnoProductionAnalyzer,
)
from movie_breakdown.infrastructure.production_storage import ProductionStore
from movie_breakdown.infrastructure.storage import ProjectStore

production_app = typer.Typer(
    help="基于共享场景独立拆解演员、群演、地点、服化道、车辆、特效与制片复杂度。",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
error_console = Console(stderr=True)


@production_app.command()
def analyze(
    project: Annotated[Path, typer.Argument(help="已有且已完成场景切分的主项目目录。")],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="输出机器可读 JSON。"),
    ] = False,
) -> None:
    """首次创建独立制片作用域并执行完整拆解。

    Args:
        project: 已包含共享 `artifacts/scenes.json` 的主项目目录。
        json_output: 是否输出机器可读 JSON。
    """
    try:
        pipeline = _model_pipeline(project, get_settings(), json_output)
        pipeline.initialize()
        result = pipeline.run()
    except Exception as error:
        _fail(error, json_output)
    _render_run(result, json_output)


@production_app.command()
def resume(
    project: Annotated[Path, typer.Argument(help="已初始化制片拆解的主项目目录。")],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="输出机器可读 JSON。"),
    ] = False,
) -> None:
    """仅重试失败、未完成或过期的制片阶段。

    Args:
        project: 已初始化制片拆解的主项目目录。
        json_output: 是否输出机器可读 JSON。
    """
    try:
        result = _model_pipeline(project, get_settings(), json_output).run()
    except Exception as error:
        _fail(error, json_output)
    _render_run(result, json_output)


@production_app.command(name="status")
def show_status(
    project: Annotated[Path, typer.Argument(help="已初始化制片拆解的主项目目录。")],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="输出机器可读 JSON。"),
    ] = False,
) -> None:
    """只读展示四个独立制片阶段的状态和用量。

    Args:
        project: 已初始化制片拆解的主项目目录。
        json_output: 是否输出机器可读 JSON。
    """
    try:
        manifest = _local_pipeline(project).status()
    except Exception as error:
        _fail(error, json_output)
    if json_output:
        print_json(manifest, console)
    else:
        render_status(manifest, console, "制片元素拆解阶段状态")


@production_app.command(name="validate")
def validate_project(
    project: Annotated[Path, typer.Argument(help="已初始化制片拆解的主项目目录。")],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="输出机器可读 JSON。"),
    ] = False,
) -> None:
    """不读取密钥、不调用模型地重新校验制片产物。

    Args:
        project: 已初始化制片拆解的主项目目录。
        json_output: 是否输出机器可读 JSON。
    """
    try:
        report = _local_pipeline(project).validate_only()
    except Exception as error:
        _fail(error, json_output)
    if json_output:
        print_json(report, console)
    else:
        render_production_validation(report, console)
    if not report.valid:
        raise typer.Exit(ExitCode.VALIDATION_FAILED)


@production_app.command(name="export")
def export_project(
    project: Annotated[Path, typer.Argument(help="已初始化制片拆解的主项目目录。")],
    export_format: Annotated[
        ProductionExportChoice,
        typer.Option("--format", help="制片报告格式。"),
    ] = ProductionExportChoice.MARKDOWN,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="输出机器可读 JSON。"),
    ] = False,
) -> None:
    """从已验证产物重新生成指定格式的制片报告。

    Args:
        project: 已初始化制片拆解的主项目目录。
        export_format: Markdown、JSON、双 CSV 或全部格式。
        json_output: 是否输出机器可读 JSON。
    """
    try:
        paths = _local_pipeline(project).export_only(export_format.value)
    except Exception as error:
        _fail(error, json_output)
    if json_output:
        print_json({"ok": True, "exports": paths}, console)
    else:
        for kind, path in paths.items():
            console.print(f"[green]{kind}[/green]：{path}")


def _model_pipeline(
    project: Path,
    settings: AppSettings,
    json_output: bool,
) -> ProductionPipeline:
    """构造只用于 analyze 与 resume 的 DeepSeek 制片流水线。"""
    if settings.deepseek_api_key is None:
        raise ValueError("未设置 DEEPSEEK_API_KEY，请先运行 doctor 检查环境。")
    analyzer = AgnoProductionAnalyzer(
        settings.deepseek_api_key,
        settings.request_timeout_seconds,
    )
    progress = None if json_output else _show_progress
    return ProductionPipeline(_production_store(project), analyzer, progress)


def _local_pipeline(project: Path) -> ProductionPipeline:
    """构造不读取密钥且不会调用模型的制片流水线。"""
    return ProductionPipeline(_production_store(project))


def _production_store(project: Path) -> ProductionStore:
    """把主项目存储包装为隔离制片存储。"""
    return ProductionStore(ProjectStore(project))


def _show_progress(stage: str, message: str) -> None:
    """把制片进度写入标准错误，避免污染 JSON。"""
    error_console.print(f"[cyan]{stage}[/cyan] {message}")


def _render_run(result: ProductionPipelineRunResult, json_output: bool) -> None:
    """展示完整制片流水线成功结果。"""
    if json_output:
        print_json(
            {
                "ok": True,
                "project": str(Path(result.exports["json"]).parents[2]),
                "validation": result.validation.model_dump(mode="json"),
                "exports": result.exports,
            },
            console,
        )
        return
    render_production_validation(result.validation, console)
    for kind, path in result.exports.items():
        console.print(f"[green]{kind}[/green]：{path}")


def _fail(error: Exception, json_output: bool) -> NoReturn:
    """以稳定格式报告制片命令错误并终止当前命令。"""
    message = f"{type(error).__name__}: {error}"
    if json_output:
        print_json({"ok": False, "error": message}, console)
    else:
        error_console.print(f"[red]错误[/red]：{message}")
    raise typer.Exit(ExitCode.ERROR)


from movie_breakdown.cli_production_closure import (  # noqa: E402
    correct_project,
    finalize_project,
    plan_project,
    review_project,
)

production_app.command(name="plan")(plan_project)
production_app.command(name="review")(review_project)
production_app.command(name="correct")(correct_project)
production_app.command(name="finalize")(finalize_project)
