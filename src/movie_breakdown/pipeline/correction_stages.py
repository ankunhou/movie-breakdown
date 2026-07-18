"""缓存并持久化当前项目激活的人工叙事修正。"""

from __future__ import annotations

from movie_breakdown.application.correction_workflow import ManualCorrectionWorkflow
from movie_breakdown.domain.export import NarrativeBreakdown
from movie_breakdown.infrastructure.fingerprint import (
    cache_fingerprint,
    content_fingerprint,
    schema_fingerprint,
)
from movie_breakdown.pipeline.artifacts import make_artifact
from movie_breakdown.pipeline.definitions import get_stage
from movie_breakdown.pipeline.runtime import PipelineStageError, StageRuntime

STAGE_NAME = "manual_corrections"
ARTIFACT_NAME = "manual_corrections"


class ManualCorrectionStageService:
    """重放激活修正并把修正后聚合纳入流水线缓存。"""

    def __init__(self, runtime: StageRuntime) -> None:
        """创建人工修正阶段服务。

        Args:
            runtime: 当前项目的共享阶段运行时。
        """
        self.runtime = runtime

    def apply(self, base: NarrativeBreakdown) -> NarrativeBreakdown:
        """应用当前激活修正，或确定性通过没有修正的基础聚合。

        Args:
            base: 已通过确定性校验且尚未应用人工修正的叙事聚合。

        Returns:
            带可选修正回执的正式叙事聚合。

        Raises:
            PipelineStageError: 激活修正过期、无效或无法持久化。
        """
        try:
            corrected, receipt = ManualCorrectionWorkflow(self.runtime.store).apply_active(base)
            content = corrected.model_copy(update={"correction_receipt": receipt})
            base_fingerprint = content_fingerprint(base)
            correction_fingerprint = content_fingerprint(receipt) if receipt else "none"
            cache_key = cache_fingerprint(
                base_fingerprint,
                correction_fingerprint,
                get_stage(STAGE_NAME).version,
                schema_fingerprint(NarrativeBreakdown),
            )
            cached = self.runtime.load_cached(
                STAGE_NAME,
                ARTIFACT_NAME,
                NarrativeBreakdown,
                cache_key,
            )
            if cached is not None:
                return cached.data
            count = receipt.applied_count if receipt else 0
            self.runtime.start(STAGE_NAME, cache_key, f"应用 {count} 条人工叙事修正")
            artifact = make_artifact(
                stage_name=STAGE_NAME,
                cache_key=cache_key,
                data=content,
                source_fingerprint=base.screenplay.source_fingerprint,
                upstream_fingerprints=[base_fingerprint],
            )
            self.runtime.store.write_artifact(ARTIFACT_NAME, artifact)
            fingerprint = artifact.metadata.artifact_fingerprint
            self.runtime.success(STAGE_NAME, cache_key, fingerprint)
            return content
        except PipelineStageError:
            raise
        except Exception as error:
            self.runtime.fail(STAGE_NAME, error)
            raise PipelineStageError(f"人工叙事修正阶段失败：{error}") from error
