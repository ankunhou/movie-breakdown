"""制片规划拍摄单元与资源出现项的结构校验。"""

from __future__ import annotations

from collections import defaultdict

from movie_breakdown.application.production_plan_validation_support import (
    evidence_is_located,
    planning_issue,
)
from movie_breakdown.application.production_units import suspected_composite_reasons
from movie_breakdown.domain.production_catalog import ProductionBreakdown
from movie_breakdown.domain.production_planning import ProductionPlan, ShootingUnit
from movie_breakdown.domain.production_planning_validation import (
    ProductionPlanningIssue,
    ProductionReadinessLevel,
)
from movie_breakdown.domain.source import Scene, Screenplay

_ALL_LEVELS = list(ProductionReadinessLevel)


class ProductionPlanStructureValidator:
    """校验拍摄单元行覆盖和逐场需求出现项的双向引用。"""

    def validate(
        self,
        screenplay: Screenplay,
        breakdown: ProductionBreakdown,
        plan: ProductionPlan,
        issues: list[ProductionPlanningIssue],
    ) -> None:
        """把全部单元和出现项结构问题追加到结果列表。

        Args:
            screenplay: 当前共享剧本和原文行号。
            breakdown: 提供完整逐场需求的基础拆解。
            plan: 待校验的当前规划。
            issues: 原地追加问题的结果列表。
        """
        self._validate_units(screenplay, breakdown, plan, issues)
        self._validate_occurrences(breakdown, plan, issues)

    def _validate_units(
        self,
        screenplay: Screenplay,
        breakdown: ProductionBreakdown,
        plan: ProductionPlan,
        issues: list[ProductionPlanningIssue],
    ) -> None:
        """检查每场单元行覆盖、顺序、证据和疑似漏拆。"""
        scenes = {scene.id: scene for scene in screenplay.scenes}
        analyses = {item.scene_id: item for item in breakdown.scenes}
        grouped: dict[str, list[ShootingUnit]] = defaultdict(list)
        for unit in plan.shooting_units:
            grouped[unit.scene_id].append(unit)
        for scene_id, scene in scenes.items():
            units = sorted(grouped.get(scene_id, []), key=lambda item: item.ordinal)
            if not units:
                issues.append(
                    planning_issue(
                        "planning.unit_missing",
                        "场景缺少拍摄单元。",
                        _ALL_LEVELS,
                        scene_id,
                    )
                )
                continue
            self._validate_scene_unit_spans(scene, units, issues)
            for reason in suspected_composite_reasons(scene, analyses[scene_id], units):
                issues.append(
                    planning_issue(
                        "planning.unit_suspected_undersplit",
                        reason,
                        [],
                        scene_id,
                    )
                )
        for scene_id in sorted(set(grouped) - set(scenes)):
            issues.append(
                planning_issue(
                    "planning.unit_scene_ref",
                    "拍摄单元引用未知场景。",
                    _ALL_LEVELS,
                    scene_id,
                )
            )

    @staticmethod
    def _validate_scene_unit_spans(
        scene: Scene,
        units: list[ShootingUnit],
        issues: list[ProductionPlanningIssue],
    ) -> None:
        """验证一个场景的单元顺序、连续覆盖及逐字证据。"""
        if [item.ordinal for item in units] != list(range(1, len(units) + 1)):
            issues.append(
                planning_issue(
                    "planning.unit_ordinal",
                    "拍摄单元序号不连续。",
                    _ALL_LEVELS,
                    scene.id,
                )
            )
        cursor = scene.source_span.line_start
        previous: ShootingUnit | None = None
        for unit in units:
            shared_single_line = (
                previous is not None
                and previous.source_span == unit.source_span
                and unit.source_span.line_start == unit.source_span.line_end
            )
            if unit.source_span.line_start != cursor and not shared_single_line:
                issues.append(
                    planning_issue(
                        "planning.unit_span",
                        "拍摄单元存在空档或非法交叉。",
                        _ALL_LEVELS,
                        unit.id,
                    )
                )
            cursor = max(cursor, unit.source_span.line_end + 1)
            if any(not evidence_is_located(scene, item) for item in unit.evidence):
                issues.append(
                    planning_issue(
                        "planning.unit_evidence",
                        "拍摄单元证据无法逐字定位。",
                        _ALL_LEVELS,
                        unit.id,
                    )
                )
            previous = unit
        if cursor != scene.source_span.line_end + 1:
            issues.append(
                planning_issue(
                    "planning.unit_coverage",
                    "拍摄单元没有覆盖场景全部原文行。",
                    _ALL_LEVELS,
                    scene.id,
                )
            )

    @staticmethod
    def _validate_occurrences(
        breakdown: ProductionBreakdown,
        plan: ProductionPlan,
        issues: list[ProductionPlanningIssue],
    ) -> None:
        """检查需求出现项完整性以及单元和资源双向引用。"""
        expected = {f"{unit.id}/setting" for unit in plan.shooting_units}
        expected.update(
            f"{analysis.scene_id}/{item.id}"
            for analysis in breakdown.scenes
            for item in [*analysis.cast, *analysis.background, *analysis.elements]
        )
        actual = {item.source_requirement_id for item in plan.occurrences}
        for reference in sorted(expected - actual):
            issues.append(
                planning_issue(
                    "planning.occurrence_missing",
                    "制片需求没有资源出现项。",
                    _ALL_LEVELS,
                    reference,
                )
            )
        for reference in sorted(actual - expected):
            issues.append(
                planning_issue(
                    "planning.occurrence_unknown",
                    "资源出现项引用未知需求。",
                    _ALL_LEVELS,
                    reference,
                )
            )
        units = {item.id: item for item in plan.shooting_units}
        classes = {item.id for item in plan.resource_classes}
        by_unit: dict[str, list[str]] = defaultdict(list)
        for item in plan.occurrences:
            if item.shooting_unit_id not in units or item.resource_class_id not in classes:
                issues.append(
                    planning_issue(
                        "planning.occurrence_ref",
                        "出现项引用未知单元或资源类别。",
                        _ALL_LEVELS,
                        item.id,
                    )
                )
            by_unit[item.shooting_unit_id].append(item.id)
        for unit in plan.shooting_units:
            if unit.occurrence_ids != by_unit[unit.id]:
                issues.append(
                    planning_issue(
                        "planning.unit_occurrences",
                        "单元出现项双向引用不一致。",
                        _ALL_LEVELS,
                        unit.id,
                    )
                )
