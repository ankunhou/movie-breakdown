"""本地制片命令使用的逐场产物信任门和状态失效工具。"""

from __future__ import annotations

from dataclasses import dataclass

from movie_breakdown.domain.base import StageStatus
from movie_breakdown.domain.production_run import ProductionProject
from movie_breakdown.domain.production_scene import SceneProductionRecord
from movie_breakdown.domain.run import Artifact, RunManifest
from movie_breakdown.domain.source import Screenplay
from movie_breakdown.infrastructure.fingerprint import content_fingerprint
from movie_breakdown.infrastructure.llm.production_prompts import (
    production_prompt_fingerprint,
)
from movie_breakdown.pipeline.production_definitions import get_production_stage
from movie_breakdown.pipeline.production_scene_cache import (
    production_scene_cache_key,
    production_scene_stage_cache_key,
)
from movie_breakdown.pipeline.runtime import StageRuntime


@dataclass(frozen=True, slots=True)
class ProductionLocalRecordState:
    """本地命令可信使用的有序逐场记录状态。

    Attributes:
        records: 按共享剧本场序排列、过期项已显式标记的记录。
        trusted: 记录、缓存键和逐场阶段清单是否构成完整可追溯链。
        records_fingerprint: 当前有序原始记录指纹。
        reason: 不可信时供清单和本地校验展示的原因。
    """

    records: list[SceneProductionRecord]
    trusted: bool
    records_fingerprint: str
    reason: str | None = None


def inspect_production_records(
    project: ProductionProject,
    screenplay: Artifact[Screenplay],
    records: list[SceneProductionRecord],
    manifest: RunManifest,
) -> ProductionLocalRecordState:
    """核对逐场当前缓存键、完整性和阶段追溯链。

    Args:
        project: 当前独立制片项目。
        screenplay: 已通过源文件指纹校验的共享场景产物。
        records: 从制片 JSONL 严格加载的逐场记录。
        manifest: 当前独立制片运行清单。

    Returns:
        排序后的记录、可信标记、指纹和失败原因。
    """
    scenes = screenplay.data.scenes
    order = {scene.id: index for index, scene in enumerate(scenes)}
    ordered = sorted(records, key=lambda item: order.get(item.scene_id, len(order)))
    expected = {
        scene.id: production_scene_cache_key(
            scene,
            project,
            production_prompt_fingerprint(),
        )
        for scene in scenes
    }
    stale_ids = {
        record.scene_id
        for record in ordered
        if record.scene_id in expected and record.cache_key != expected[record.scene_id]
    }
    visible = [
        _mark_record_stale(record, "逐场记录缓存键与当前制片契约不一致。")
        if record.scene_id in stale_ids
        else record
        for record in ordered
    ]
    raw_fingerprint = content_fingerprint(ordered)
    actual_ids = [record.scene_id for record in ordered]
    structurally_complete = (
        len(actual_ids) == len(set(actual_ids))
        and set(actual_ids) == set(expected)
        and all(
            record.status == StageStatus.SUCCESS and record.analysis is not None
            for record in ordered
        )
    )
    if not structurally_complete:
        return ProductionLocalRecordState(
            visible,
            False,
            raw_fingerprint,
            "制片逐场记录不完整、存在失败或引用未知场景。",
        )
    if stale_ids:
        return ProductionLocalRecordState(
            visible,
            False,
            raw_fingerprint,
            "制片逐场记录包含过期缓存键。",
        )
    stage = manifest.stages["production_scene_analysis"]
    expected_stage_key = production_scene_stage_cache_key(
        screenplay.metadata.artifact_fingerprint,
        scenes,
        expected,
    )
    current_version = get_production_stage("production_scene_analysis").version
    stage_trusted = (
        stage.status == StageStatus.SUCCESS
        and stage.version == current_version
        and stage.cache_key == expected_stage_key
        and stage.artifact_fingerprint == raw_fingerprint
    )
    if stage_trusted:
        return ProductionLocalRecordState(visible, True, raw_fingerprint)
    reason = "制片逐场阶段清单与当前记录或阶段契约不一致。"
    return ProductionLocalRecordState(
        [_mark_record_stale(record, reason) for record in visible],
        False,
        raw_fingerprint,
        reason,
    )


def mark_successful_stages_stale(
    runtime: StageRuntime,
    stage_names: tuple[str, ...],
    reason: str,
) -> None:
    """把上游不再可信时仍显示成功的阶段标为过期。

    Args:
        runtime: 绑定制片清单和独立存储的运行时。
        stage_names: 需要失效的阶段名称。
        reason: 可供 CLI 展示的简明原因。
    """
    changed = False
    for name in stage_names:
        record = runtime.manifest.stages[name]
        if record.status != StageStatus.SUCCESS:
            continue
        record.status = StageStatus.STALE
        record.error = reason
        changed = True
    if changed:
        runtime.store.save_manifest(runtime.manifest)


def _mark_record_stale(
    record: SceneProductionRecord,
    reason: str,
) -> SceneProductionRecord:
    """返回不改写 JSONL 的过期记录视图。"""
    return record.model_copy(update={"status": StageStatus.STALE, "error": reason})
