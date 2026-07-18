"""全局分析证据规范化及可审计恢复。"""

from __future__ import annotations

from dataclasses import dataclass

from movie_breakdown.application.evidence import (
    EvidenceNormalizer,
    UnlocatableEvidenceError,
)
from movie_breakdown.application.structure_normalization import fill_unassigned_act_scenes
from movie_breakdown.application.validation import ValidationService
from movie_breakdown.domain.global_analysis import GlobalAnalysisResult
from movie_breakdown.domain.recovery import GlobalEvidenceRecoveryReport
from movie_breakdown.domain.scene_analysis import SceneAnalysisRecord
from movie_breakdown.domain.source import Screenplay
from movie_breakdown.infrastructure.fingerprint import content_fingerprint


@dataclass(frozen=True, slots=True)
class NormalizedGlobalAnalysis:
    """携带证据恢复审计信息的全局分析结果。

    Attributes:
        content: 已完成证据定位和幕场景补齐的全局结果。
        recovery: 绑定最终结果指纹的证据恢复报告。
    """

    content: GlobalAnalysisResult
    recovery: GlobalEvidenceRecoveryReport


def normalize_global_with_recovery(
    result: GlobalAnalysisResult,
    screenplay: Screenplay,
    records: list[SceneAnalysisRecord],
    cache_key: str,
) -> NormalizedGlobalAnalysis:
    """严格规范化新全局结果，并审计式删除无法定位的单条证据。

    只恢复专用的证据定位错误。恢复后的完整全局结果必须通过不要求人物小传
    和分级档案的确定性校验，引用或结构错误不会被掩盖。

    Args:
        result: 模型刚返回的严格全局分析。
        screenplay: 提供场景原文、顺序和来源指纹的剧本。
        records: 已成功的逐场分析记录。
        cache_key: 本次全局阶段的完整缓存键。

    Returns:
        规范化结果及严格绑定的恢复审计报告。

    Raises:
        ValueError: 恢复后仍存在引用、结构或覆盖错误。
        UnlocatableEvidenceError: 严格失败却没有可审计的坏证据可删除。
    """
    strict_error: UnlocatableEvidenceError | None = None
    normalizer = EvidenceNormalizer(screenplay.scenes)
    try:
        normalized = normalizer.normalize(result)
        dropped = []
    except UnlocatableEvidenceError as error:
        strict_error = error
        recovery_normalizer = EvidenceNormalizer(
            screenplay.scenes,
            drop_unlocatable=True,
        )
        normalized = recovery_normalizer.normalize(result)
        dropped = recovery_normalizer.dropped_evidence
        if not dropped:
            raise

    structure = fill_unassigned_act_scenes(normalized.structure, screenplay.scenes)
    normalized = normalized.model_copy(update={"structure": structure})
    if strict_error is not None:
        validation = ValidationService().validate(
            screenplay,
            records,
            normalized,
            require_biographies=False,
            require_dossiers=False,
        )
        if not validation.valid:
            details = "；".join(
                issue.message for issue in validation.issues if issue.severity.value == "error"
            )
            raise ValueError(f"删除坏证据后全局结果仍未通过确定性校验：{details}") from strict_error

    fingerprint = content_fingerprint(normalized)
    recovery = GlobalEvidenceRecoveryReport(
        source_fingerprint=screenplay.source_fingerprint,
        cache_key=cache_key,
        recovered=strict_error is not None,
        initial_error=str(strict_error) if strict_error is not None else None,
        dropped_evidence=dropped,
        result_fingerprint=fingerprint,
    )
    return NormalizedGlobalAnalysis(content=normalized, recovery=recovery)
