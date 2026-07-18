"""全人物分级档案覆盖、引用和策略快照的确定性校验。"""

from __future__ import annotations

from movie_breakdown.application.character_dossiers import (
    CharacterDossierStrategy,
    RuleBasedCharacterDossierStrategy,
)
from movie_breakdown.domain.base import Severity
from movie_breakdown.domain.character_biography import BiographyCatalog
from movie_breakdown.domain.character_dossier import CharacterDossierCatalog, CharacterDossierTier
from movie_breakdown.domain.global_analysis import (
    CharacterRelation,
    GlobalAnalysisResult,
    StoryEvent,
)
from movie_breakdown.domain.run import ValidationIssue
from movie_breakdown.domain.source import Screenplay


class CharacterDossierValidationService:
    """校验全人物档案与全局人物、事件、关系和分级策略一致。"""

    def __init__(self, strategy: CharacterDossierStrategy | None = None) -> None:
        """创建全人物档案校验服务。

        Args:
            strategy: 可选且必须与生成阶段一致的人物档案策略。
        """
        self.strategy = strategy or RuleBasedCharacterDossierStrategy()

    def validate(
        self,
        catalog: CharacterDossierCatalog,
        biographies: BiographyCatalog | None,
        screenplay: Screenplay,
        global_result: GlobalAnalysisResult,
        issues: list[ValidationIssue],
    ) -> None:
        """把档案覆盖、引用和分级一致性问题追加到总报告。

        Args:
            catalog: 当前全人物分级档案目录。
            biographies: 可选的核心人物完整小传目录。
            screenplay: 提供有效场景集合的完整剧本。
            global_result: 提供人物、事件、关系和弧光的全局结果。
            issues: 接收新增校验问题的可变列表。
        """
        expected = self.strategy.build(screenplay, global_result)
        expected_by_id = {item.character_id: item for item in expected.dossiers}
        actual_by_id = {item.character_id: item for item in catalog.dossiers}
        expected_ids = [item.character_id for item in expected.dossiers]
        actual_ids = [item.character_id for item in catalog.dossiers]
        for character_id in sorted(set(expected_ids) - set(actual_ids)):
            issues.append(self._error("dossier.coverage", "归一人物缺少分级档案。", character_id))
        for character_id in sorted(set(actual_ids) - set(expected_ids)):
            issues.append(
                self._error("dossier.character_ref", "分级档案引用未知人物。", character_id)
            )
        if actual_ids != [item for item in expected_ids if item in actual_by_id]:
            issues.append(self._error("dossier.order", "人物档案顺序与全局人物目录不一致。"))
        if (
            catalog.policy_version != expected.policy_version
            or catalog.scene_recurring_threshold != expected.scene_recurring_threshold
            or catalog.event_recurring_threshold != expected.event_recurring_threshold
        ):
            issues.append(self._error("dossier.policy", "人物档案分级策略或阈值已过期。"))
        events = {item.id: item for item in global_result.events.events}
        relationships = {item.id: item for item in global_result.relationships.relationships}
        scenes = {item.id for item in screenplay.scenes}
        for character_id, dossier in actual_by_id.items():
            reference = f"dossier:{character_id}"
            self._validate_refs(dossier.scene_ids, scenes, "dossier.scene_ref", reference, issues)
            self._validate_event_ownership(
                character_id, dossier.event_ids, events, reference, issues
            )
            self._validate_relationship_ownership(
                character_id,
                dossier.relationship_ids,
                relationships,
                reference,
                issues,
            )
            expected_dossier = expected_by_id.get(character_id)
            if expected_dossier is not None and dossier != expected_dossier:
                issues.append(
                    self._error(
                        "dossier.snapshot",
                        "人物档案内容或分级与当前确定性策略不一致。",
                        reference,
                    )
                )
        if biographies is not None:
            core_ids = {
                item.character_id
                for item in catalog.dossiers
                if item.tier == CharacterDossierTier.CORE
            }
            biography_ids = {item.character_id for item in biographies.biographies}
            for character_id in sorted(core_ids - biography_ids):
                issues.append(
                    self._error("biography.coverage", "核心人物缺少完整人物小传。", character_id)
                )
            for character_id in sorted(biography_ids - core_ids):
                issues.append(
                    self._error(
                        "biography.non_core",
                        "非核心人物不应占用完整人物小传名额。",
                        character_id,
                    )
                )

    def _validate_event_ownership(
        self,
        character_id: str,
        event_ids: list[str],
        events: dict[str, StoryEvent],
        reference: str,
        issues: list[ValidationIssue],
    ) -> None:
        """检查档案事件存在且当前人物确实参与。"""
        for event_id in event_ids:
            event = events.get(event_id)
            if event is None:
                issues.append(self._error("dossier.event_ref", "人物档案引用未知事件。", reference))
            elif character_id not in event.participant_ids:
                issues.append(
                    self._error("dossier.event_owner", "档案事件不属于当前人物。", reference)
                )

    def _validate_relationship_ownership(
        self,
        character_id: str,
        relationship_ids: list[str],
        relationships: dict[str, CharacterRelation],
        reference: str,
        issues: list[ValidationIssue],
    ) -> None:
        """检查档案关系存在且当前人物是关系端点。"""
        for relation_id in relationship_ids:
            relation = relationships.get(relation_id)
            if relation is None:
                issues.append(
                    self._error("dossier.relationship_ref", "人物档案引用未知关系。", reference)
                )
                continue
            endpoints = {
                relation.source_character_id,
                relation.target_character_id,
            }
            if character_id not in endpoints:
                issues.append(
                    self._error(
                        "dossier.relationship_owner",
                        "档案关系不属于当前人物。",
                        reference,
                    )
                )

    @staticmethod
    def _validate_refs(
        values: list[str],
        known: set[str],
        code: str,
        reference: str,
        issues: list[ValidationIssue],
    ) -> None:
        """检查一组 ID 是否全部存在于目标集合。"""
        for value in values:
            if value not in known:
                issues.append(
                    CharacterDossierValidationService._error(
                        code,
                        f"人物档案引用未知 ID：{value}",
                        reference,
                    )
                )

    @staticmethod
    def _error(
        code: str,
        message: str,
        reference: str | None = None,
    ) -> ValidationIssue:
        """构造人物档案错误级校验问题。"""
        return ValidationIssue(
            severity=Severity.ERROR,
            code=code,
            message=message,
            reference=reference,
        )
