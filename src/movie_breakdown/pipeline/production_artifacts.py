"""独立制片阶段的追溯产物构造器。"""

from __future__ import annotations

from pydantic import BaseModel

from movie_breakdown.domain.run import Artifact, ArtifactMetadata
from movie_breakdown.domain.scene_analysis import TokenUsage
from movie_breakdown.infrastructure.fingerprint import content_fingerprint, schema_fingerprint
from movie_breakdown.pipeline.production_definitions import get_production_stage


def make_production_artifact[T: BaseModel](
    *,
    stage_name: str,
    cache_key: str,
    data: T,
    source_fingerprint: str,
    upstream_fingerprints: list[str],
    prompt_fingerprint: str | None = None,
    model: str | None = None,
    model_parameters: dict[str, str | int | bool] | None = None,
    usage: TokenUsage | None = None,
) -> Artifact[T]:
    """为制片业务数据附加独立阶段与缓存元数据。

    Args:
        stage_name: 生成产物的制片阶段名。
        cache_key: 当前输入和配置的完整缓存键。
        data: 通过严格校验的制片业务数据。
        source_fingerprint: 原始剧本内容指纹。
        upstream_fingerprints: 直接共享或制片上游指纹。
        prompt_fingerprint: 模型 Prompt 指纹；本地阶段可省略。
        model: 模型名称；本地阶段可省略。
        model_parameters: 不含密钥的模型关键参数。
        usage: 当前阶段累计 token 用量。

    Returns:
        带完整追溯元数据的制片产物。
    """
    stage = get_production_stage(stage_name)
    metadata = ArtifactMetadata(
        stage=stage_name,
        stage_version=stage.version,
        cache_key=cache_key,
        artifact_fingerprint=content_fingerprint(data),
        source_fingerprint=source_fingerprint,
        upstream_fingerprints=upstream_fingerprints,
        prompt_fingerprint=prompt_fingerprint,
        schema_fingerprint=schema_fingerprint(type(data)),
        model=model,
        model_parameters=model_parameters or {},
        usage=usage or TokenUsage(),
    )
    return Artifact[T](metadata=metadata, data=data)
