"""不调用模型的剧本拆解一致性校验。"""

from __future__ import annotations

from collections.abc import Iterable

from movie_breakdown.application.character_validation import CharacterArtifactValidationService
from movie_breakdown.application.structure_validation import validate_act_assignments
from movie_breakdown.domain.base import Severity, StageStatus
from movie_breakdown.domain.character_biography import BiographyCatalog
from movie_breakdown.domain.character_dossier import CharacterDossierCatalog
from movie_breakdown.domain.global_analysis import GlobalAnalysisResult
from movie_breakdown.domain.run import ValidationIssue, ValidationReport
from movie_breakdown.domain.scene_analysis import Evidence, SceneAnalysisRecord
from movie_breakdown.domain.source import Scene, Screenplay


class ValidationService:
    """检查覆盖率、证据范围和跨产物引用完整性。"""

    def validate(
        self,
        screenplay: Screenplay,
        records: list[SceneAnalysisRecord],
        global_result: GlobalAnalysisResult | None,
        biographies: BiographyCatalog | None = None,
        dossiers: CharacterDossierCatalog | None = None,
        *,
        require_biographies: bool = True,
        require_dossiers: bool | None = None,
    ) -> ValidationReport:
        """对当前全部结构化产物执行本地一致性校验。

        Args:
            screenplay: 场景切分后的剧本。
            records: 逐场分析状态与结果。
            global_result: 已完成的全局分析；缺失时记录错误。
            biographies: 已完成的人物小传目录；缺失时记录错误。
            dossiers: 全部已归一人物的分级档案目录；缺失时记录错误。
            require_biographies: 是否把人物小传缺失视为错误。
            require_dossiers: 是否要求人物档案；缺省时跟随人物小传要求。

        Returns:
            包含覆盖率及所有错误和警告的校验报告。
        """
        issues: list[ValidationIssue] = []
        require_dossiers = require_biographies if require_dossiers is None else require_dossiers
        scenes = {scene.id: scene for scene in screenplay.scenes}
        self._validate_scene_index(screenplay, issues)
        successful = self._validate_records(records, scenes, issues)
        if global_result is None:
            issues.append(self._error("global.missing", "缺少全局叙事分析产物。"))
        else:
            self._validate_global(global_result, scenes, issues)
            CharacterArtifactValidationService().validate(
                screenplay,
                global_result,
                biographies,
                dossiers,
                issues,
                require_biographies=require_biographies,
                require_dossiers=require_dossiers,
            )

        coverage = len(successful) / len(scenes) if scenes else 0.0
        if coverage < 1:
            issues.append(
                self._error(
                    "scene.coverage",
                    f"逐场分析覆盖率为 {coverage:.1%}，要求为 100%。",
                )
            )
        valid = not any(issue.severity == Severity.ERROR for issue in issues)
        return ValidationReport(
            valid=valid,
            scene_count=len(scenes),
            analyzed_scene_count=len(successful),
            coverage=coverage,
            issues=issues,
        )

    def _validate_scene_index(
        self,
        screenplay: Screenplay,
        issues: list[ValidationIssue],
    ) -> None:
        """检查场景 ID 唯一性和顺序连续性。"""
        ids = [scene.id for scene in screenplay.scenes]
        if len(ids) != len(set(ids)):
            issues.append(self._error("scene.duplicate_id", "场景存在重复 ID。"))
        ordinals = [scene.ordinal for scene in screenplay.scenes]
        if ordinals != list(range(1, len(ordinals) + 1)):
            issues.append(self._error("scene.ordinal", "场景顺序编号不连续。"))

    def _validate_records(
        self,
        records: list[SceneAnalysisRecord],
        scenes: dict[str, Scene],
        issues: list[ValidationIssue],
    ) -> set[str]:
        """检查逐场状态、场景引用和证据位置。"""
        successful: set[str] = set()
        seen: set[str] = set()
        for record in records:
            if record.scene_id in seen:
                issues.append(
                    self._error("analysis.duplicate", "逐场分析记录重复。", record.scene_id)
                )
            seen.add(record.scene_id)
            if record.scene_id not in scenes:
                issues.append(
                    self._error("analysis.scene_ref", "逐场分析引用未知场景。", record.scene_id)
                )
                continue
            if record.analysis is None or record.status != StageStatus.SUCCESS:
                issues.append(
                    self._error(
                        "analysis.failed",
                        record.error or "场景分析未成功。",
                        record.scene_id,
                    )
                )
                continue
            if record.analysis.scene_id != record.scene_id:
                issues.append(
                    self._error(
                        "analysis.id_mismatch",
                        "分析内容中的场景 ID 与记录不一致。",
                        record.scene_id,
                    )
                )
                continue
            successful.add(record.scene_id)
            self._validate_evidence(
                record.analysis.evidence,
                scenes,
                issues,
                f"analysis:{record.scene_id}",
            )
            for event in record.analysis.events:
                self._validate_evidence(
                    event.evidence, scenes, issues, f"analysis:{record.scene_id}"
                )
        return successful

    def _validate_global(
        self,
        result: GlobalAnalysisResult,
        scenes: dict[str, Scene],
        issues: list[ValidationIssue],
    ) -> None:
        """检查全局实体、事件、关系和结构的引用完整性。"""
        characters = {item.id for item in result.entities.characters}
        events = {item.id for item in result.events.events}
        unique_groups = {
            "character": [item.id for item in result.entities.characters],
            "location": [item.id for item in result.entities.locations],
            "event": [item.id for item in result.events.events],
            "relationship": [item.id for item in result.relationships.relationships],
            "arc": [item.character_id for item in result.relationships.character_arcs],
            "beat": [item.id for item in result.structure.beats],
            "plot": [item.id for item in result.structure.plot_threads],
            "foreshadow": [item.id for item in result.structure.foreshadowing],
        }
        for kind, values in unique_groups.items():
            self._check_unique(values, kind, issues)
        for kind, items in (
            ("character", result.entities.characters),
            ("location", result.entities.locations),
        ):
            for item in items:
                reference = f"{kind}:{item.id}"
                self._check_scene_refs([(reference, item.scene_ids)], scenes, issues)
                self._validate_evidence(item.evidence, scenes, issues, reference)
        for event in result.events.events:
            self._check_scene_refs([(f"event:{event.id}", [event.scene_id])], scenes, issues)
            self._check_refs(
                event.participant_ids, characters, "event.character_ref", event.id, issues
            )
            self._check_refs(event.cause_event_ids, events, "event.cause_ref", event.id, issues)
            self._validate_evidence(event.evidence, scenes, issues, f"event:{event.id}")
        for relation in result.relationships.relationships:
            self._check_refs(
                [relation.source_character_id, relation.target_character_id],
                characters,
                "relation.character_ref",
                relation.id,
                issues,
            )
            self._check_scene_refs(
                [(f"relation:{relation.id}", relation.scene_ids)], scenes, issues
            )
            self._validate_evidence(relation.evidence, scenes, issues, f"relation:{relation.id}")
        for arc in result.relationships.character_arcs:
            self._check_refs(
                [arc.character_id], characters, "arc.character_ref", arc.character_id, issues
            )
            self._validate_evidence(arc.evidence, scenes, issues, f"arc:{arc.character_id}")
            for point in arc.turning_points:
                self._check_scene_refs(
                    [(f"arc:{arc.character_id}", point.scene_ids)], scenes, issues
                )
                self._validate_evidence(point.evidence, scenes, issues, f"arc:{arc.character_id}")
        structural_refs: list[tuple[str, list[str]]] = []
        structural_refs.extend((f"act:{act.act}", act.scene_ids) for act in result.structure.acts)
        structural_refs.extend(
            (f"beat:{beat.id}", beat.scene_ids) for beat in result.structure.beats
        )
        structural_refs.extend(
            (f"plot:{thread.id}", thread.scene_ids) for thread in result.structure.plot_threads
        )
        for link in result.structure.foreshadowing:
            structural_refs.append((f"foreshadow:{link.id}:setup", link.setup_scene_ids))
            structural_refs.append((f"foreshadow:{link.id}:payoff", link.payoff_scene_ids))
        self._check_scene_refs(structural_refs, scenes, issues)
        validate_act_assignments(result.structure.acts, scenes, issues)
        evidence_groups = [
            *((f"act:{item.act}", item.evidence) for item in result.structure.acts),
            *((f"beat:{item.id}", item.evidence) for item in result.structure.beats),
            *((f"plot:{item.id}", item.evidence) for item in result.structure.plot_threads),
            *((f"foreshadow:{item.id}", item.evidence) for item in result.structure.foreshadowing),
            ("structure", result.structure.evidence),
        ]
        for reference, evidence in evidence_groups:
            self._validate_evidence(evidence, scenes, issues, reference)

    def _validate_evidence(
        self,
        evidence_items: list[Evidence],
        scenes: dict[str, Scene],
        issues: list[ValidationIssue],
        reference: str,
    ) -> None:
        """检查证据场景、行号范围和原文摘录。"""
        for evidence in evidence_items:
            scene = scenes.get(evidence.scene_id)
            if scene is None:
                issues.append(self._error("evidence.scene_ref", "证据引用未知场景。", reference))
                continue
            span = evidence.source_span
            if (
                span.line_start < scene.source_span.line_start
                or span.line_end > scene.source_span.line_end
            ):
                issues.append(self._error("evidence.span", "证据行号超出场景范围。", reference))
            if evidence.excerpt.strip() and evidence.excerpt.strip() not in scene.text:
                issues.append(
                    ValidationIssue(
                        severity=Severity.WARNING,
                        code="evidence.excerpt",
                        message="证据摘录未在场景原文中找到完全匹配。",
                        reference=reference,
                    )
                )

    def _check_scene_refs(
        self,
        values: Iterable[tuple[str, list[str]]],
        scenes: dict[str, Scene],
        issues: list[ValidationIssue],
    ) -> None:
        """批量检查业务对象的场景引用。"""
        for reference, scene_ids in values:
            self._check_refs(scene_ids, set(scenes), "global.scene_ref", reference, issues)

    def _check_refs(
        self,
        values: Iterable[str],
        known: set[str],
        code: str,
        reference: str,
        issues: list[ValidationIssue],
    ) -> None:
        """把未知 ID 引用转换为可定位的校验错误。"""
        for value in values:
            if value not in known:
                issues.append(self._error(code, f"引用了未知 ID：{value}", reference))

    def _check_unique(
        self,
        values: list[str],
        kind: str,
        issues: list[ValidationIssue],
    ) -> None:
        """检查全局业务实体 ID 是否唯一。"""
        if len(values) != len(set(values)):
            issues.append(self._error(f"{kind}.duplicate_id", f"{kind} 存在重复 ID。"))

    @staticmethod
    def _error(code: str, message: str, reference: str | None = None) -> ValidationIssue:
        """构造格式一致的错误级校验问题。"""
        return ValidationIssue(
            severity=Severity.ERROR,
            code=code,
            message=message,
            reference=reference,
        )
