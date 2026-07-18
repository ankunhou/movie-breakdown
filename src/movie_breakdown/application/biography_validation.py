"""人物小传引用、覆盖范围和证据位置的确定性校验。"""

from __future__ import annotations

from movie_breakdown.application.biography_context import select_biography_characters
from movie_breakdown.domain.base import Severity
from movie_breakdown.domain.character_biography import BiographyCatalog
from movie_breakdown.domain.character_dossier import CharacterDossierCatalog, CharacterDossierTier
from movie_breakdown.domain.global_analysis import CharacterRelation, GlobalAnalysisResult
from movie_breakdown.domain.run import ValidationIssue
from movie_breakdown.domain.scene_analysis import Evidence
from movie_breakdown.domain.source import Scene, Screenplay


class BiographyValidationService:
    """校验人物小传与人物、关系、场景和原文之间的确定性约束。"""

    def validate(
        self,
        catalog: BiographyCatalog,
        screenplay: Screenplay,
        global_result: GlobalAnalysisResult,
        issues: list[ValidationIssue],
        dossiers: CharacterDossierCatalog | None = None,
    ) -> None:
        """把人物小传的一致性问题追加到总校验问题列表。

        Args:
            catalog: 已聚合的人物小传目录。
            screenplay: 提供场景顺序和原文的完整剧本。
            global_result: 提供人物、关系和人物弧光的全局分析。
            issues: 接收新增校验问题的可变列表。
            dossiers: 可选的全人物分级档案，用作核心人物单一真源。
        """
        scenes = {scene.id: scene for scene in screenplay.scenes}
        characters = {item.id for item in global_result.entities.characters}
        relationships = {item.id: item for item in global_result.relationships.relationships}
        expected = (
            {
                item.character_id
                for item in dossiers.dossiers
                if item.tier == CharacterDossierTier.CORE
            }
            if dossiers is not None
            else {item.id for item in select_biography_characters(screenplay, global_result)}
        )
        actual = {item.character_id for item in catalog.biographies}
        for character_id in sorted(expected - actual):
            issues.append(
                self._error(
                    "biography.coverage",
                    "核心人物缺少人物小传。",
                    character_id,
                )
            )
        for biography in catalog.biographies:
            reference = f"biography:{biography.character_id}"
            if biography.character_id not in characters:
                issues.append(
                    self._error(
                        "biography.character_ref",
                        "人物小传引用未知人物。",
                        reference,
                    )
                )
            self._validate_scene_refs(
                biography.context_scene_ids,
                scenes,
                issues,
                reference,
            )
            self._validate_relationships(
                biography.character_id,
                biography.key_relationship_ids,
                relationships,
                issues,
                reference,
            )
            claims = [biography.summary, *biography.claims]
            for claim in claims:
                self._validate_evidence(
                    claim.evidence,
                    scenes,
                    issues,
                    f"{reference}:{claim.id}",
                )
            self._validate_evidence(
                biography.representative_lines,
                scenes,
                issues,
                f"{reference}:representative-lines",
            )
            context_ids = set(biography.context_scene_ids)
            for line in biography.representative_lines:
                if line.scene_id not in context_ids:
                    issues.append(
                        self._error(
                            "biography.line_context",
                            "代表性台词不在人物小传原文上下文中。",
                            reference,
                        )
                    )

    def _validate_relationships(
        self,
        character_id: str,
        relation_ids: list[str],
        relationships: dict[str, CharacterRelation],
        issues: list[ValidationIssue],
        reference: str,
    ) -> None:
        """检查关键关系存在且确实连接当前人物。"""
        for relation_id in relation_ids:
            relation = relationships.get(relation_id)
            if relation is None:
                issues.append(
                    self._error(
                        "biography.relationship_ref",
                        f"人物小传引用未知关系：{relation_id}",
                        reference,
                    )
                )
                continue
            endpoints = {
                relation.source_character_id,
                relation.target_character_id,
            }
            if character_id not in endpoints:
                issues.append(
                    self._error(
                        "biography.relationship_owner",
                        f"关系 {relation_id} 不属于当前人物。",
                        reference,
                    )
                )

    def _validate_scene_refs(
        self,
        scene_ids: list[str],
        scenes: dict[str, Scene],
        issues: list[ValidationIssue],
        reference: str,
    ) -> None:
        """检查人物小传上下文中的场景引用。"""
        for scene_id in scene_ids:
            if scene_id not in scenes:
                issues.append(
                    self._error(
                        "biography.scene_ref",
                        f"人物小传引用未知场景：{scene_id}",
                        reference,
                    )
                )

    def _validate_evidence(
        self,
        evidence_items: list[Evidence],
        scenes: dict[str, Scene],
        issues: list[ValidationIssue],
        reference: str,
    ) -> None:
        """检查人物小传证据的场景、行号和原文摘录。"""
        for evidence in evidence_items:
            scene = scenes.get(evidence.scene_id)
            if scene is None:
                issues.append(
                    self._error(
                        "biography.evidence.scene_ref",
                        "人物小传证据引用未知场景。",
                        reference,
                    )
                )
                continue
            span = evidence.source_span
            if (
                span.line_start < scene.source_span.line_start
                or span.line_end > scene.source_span.line_end
            ):
                issues.append(
                    self._error(
                        "biography.evidence.span",
                        "人物小传证据行号超出场景范围。",
                        reference,
                    )
                )
            if evidence.excerpt.strip() and evidence.excerpt.strip() not in scene.text:
                issues.append(
                    ValidationIssue(
                        severity=Severity.WARNING,
                        code="biography.evidence.excerpt",
                        message="人物小传证据摘录未在场景原文中找到完全匹配。",
                        reference=reference,
                    )
                )

    @staticmethod
    def _error(code: str, message: str, reference: str) -> ValidationIssue:
        """构造人物小传错误级校验问题。"""
        return ValidationIssue(
            severity=Severity.ERROR,
            code=code,
            message=message,
            reference=reference,
        )
