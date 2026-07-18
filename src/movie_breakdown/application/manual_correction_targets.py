"""人工叙事修正的内部字段目标解析器。"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from movie_breakdown.domain.export import NarrativeBreakdown
from movie_breakdown.domain.manual_correction import CorrectionField, NarrativeCorrection


class _TargetResolutionError(ValueError):
    """修正对象、字段或评审目标无法唯一解析。"""


@dataclass(frozen=True, slots=True)
class _TextTarget:
    """一个已经解析到具体模型属性或字符串列表项的修正目标。"""

    key: tuple[CorrectionField, str]
    review_target_id: str
    owner: BaseModel | list[str]
    attribute: str | None
    index: int | None
    value: str | None

    def replace(self, replacement: str | None) -> None:
        """把预检通过的值写入工作副本。

        Args:
            replacement: 待写入的文本或允许字段使用的空值。
        """
        if self.attribute is not None:
            setattr(self.owner, self.attribute, replacement)
            return
        if not isinstance(self.owner, list) or self.index is None or replacement is None:
            raise _TargetResolutionError("该字段不允许使用空值替换。")
        self.owner[self.index] = replacement


class _TargetResolver:
    """为一份工作副本建立稳定对象索引并解析可写文本字段。"""

    def __init__(self, breakdown: NarrativeBreakdown) -> None:
        """建立全部受支持叙事对象的 ID 索引。

        Args:
            breakdown: 只用于本次修正的深拷贝叙事产物。
        """
        self.breakdown = breakdown
        biographies = {item.character_id: item for item in breakdown.biographies.biographies}
        self.simple: dict[CorrectionField, tuple[dict[str, BaseModel], str, str]] = {
            CorrectionField.SCENE_SUMMARY: (
                {item.scene_id: item for item in breakdown.scene_analyses},
                "summary",
                "scene-summary",
            ),
            CorrectionField.EVENT_SUMMARY: (
                {item.id: item for item in breakdown.events.events},
                "summary",
                "event",
            ),
            CorrectionField.ACT_SUMMARY: (
                {str(item.act): item for item in breakdown.structure.acts},
                "summary",
                "act",
            ),
            CorrectionField.ACT_TURNING_POINT: (
                {str(item.act): item for item in breakdown.structure.acts},
                "turning_point",
                "act",
            ),
            CorrectionField.BEAT_SUMMARY: (
                {item.id: item for item in breakdown.structure.beats},
                "summary",
                "beat",
            ),
            CorrectionField.PLOT_SUMMARY: (
                {item.id: item for item in breakdown.structure.plot_threads},
                "summary",
                "plot",
            ),
            CorrectionField.FORESHADOW_DESCRIPTION: (
                {item.id: item for item in breakdown.structure.foreshadowing},
                "description",
                "foreshadow",
            ),
            CorrectionField.ARC_INITIAL_STATE: (
                {item.character_id: item for item in breakdown.relationships.character_arcs},
                "initial_state",
                "arc",
            ),
            CorrectionField.ARC_DESIRE: (
                {item.character_id: item for item in breakdown.relationships.character_arcs},
                "desire",
                "arc",
            ),
            CorrectionField.ARC_NEED: (
                {item.character_id: item for item in breakdown.relationships.character_arcs},
                "need",
                "arc",
            ),
            CorrectionField.ARC_FINAL_STATE: (
                {item.character_id: item for item in breakdown.relationships.character_arcs},
                "final_state",
                "arc",
            ),
            CorrectionField.RELATION_DEVELOPMENT: (
                {item.id: item for item in breakdown.relationships.relationships},
                "development",
                "relation",
            ),
            CorrectionField.BIOGRAPHY_SUMMARY: (
                {key: item.summary for key, item in biographies.items()},
                "statement",
                "biography",
            ),
        }
        self.claims = {
            f"{biography.character_id}:{claim.id}": (claim.id, claim)
            for biography in biographies.values()
            for claim in biography.claims
        }

    def resolve(self, correction: NarrativeCorrection) -> _TextTarget:
        """把修正字段和对象 ID 解析为工作副本中的唯一文本目标。

        Args:
            correction: 待解析的单条人工修正。

        Returns:
            携带当前旧值和稳定冲突键的内部文本目标。

        Raises:
            _TargetResolutionError: 字段、对象或评审目标不匹配。
        """
        if correction.field in {CorrectionField.THEME, CorrectionField.MOTIF}:
            target = self._resolve_interpretation(correction)
        elif correction.field in {
            CorrectionField.BIOGRAPHY_CLAIM_STATEMENT,
            CorrectionField.BIOGRAPHY_CLAIM_RATIONALE,
        }:
            target = self._resolve_claim(correction)
        else:
            target = self._resolve_simple(correction)
        if correction.review_target_id != target.review_target_id:
            raise _TargetResolutionError(
                f"修正 {correction.id} 的 review_target_id 与字段目标不匹配。"
            )
        return target

    def _resolve_simple(self, correction: NarrativeCorrection) -> _TextTarget:
        """解析具有直接 ID、属性和评审前缀的普通字段。"""
        config = self.simple.get(correction.field)
        if config is None:
            raise _TargetResolutionError(f"不支持的修正字段：{correction.field.value}")
        objects, attribute, prefix = config
        owner = objects.get(correction.object_id)
        if owner is None:
            raise _TargetResolutionError(
                f"修正 {correction.id} 引用的对象不存在：{correction.object_id}"
            )
        return _TextTarget(
            key=(correction.field, correction.object_id),
            review_target_id=f"{prefix}:{correction.object_id}",
            owner=owner,
            attribute=attribute,
            index=None,
            value=getattr(owner, attribute),
        )

    def _resolve_claim(self, correction: NarrativeCorrection) -> _TextTarget:
        """借助人物与声明复合 ID 消除不同小传中的声明歧义。"""
        prefix = "biography-claim:"
        reference = correction.review_target_id.removeprefix(prefix)
        resolved = self.claims.get(reference)
        if resolved is None or not correction.review_target_id.startswith(prefix):
            raise _TargetResolutionError(f"修正 {correction.id} 引用的人物声明不存在。")
        claim_id, owner = resolved
        if correction.object_id not in {claim_id, reference}:
            raise _TargetResolutionError(f"修正 {correction.id} 的 object_id 与人物声明不匹配。")
        attribute = (
            "statement"
            if correction.field == CorrectionField.BIOGRAPHY_CLAIM_STATEMENT
            else "rationale"
        )
        return _TextTarget(
            key=(correction.field, reference),
            review_target_id=correction.review_target_id,
            owner=owner,
            attribute=attribute,
            index=None,
            value=getattr(owner, attribute),
        )

    def _resolve_interpretation(self, correction: NarrativeCorrection) -> _TextTarget:
        """按人工评审使用的一基序号解析主题或母题。"""
        prefix = correction.field.value
        raw_index = correction.object_id.removeprefix(f"{prefix}:")
        try:
            index = int(raw_index) - 1
        except ValueError as error:
            raise _TargetResolutionError(
                f"修正 {correction.id} 的 object_id 必须是一基序号。"
            ) from error
        values = (
            self.breakdown.structure.themes
            if correction.field == CorrectionField.THEME
            else self.breakdown.structure.motifs
        )
        if index < 0 or index >= len(values):
            raise _TargetResolutionError(f"修正 {correction.id} 引用的解释项不存在。")
        return _TextTarget(
            key=(correction.field, str(index + 1)),
            review_target_id=f"{prefix}:{index + 1}",
            owner=values,
            attribute=None,
            index=index,
            value=values[index],
        )
