"""流水线阶段名称、顺序和版本。"""

from __future__ import annotations

from dataclasses import dataclass

from movie_breakdown.domain.base import StageStatus
from movie_breakdown.domain.run import RunManifest, StageRecord


@dataclass(frozen=True, slots=True)
class StageSpec:
    """参与缓存判定的流水线阶段定义。"""

    name: str
    version: str


STAGES = (
    StageSpec("normalize", "1.1"),
    StageSpec("scenes", "1.1"),
    StageSpec("scene_analysis", "1.1"),
    StageSpec("global_analysis", "1.2"),
    StageSpec("character_dossiers", "1.0"),
    StageSpec("character_biographies", "1.1"),
    StageSpec("validation", "1.2"),
    StageSpec("manual_corrections", "1.0"),
    StageSpec("export", "1.3"),
)


def stage_versions() -> dict[str, str]:
    """返回创建运行清单所需的阶段版本映射。

    Returns:
        以阶段名称为键、阶段版本为值的映射。
    """
    return {stage.name: stage.version for stage in STAGES}


def get_stage(name: str) -> StageSpec:
    """按名称查找阶段定义。

    Args:
        name: 流水线阶段的稳定名称。

    Returns:
        对应的阶段定义。

    Raises:
        StopIteration: 阶段名称未注册。
    """
    return next(stage for stage in STAGES if stage.name == name)


def reconcile_manifest_stages(manifest: RunManifest) -> bool:
    """为旧项目清单补入后来新增的流水线阶段。

    只增加缺失阶段，不覆盖已有阶段的版本、状态或缓存信息。各阶段在实际运行或
    命中缓存时自行刷新版本，避免项目升级时误报已经执行过新能力。

    Args:
        manifest: 从项目目录读取的运行清单。

    Returns:
        是否向清单增加了至少一个阶段。
    """
    changed = False
    for spec in STAGES:
        if spec.name in manifest.stages:
            continue
        manifest.stages[spec.name] = StageRecord(
            name=spec.name,
            version=spec.version,
            status=StageStatus.PENDING,
        )
        changed = True
    return changed
