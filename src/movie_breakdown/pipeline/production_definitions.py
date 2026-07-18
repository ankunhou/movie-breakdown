"""独立制片流水线的阶段名称、顺序和版本。"""

from __future__ import annotations

from movie_breakdown.domain.base import StageStatus
from movie_breakdown.domain.run import RunManifest, StageRecord
from movie_breakdown.pipeline.definitions import StageSpec

PRODUCTION_STAGES = (
    StageSpec("production_scene_analysis", "1.0"),
    StageSpec("production_catalog", "1.0"),
    StageSpec("production_validation", "1.0"),
    StageSpec("production_export", "1.0"),
)


def production_stage_versions() -> dict[str, str]:
    """返回制片 manifest 使用的独立阶段版本。

    Returns:
        以制片阶段名为键、版本为值的映射。
    """
    return {stage.name: stage.version for stage in PRODUCTION_STAGES}


def get_production_stage(name: str) -> StageSpec:
    """按名称查找制片阶段定义。

    Args:
        name: 制片阶段的稳定名称。

    Returns:
        对应的制片阶段定义。

    Raises:
        StopIteration: 阶段名称没有注册。
    """
    return next(stage for stage in PRODUCTION_STAGES if stage.name == name)


def reconcile_production_manifest_stages(manifest: RunManifest) -> bool:
    """为旧制片清单补入新增阶段且不覆盖已有状态。

    Args:
        manifest: 从 `production/manifest.json` 读取的运行清单。

    Returns:
        是否向清单增加了至少一个阶段。
    """
    changed = False
    for spec in PRODUCTION_STAGES:
        if spec.name in manifest.stages:
            continue
        manifest.stages[spec.name] = StageRecord(
            name=spec.name,
            version=spec.version,
            status=StageStatus.PENDING,
        )
        changed = True
    return changed
