"""带完整追溯元数据的流水线产物构造器。"""

from __future__ import annotations

from pydantic import BaseModel

from movie_breakdown.domain.run import Artifact, ArtifactMetadata
from movie_breakdown.domain.scene_analysis import TokenUsage
from movie_breakdown.infrastructure.fingerprint import content_fingerprint, schema_fingerprint
from movie_breakdown.pipeline.definitions import get_stage


def make_artifact[T: BaseModel](
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
    """为业务数据附加缓存、Schema、模型和上游指纹。

    Args:
        stage_name: 生成产物的流水线阶段名称。
        cache_key: 当前输入和配置计算出的缓存键。
        data: 通过 Pydantic 校验的业务数据。
        source_fingerprint: 原始剧本内容指纹。
        upstream_fingerprints: 直接上游产物的内容指纹。
        prompt_fingerprint: 模型 Prompt 指纹；本地阶段可省略。
        model: 模型名称；本地阶段可省略。
        model_parameters: 会影响结果的模型参数。
        usage: 生成产物消耗的 token 用量。

    Returns:
        包含完整追溯元数据的泛型产物。
    """
    spec = get_stage(stage_name)
    metadata = ArtifactMetadata(
        stage=stage_name,
        stage_version=spec.version,
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
