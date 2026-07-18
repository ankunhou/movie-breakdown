"""在内存中原子应用带指纹与证据约束的人工叙事修正。"""

from __future__ import annotations

from movie_breakdown.application.manual_correction_targets import (
    _TargetResolutionError,
    _TargetResolver,
    _TextTarget,
)
from movie_breakdown.domain.export import NarrativeBreakdown
from movie_breakdown.domain.manual_correction import (
    CorrectionField,
    CorrectionReceipt,
    CorrectionSet,
    NarrativeCorrection,
)
from movie_breakdown.domain.scene_analysis import Evidence
from movie_breakdown.infrastructure.fingerprint import content_fingerprint


class CorrectionApplicationError(ValueError):
    """人工修正无法安全应用时的基础错误。"""


class StaleCorrectionSetError(CorrectionApplicationError):
    """修正集合绑定了其他剧本或其他分析版本。"""


class CorrectionConflictError(CorrectionApplicationError):
    """多条修正试图覆盖同一对象的同一字段。"""


class CorrectionTargetError(CorrectionApplicationError):
    """修正对象、字段、评审目标或旧值指纹无效。"""


class CorrectionEvidenceError(CorrectionApplicationError):
    """修正证据无法在当前剧本原文中定位。"""


class NarrativeCorrectionService:
    """校验并原子应用人工文本修正，不执行持久化或模型调用。"""

    def apply(
        self,
        breakdown: NarrativeBreakdown,
        correction_set: CorrectionSet,
    ) -> tuple[NarrativeBreakdown, CorrectionReceipt]:
        """在深拷贝上预检并应用整组修正。

        Args:
            breakdown: 当前未经修正的完整叙事拆解。
            correction_set: 与当前分析及专家评审绑定的修正集合。

        Returns:
            修正后的独立叙事拆解与可复核的应用回执。

        Raises:
            StaleCorrectionSetError: 剧本或基础分析指纹已经变化。
            CorrectionConflictError: 同一字段目标收到多条修正。
            CorrectionTargetError: 对象、评审目标、旧值或替换值无效。
            CorrectionEvidenceError: 任一证据无法在当前剧本定位。
        """
        self._validate_bindings(breakdown, correction_set)
        working = breakdown.model_copy(deep=True)
        resolver = _TargetResolver(working)
        targets: list[_TextTarget] = []
        seen: set[tuple[CorrectionField, str]] = set()
        for correction in correction_set.corrections:
            self._validate_evidence(working, correction)
            try:
                target = resolver.resolve(correction)
            except _TargetResolutionError as error:
                raise CorrectionTargetError(str(error)) from error
            if target.key in seen:
                raise CorrectionConflictError(
                    f"同一字段目标出现多条修正：{target.key[0].value}/{target.key[1]}"
                )
            seen.add(target.key)
            if content_fingerprint(target.value) != correction.expected_value_fingerprint:
                raise CorrectionTargetError(f"修正 {correction.id} 的旧值指纹已经过期。")
            targets.append(target)
        corrected = self._apply_to_copy(working, correction_set.corrections, targets)
        receipt = CorrectionReceipt(
            source_fingerprint=correction_set.source_fingerprint,
            base_analysis_fingerprint=correction_set.base_analysis_fingerprint,
            corrected_analysis_fingerprint=content_fingerprint(corrected),
            correction_set_fingerprint=content_fingerprint(correction_set),
            rubric_version=correction_set.rubric_version,
            review_answers_fingerprint=correction_set.review_answers_fingerprint,
            reviewer=correction_set.reviewer,
            applied_correction_ids=[item.id for item in correction_set.corrections],
            applied_count=len(correction_set.corrections),
        )
        return corrected, receipt

    @staticmethod
    def _apply_to_copy(
        working: NarrativeBreakdown,
        corrections: list[NarrativeCorrection],
        targets: list[_TextTarget],
    ) -> NarrativeBreakdown:
        """应用全部预检目标，并对最终嵌套模型执行完整 Schema 校验。"""
        try:
            for correction, target in zip(corrections, targets, strict=True):
                target.replace(correction.replacement)
            return NarrativeBreakdown.model_validate(working.model_dump(mode="python"))
        except (TypeError, ValueError) as error:
            raise CorrectionTargetError(f"修正后的字段值不满足叙事 Schema：{error}") from error

    @staticmethod
    def _validate_bindings(
        breakdown: NarrativeBreakdown,
        correction_set: CorrectionSet,
    ) -> None:
        """拒绝属于其他剧本或其他基础分析版本的修正集合。"""
        if correction_set.source_fingerprint != breakdown.screenplay.source_fingerprint:
            raise StaleCorrectionSetError("人工修正对应的剧本来源指纹已经过期。")
        if correction_set.base_analysis_fingerprint != content_fingerprint(breakdown):
            raise StaleCorrectionSetError("人工修正对应的基础分析指纹已经过期。")

    @staticmethod
    def _validate_evidence(
        breakdown: NarrativeBreakdown,
        correction: NarrativeCorrection,
    ) -> None:
        """要求每条修正的全部证据都能按场景、行号与摘录定位。"""
        scenes = {item.id: item for item in breakdown.screenplay.scenes}
        for evidence in correction.evidence:
            scene = scenes.get(evidence.scene_id)
            located = (
                None
                if scene is None
                else _located_excerpt(scene.text, scene.source_span.line_start, evidence)
            )
            if located is None:
                raise CorrectionEvidenceError(
                    f"修正 {correction.id} 的证据无法在场景 {evidence.scene_id} 中定位。"
                )


def _located_excerpt(text: str, scene_line_start: int, evidence: Evidence) -> str | None:
    """按全局行号提取证据文本，并要求与声明摘录逐字一致。"""
    start = evidence.source_span.line_start - scene_line_start
    end = evidence.source_span.line_end - scene_line_start + 1
    lines = text.splitlines()
    if start < 0 or end > len(lines) or start >= end:
        return None
    excerpt = "\n".join(lines[start:end]).strip()[:300].rstrip()
    return excerpt if excerpt and excerpt == evidence.excerpt else None
