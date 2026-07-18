"""确定性制片总表的来源、场景和完整性校验。"""

from __future__ import annotations

from typing import Literal

from movie_breakdown.application.production_aggregation import (
    ConservativeProductionCatalogBuilder,
    ProductionCatalogBuilder,
)
from movie_breakdown.application.production_validation_support import (
    check_refs,
    check_unique,
    production_issue,
    validate_production_evidence,
)
from movie_breakdown.domain.base import Severity
from movie_breakdown.domain.production_catalog import GlobalProductionCatalog
from movie_breakdown.domain.production_common import ProductionElementKind
from movie_breakdown.domain.production_scene import SceneProductionAnalysis
from movie_breakdown.domain.run import ValidationIssue
from movie_breakdown.domain.source import Scene, Screenplay

SourceKind = Literal["cast", "background", "element"]
SourceInfo = tuple[SourceKind, str, ProductionElementKind | None]


class ProductionCatalogValidationService:
    """验证制片总表完全且只由逐场需求确定性派生。"""

    def __init__(self, builder: ProductionCatalogBuilder | None = None) -> None:
        """初始化目录校验服务。

        Args:
            builder: 可选的确定性目录构建策略；省略时使用保守默认策略。
        """
        self._builder = builder or ConservativeProductionCatalogBuilder()

    def validate(
        self,
        catalog: GlobalProductionCatalog,
        analyses: dict[str, SceneProductionAnalysis],
        screenplay: Screenplay,
        issues: list[ValidationIssue],
    ) -> None:
        """把制片总表问题追加到给定列表。

        Args:
            catalog: 待验证的全剧制片目录。
            analyses: 已通过基础检查的逐场结果。
            screenplay: 提供完整场景集合和稳定顺序的共享剧本。
            issues: 接收问题的可变列表。
        """
        scenes = {scene.id: scene for scene in screenplay.scenes}
        all_items = [*catalog.locations, *catalog.cast, *catalog.background, *catalog.elements]
        check_unique(
            [item.id for item in all_items],
            "production.catalog_duplicate",
            "制片总表 ID 重复。",
            issues,
        )
        sources = self._source_index(analyses)
        used: set[str] = set()
        for item in catalog.locations:
            check_refs(
                item.scene_ids,
                set(scenes),
                "production.catalog_scene_ref",
                item.id,
                issues,
            )
            validate_production_evidence(
                item.evidence,
                scenes,
                set(item.scene_ids),
                issues,
                item.id,
            )
        for kind, items in (
            ("cast", catalog.cast),
            ("background", catalog.background),
            ("element", catalog.elements),
        ):
            for item in items:
                element_kind = item.kind if kind == "element" else None
                used.update(
                    self._validate_sources(
                        item.id,
                        item.scene_ids,
                        item.source_requirement_ids,
                        kind,
                        element_kind,
                        sources,
                        scenes,
                        issues,
                    )
                )
                validate_production_evidence(
                    item.evidence,
                    scenes,
                    set(item.scene_ids),
                    issues,
                    item.id,
                )
        self._validate_completeness(catalog, analyses, sources, used, issues)
        self._validate_deterministic_derivation(catalog, analyses, screenplay, issues)

    def _validate_deterministic_derivation(
        self,
        catalog: GlobalProductionCatalog,
        analyses: dict[str, SceneProductionAnalysis],
        screenplay: Screenplay,
        issues: list[ValidationIssue],
    ) -> None:
        """用正式构建策略重建目录并要求全部字段完全一致。"""
        expected_scene_ids = {scene.id for scene in screenplay.scenes}
        if set(analyses) != expected_scene_ids:
            return
        expected = self._builder.build(screenplay, list(analyses.values()))
        if catalog != expected:
            issues.append(
                production_issue(
                    Severity.ERROR,
                    "production.catalog_derivation_mismatch",
                    "制片总表与当前逐场结果的确定性重建不一致。",
                    "production:catalog",
                )
            )

    def _validate_completeness(
        self,
        catalog: GlobalProductionCatalog,
        analyses: dict[str, SceneProductionAnalysis],
        sources: dict[str, SourceInfo],
        used: set[str],
        issues: list[ValidationIssue],
    ) -> None:
        """检查逐场需求、地点和高复杂度场景均进入总表。"""
        for source_id in sorted(set(sources) - used):
            issues.append(
                production_issue(
                    Severity.ERROR,
                    "production.catalog_omission",
                    "逐场制片需求未进入总表。",
                    source_id,
                )
            )
        location_scenes = {scene_id for item in catalog.locations for scene_id in item.scene_ids}
        for scene_id in sorted(set(analyses) - location_scenes):
            issues.append(
                production_issue(
                    Severity.ERROR,
                    "production.location_omission",
                    "场景地点未进入总表。",
                    scene_id,
                )
            )
        expected = {
            scene_id: analysis
            for scene_id, analysis in analyses.items()
            if analysis.complexity.score >= 4
        }
        check_unique(
            [item.scene_id for item in catalog.complex_scenes],
            "production.complex_duplicate",
            "高复杂度场景索引重复。",
            issues,
        )
        actual_ids = {item.scene_id for item in catalog.complex_scenes}
        for scene_id in sorted(set(expected) - actual_ids):
            issues.append(
                production_issue(
                    Severity.ERROR,
                    "production.complex_omission",
                    "高复杂度场景未进入索引。",
                    scene_id,
                )
            )
        for item in catalog.complex_scenes:
            analysis = expected.get(item.scene_id)
            if analysis is None:
                issues.append(
                    production_issue(
                        Severity.ERROR,
                        "production.complex_ref",
                        "索引引用非高复杂度场景。",
                        item.scene_id,
                    )
                )
            elif (item.score, item.level) != (
                analysis.complexity.score,
                analysis.complexity.level,
            ):
                issues.append(
                    production_issue(
                        Severity.ERROR,
                        "production.complex_mismatch",
                        "复杂度索引与逐场结果不一致。",
                        item.scene_id,
                    )
                )

    def _source_index(
        self,
        analyses: dict[str, SceneProductionAnalysis],
    ) -> dict[str, SourceInfo]:
        """建立带场景前缀的逐场需求索引。"""
        sources: dict[str, SourceInfo] = {}
        for scene_id, analysis in analyses.items():
            for item in analysis.cast:
                sources[f"{scene_id}/{item.id}"] = ("cast", scene_id, None)
            for item in analysis.background:
                sources[f"{scene_id}/{item.id}"] = ("background", scene_id, None)
            for item in analysis.elements:
                sources[f"{scene_id}/{item.id}"] = ("element", scene_id, item.kind)
        return sources

    def _validate_sources(
        self,
        item_id: str,
        scene_ids: list[str],
        source_ids: list[str],
        expected_kind: SourceKind,
        expected_element_kind: ProductionElementKind | None,
        sources: dict[str, SourceInfo],
        scenes: dict[str, Scene],
        issues: list[ValidationIssue],
    ) -> set[str]:
        """核对一个聚合项的来源类型和派生场景。"""
        check_refs(
            scene_ids,
            set(scenes),
            "production.catalog_scene_ref",
            item_id,
            issues,
        )
        used: set[str] = set()
        derived_scenes: set[str] = set()
        for source_id in source_ids:
            source = sources.get(source_id)
            if source is None:
                issues.append(
                    production_issue(
                        Severity.ERROR,
                        "production.catalog_source_ref",
                        "总表引用未知逐场需求。",
                        item_id,
                    )
                )
                continue
            used.add(source_id)
            source_kind, scene_id, element_kind = source
            derived_scenes.add(scene_id)
            if source_kind != expected_kind or element_kind != expected_element_kind:
                issues.append(
                    production_issue(
                        Severity.ERROR,
                        "production.catalog_kind",
                        "总表与逐场需求类别不一致。",
                        item_id,
                    )
                )
        if set(scene_ids) != derived_scenes:
            issues.append(
                production_issue(
                    Severity.ERROR,
                    "production.catalog_scenes",
                    "总表场景与来源需求不一致。",
                    item_id,
                )
            )
        return used
