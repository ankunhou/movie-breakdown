"""不调用模型的制片元素覆盖、证据和逐场引用校验。"""

from __future__ import annotations

from movie_breakdown.application.production_catalog_validation import (
    ProductionCatalogValidationService,
)
from movie_breakdown.application.production_validation_support import (
    check_refs,
    check_unique,
    production_issue,
    validate_production_evidence,
)
from movie_breakdown.domain.base import Severity, StageStatus
from movie_breakdown.domain.production_catalog import (
    GlobalProductionCatalog,
    ProductionValidationReport,
)
from movie_breakdown.domain.production_common import DayPhase, InteriorExterior
from movie_breakdown.domain.production_scene import (
    SceneProductionAnalysis,
    SceneProductionRecord,
)
from movie_breakdown.domain.run import ValidationIssue
from movie_breakdown.domain.source import Scene, Screenplay


class ProductionValidationService:
    """检查逐场制片拆解和确定性总表能否安全导出。"""

    def validate_analysis(
        self,
        scene: Scene,
        analysis: SceneProductionAnalysis,
    ) -> list[ValidationIssue]:
        """单独检查模型刚返回的一场制片结果。

        Args:
            scene: 本次模型调用对应的原始场景。
            analysis: 完成证据规范化的制片结果。

        Returns:
            该场全部错误和警告；调用方应阻断任一错误。
        """
        issues: list[ValidationIssue] = []
        if analysis.scene_id != scene.id:
            issues.append(
                production_issue(
                    Severity.ERROR,
                    "production.id_mismatch",
                    "输入场景与分析的场景 ID 不一致。",
                    scene.id,
                )
            )
            return issues
        self._validate_analysis(analysis, scene, {scene.id: scene}, issues)
        return issues

    def validate(
        self,
        screenplay: Screenplay,
        records: list[SceneProductionRecord],
        catalog: GlobalProductionCatalog | None,
        *,
        require_catalog: bool = True,
    ) -> ProductionValidationReport:
        """执行完整制片一致性校验。

        Args:
            screenplay: 场景切分后的共享只读剧本索引。
            records: 独立制片流水线保存的逐场记录。
            catalog: 由逐场记录确定性汇总的全剧制片目录。
            require_catalog: 是否把目录缺失视为阻断错误。

        Returns:
            包含覆盖率、目录规模及全部问题的制片校验报告。
        """
        issues: list[ValidationIssue] = []
        scenes = {scene.id: scene for scene in screenplay.scenes}
        self._validate_scene_index(screenplay, issues)
        analyses = self._validate_records(records, scenes, issues)
        if catalog is None:
            if require_catalog:
                issues.append(
                    production_issue(
                        Severity.ERROR,
                        "production.catalog_missing",
                        "缺少制片元素总表。",
                    )
                )
        else:
            ProductionCatalogValidationService().validate(catalog, analyses, screenplay, issues)
        coverage = len(analyses) / len(scenes) if scenes else 0.0
        if coverage < 1:
            issues.append(
                production_issue(
                    Severity.ERROR,
                    "production.scene_coverage",
                    f"制片逐场覆盖率为 {coverage:.1%}，要求为 100%。",
                )
            )
        catalog_count = 0
        if catalog is not None:
            catalog_count = sum(
                len(items)
                for items in (catalog.locations, catalog.cast, catalog.background, catalog.elements)
            )
        return ProductionValidationReport(
            valid=not any(issue.severity == Severity.ERROR for issue in issues),
            scene_count=len(scenes),
            analyzed_scene_count=len(analyses),
            coverage=coverage,
            catalog_item_count=catalog_count,
            issues=issues,
        )

    def _validate_scene_index(
        self,
        screenplay: Screenplay,
        issues: list[ValidationIssue],
    ) -> None:
        """检查共享场景索引的唯一性和顺序。"""
        ids = [scene.id for scene in screenplay.scenes]
        check_unique(ids, "production.scene_duplicate", "场景 ID 重复。", issues)
        ordinals = [scene.ordinal for scene in screenplay.scenes]
        if ordinals != list(range(1, len(ordinals) + 1)):
            issues.append(
                production_issue(
                    Severity.ERROR,
                    "production.scene_ordinal",
                    "场景顺序编号不连续。",
                )
            )

    def _validate_records(
        self,
        records: list[SceneProductionRecord],
        scenes: dict[str, Scene],
        issues: list[ValidationIssue],
    ) -> dict[str, SceneProductionAnalysis]:
        """检查逐场状态、ID、需求引用和本场证据。"""
        analyses: dict[str, SceneProductionAnalysis] = {}
        seen: set[str] = set()
        for record in records:
            reference = f"production:{record.scene_id}"
            if record.scene_id in seen:
                issues.append(
                    production_issue(
                        Severity.ERROR,
                        "production.record_duplicate",
                        "逐场记录重复。",
                        reference,
                    )
                )
                continue
            seen.add(record.scene_id)
            scene = scenes.get(record.scene_id)
            if scene is None:
                issues.append(
                    production_issue(
                        Severity.ERROR,
                        "production.scene_ref",
                        "记录引用未知场景。",
                        reference,
                    )
                )
                continue
            if record.status != StageStatus.SUCCESS or record.analysis is None:
                issues.append(
                    production_issue(
                        Severity.ERROR,
                        "production.record_failed",
                        record.error or "制片逐场分析未成功。",
                        reference,
                    )
                )
                continue
            if record.analysis.scene_id != record.scene_id:
                issues.append(
                    production_issue(
                        Severity.ERROR,
                        "production.id_mismatch",
                        "记录与分析的场景 ID 不一致。",
                        reference,
                    )
                )
                continue
            analyses[record.scene_id] = record.analysis
            self._validate_analysis(record.analysis, scene, scenes, issues)
        return analyses

    def _validate_analysis(
        self,
        analysis: SceneProductionAnalysis,
        scene: Scene,
        scenes: dict[str, Scene],
        issues: list[ValidationIssue],
    ) -> None:
        """检查单场设置、需求 ID、交叉引用和证据。"""
        reference = f"production:{scene.id}"
        if analysis.setting.raw_heading != scene.heading:
            issues.append(
                production_issue(
                    Severity.ERROR,
                    "production.heading",
                    "制片场景标题与原场景不一致。",
                    reference,
                )
            )
        if analysis.setting.interior_exterior == InteriorExterior.UNKNOWN:
            issues.append(
                production_issue(
                    Severity.WARNING,
                    "production.interior_unknown",
                    "内外景未能确认。",
                    reference,
                )
            )
        if analysis.setting.time_of_day == DayPhase.UNKNOWN:
            issues.append(
                production_issue(
                    Severity.WARNING,
                    "production.time_unknown",
                    "场景时段未能确认。",
                    reference,
                )
            )
        requirements = [*analysis.cast, *analysis.background, *analysis.elements]
        requirement_ids = [item.id for item in requirements]
        check_unique(
            requirement_ids,
            "production.requirement_duplicate",
            "本场制片需求 ID 重复。",
            issues,
            reference,
        )
        cast_ids = {item.id for item in analysis.cast}
        known_ids = set(requirement_ids)
        for element in analysis.elements:
            check_refs(
                element.associated_cast_ids,
                cast_ids,
                "production.element_cast_ref",
                element.id,
                issues,
            )
        for factor in analysis.complexity.factors:
            check_refs(
                factor.related_requirement_ids,
                known_ids,
                "production.complexity_ref",
                reference,
                issues,
            )
        element_keys = [(item.kind, item.name.casefold().strip()) for item in analysis.elements]
        if len(element_keys) != len(set(element_keys)):
            issues.append(
                production_issue(
                    Severity.WARNING,
                    "production.element_duplicate",
                    "本场存在同类别同名元素，请人工确认是否需要合并。",
                    reference,
                )
            )
        evidence_groups = [("setting", analysis.setting.evidence)]
        evidence_groups.extend((item.id, item.evidence) for item in requirements)
        evidence_groups.extend(
            (f"complexity:{index}", factor.evidence)
            for index, factor in enumerate(analysis.complexity.factors, start=1)
        )
        for suffix, evidence in evidence_groups:
            validate_production_evidence(
                evidence,
                scenes,
                {scene.id},
                issues,
                f"{reference}:{suffix}",
            )
