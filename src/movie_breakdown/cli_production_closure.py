"""制片规划、专家复核、人工修正与分级封版的纯本地 CLI。"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, NoReturn

import typer
from rich.console import Console

from movie_breakdown.application.production_closure import ProductionClosureService
from movie_breakdown.cli_render import print_json
from movie_breakdown.cli_types import ExitCode
from movie_breakdown.domain.production_correction import ProductionCorrectionSet
from movie_breakdown.domain.production_release import ProductionReleaseProfile
from movie_breakdown.domain.production_review import ProductionReviewAnswers
from movie_breakdown.infrastructure.production_storage import ProductionStore
from movie_breakdown.infrastructure.storage import ProjectStore

console = Console()
error_console = Console(stderr=True)


def plan_project(
    project: Annotated[Path, typer.Argument(help="已完成基础制片拆解的主项目目录。")],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="输出机器可读 JSON。"),
    ] = False,
) -> None:
    """纯本地派生拍摄单元、实体、数量与安全规划。

    Args:
        project: 已有制片拆解产物的主项目目录。
        json_output: 是否输出机器可读 JSON。
    """
    try:
        result = _service(project).plan()
    except Exception as error:
        _fail(error, json_output)
    payload = {
        "ok": result.validation.draft_valid,
        "plan_fingerprint": result.validation.plan_fingerprint,
        "validation": result.validation.model_dump(mode="json"),
        "exports": result.exports,
    }
    if json_output:
        print_json(payload, console)
    else:
        _render_validation(result.validation, result.exports)
    if not result.validation.draft_valid:
        raise typer.Exit(ExitCode.VALIDATION_FAILED)


def review_project(
    project: Annotated[Path, typer.Argument(help="已生成制片规划的主项目目录。")],
    answers_path: Annotated[
        Path | None,
        typer.Option("--answers", help="可选的专家答案 JSON；省略时生成空白模板。"),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="输出机器可读 JSON。"),
    ] = False,
) -> None:
    """生成强制专家复核表，或导入与当前规划匹配的答案。

    Args:
        project: 已生成制片规划的主项目目录。
        answers_path: 可选的严格专家答案 JSON 路径。
        json_output: 是否输出机器可读 JSON。
    """
    try:
        answers = _read_model(answers_path, ProductionReviewAnswers) if answers_path else None
        result = _service(project).review(answers)
    except Exception as error:
        _fail(error, json_output)
    payload = {
        "ok": True,
        "report": result.report.model_dump(mode="json"),
        "answers": result.answers.model_dump(mode="json"),
        "paths": result.paths,
    }
    if json_output:
        print_json(payload, console)
    else:
        console.print(
            f"制片专家复核：{result.report.reviewed_count}/"
            f"{result.report.target_count}；完成：{'是' if result.report.complete else '否'}"
        )
        _render_paths(result.paths)


def correct_project(
    project: Annotated[Path, typer.Argument(help="已生成基础制片规划的主项目目录。")],
    corrections_path: Annotated[Path, typer.Argument(help="结构化累计修正集 JSON。")],
    answers_path: Annotated[
        Path,
        typer.Option("--answers", help="产生该修正集的专家答案 JSON。"),
    ],
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="只在内存中校验，不写入任何闭环产物。"),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="输出机器可读 JSON。"),
    ] = False,
) -> None:
    """严格导入专家答案与修正集，预演或原子激活新代际。

    Args:
        project: 已生成基础制片规划的主项目目录。
        corrections_path: 通过 Pydantic 严格校验的修正集 JSON。
        answers_path: 通过 Pydantic 严格校验的专家答案 JSON。
        dry_run: 是否仅预演且保持零写入。
        json_output: 是否输出机器可读 JSON。
    """
    try:
        correction_set = _read_model(corrections_path, ProductionCorrectionSet)
        answers = _read_model(answers_path, ProductionReviewAnswers)
        result = _service(project).correct(correction_set, answers, dry_run=dry_run)
    except Exception as error:
        _fail(error, json_output)
    payload = {
        "ok": True,
        "dry_run": dry_run,
        "validation": result.validation.model_dump(mode="json"),
        "receipt": result.receipt.model_dump(mode="json"),
        "generation_id": result.generation_id,
        "exports": result.exports,
    }
    if json_output:
        print_json(payload, console)
    else:
        mode = "预演通过" if dry_run else "修正已激活"
        console.print(f"{mode}；应用操作：{result.receipt.applied_count}")
        _render_paths(result.exports)


def finalize_project(
    project: Annotated[Path, typer.Argument(help="已完成专家复核的主项目目录。")],
    profile: Annotated[
        ProductionReleaseProfile,
        typer.Option("--profile", help="封版等级：evaluation 或 professional。"),
    ] = ...,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="输出机器可读 JSON。"),
    ] = False,
) -> None:
    """执行评测版或专业稳定版门禁，并始终保存封版报告。

    Args:
        project: 已完成专家复核的主项目目录。
        profile: 请求的评测版或专业稳定版等级。
        json_output: 是否输出机器可读 JSON。
    """
    try:
        result = _service(project).finalize(profile)
    except Exception as error:
        _fail(error, json_output)
    payload = {
        "ok": result.report.releasable,
        "report": result.report.model_dump(mode="json"),
        "release_id": result.release_id,
        "exports": result.exports,
    }
    if json_output:
        print_json(payload, console)
    else:
        state = "通过" if result.report.releasable else "已阻断"
        console.print(f"制片封版：{state}；等级：{result.report.profile.value}")
        _render_paths(result.exports)
    if not result.report.releasable:
        raise typer.Exit(ExitCode.VALIDATION_FAILED)


def _service(project: Path) -> ProductionClosureService:
    """构造不读取密钥且不会调用模型的制片闭环服务。"""
    return ProductionClosureService(ProductionStore(ProjectStore(project)))


def _read_model(path: Path, model_type):
    """从 UTF-8 JSON 严格构造指定 Pydantic 模型。"""
    return model_type.model_validate_json(path.read_text(encoding="utf-8"))


def _render_validation(validation, exports: dict[str, str]) -> None:
    """展示三级制片准备度与规划导出路径。"""
    console.print(
        f"制片规划：草稿={'通过' if validation.draft_valid else '阻断'}；"
        f"目录={'就绪' if validation.catalog_ready else '未就绪'}；"
        f"拍摄={'就绪' if validation.shoot_ready else '未就绪'}"
    )
    _render_paths(exports)


def _render_paths(paths: dict[str, str]) -> None:
    """展示一组稳定名称与绝对路径。"""
    for name, path in paths.items():
        console.print(f"[green]{name}[/green]：{path}")


def _fail(error: Exception, json_output: bool) -> NoReturn:
    """以稳定中文格式报告本地闭环错误并终止命令。"""
    message = f"{type(error).__name__}: {error}"
    if json_output:
        print_json({"ok": False, "error": message}, console)
    else:
        error_console.print(f"[red]错误[/red]：{message}")
    raise typer.Exit(ExitCode.ERROR)
