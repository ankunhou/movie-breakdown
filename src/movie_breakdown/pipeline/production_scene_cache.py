"""制片逐场记录和阶段的统一缓存键计算。"""

from __future__ import annotations

from movie_breakdown.domain.production_run import ProductionProject
from movie_breakdown.domain.production_scene import SceneProductionAnalysis
from movie_breakdown.domain.source import Scene
from movie_breakdown.infrastructure.fingerprint import (
    cache_fingerprint,
    content_fingerprint,
    schema_fingerprint,
)
from movie_breakdown.pipeline.production_definitions import get_production_stage


def production_scene_cache_key(
    scene: Scene,
    project: ProductionProject,
    prompt_fingerprint: str,
) -> str:
    """计算单场输入对应的制片记录缓存键。

    Args:
        scene: 共享剧本中的一个完整场景。
        project: 固化了制片契约和模型参数的独立项目。
        prompt_fingerprint: 实际产生该记录的 Prompt 指纹。

    Returns:
        覆盖场景内容、阶段、Prompt、Schema、契约和模型参数的指纹。
    """
    config = project.config
    return cache_fingerprint(
        content_fingerprint(
            {
                "id": scene.id,
                "heading": scene.heading,
                "text": scene.text,
                "source_span": scene.source_span,
            }
        ),
        get_production_stage("production_scene_analysis").version,
        prompt_fingerprint,
        schema_fingerprint(SceneProductionAnalysis),
        config.contract_version,
        config.model,
        config.thinking_enabled,
        config.reasoning_effort,
    )


def production_scene_stage_cache_key(
    screenplay_artifact_fingerprint: str,
    scenes: list[Scene],
    scene_cache_keys: dict[str, str],
) -> str:
    """计算全部逐场记录对应的阶段缓存键。

    Args:
        screenplay_artifact_fingerprint: 共享场景产物指纹。
        scenes: 按剧本顺序排列的场景。
        scene_cache_keys: 场景 ID 到当前单场缓存键的映射。

    Returns:
        同时绑定共享场景和有序单场缓存键的阶段指纹。
    """
    return cache_fingerprint(
        screenplay_artifact_fingerprint,
        get_production_stage("production_scene_analysis").version,
        [scene_cache_keys[scene.id] for scene in scenes],
    )
