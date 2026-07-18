"""编排拍摄单元、资源身份、数量事实和高危候选的本地规划。"""

from __future__ import annotations

from movie_breakdown.application.production_resources import ProductionResourcePlanner
from movie_breakdown.application.production_safety import ProductionSafetyDetector
from movie_breakdown.application.production_units import (
    DeterministicShootingUnitBuilder,
    ShootingUnitBuilder,
)
from movie_breakdown.domain.production_catalog import ProductionBreakdown
from movie_breakdown.domain.production_planning import ProductionPlan
from movie_breakdown.domain.source import Screenplay
from movie_breakdown.infrastructure.fingerprint import content_fingerprint


class ProductionPlanBuildError(ValueError):
    """基础拆解或共享场景不能安全生成制片规划。"""


class ProductionPlanBuilder:
    """使用可替换策略从现有模型产物生成完全本地的制片规划。"""

    def __init__(
        self,
        unit_builder: ShootingUnitBuilder | None = None,
        resource_planner: ProductionResourcePlanner | None = None,
        safety_detector: ProductionSafetyDetector | None = None,
    ) -> None:
        """创建规划构建器。

        Args:
            unit_builder: 可替换的场内拍摄单元策略。
            resource_planner: 可替换的资源与数量规划器。
            safety_detector: 可替换的本地安全候选检测器。
        """
        self._unit_builder = unit_builder or DeterministicShootingUnitBuilder()
        self._resource_planner = resource_planner or ProductionResourcePlanner()
        self._safety_detector = safety_detector or ProductionSafetyDetector()

    def build(
        self,
        screenplay: Screenplay,
        breakdown: ProductionBreakdown,
    ) -> ProductionPlan:
        """从已验证制片拆解派生拍摄规划，不调用模型。

        Args:
            screenplay: 提供原文、稳定行号和场景顺序的共享剧本。
            breakdown: 当前正式制片元素底稿。

        Returns:
            绑定基础拆解指纹且尚未冒充人工计划或安全批准的规划。

        Raises:
            ProductionPlanBuildError: 来源、覆盖或基础校验不满足安全派生条件。
        """
        self._validate_inputs(screenplay, breakdown)
        analyses = {item.scene_id: item for item in breakdown.scenes}
        shooting_units = [
            unit
            for scene in screenplay.scenes
            for unit in self._unit_builder.build(scene, analyses[scene.id])
        ]
        resources = self._resource_planner.build(breakdown.scenes, shooting_units)
        hazards = self._safety_detector.detect(
            breakdown.scenes,
            resources.occurrences,
            resources.shooting_units,
        )
        return ProductionPlan(
            source_fingerprint=screenplay.source_fingerprint,
            base_breakdown_fingerprint=content_fingerprint(breakdown),
            shooting_units=resources.shooting_units,
            resource_classes=resources.resource_classes,
            entities=resources.entities,
            occurrences=resources.occurrences,
            quantity_facts=resources.quantity_facts,
            planned_quantities=[],
            safety_hazards=hazards,
            safety_method_decisions=[],
            safety_approvals=[],
        )

    @staticmethod
    def _validate_inputs(screenplay: Screenplay, breakdown: ProductionBreakdown) -> None:
        """拒绝来源不一致、覆盖不完整或未通过校验的基础输入。"""
        if screenplay.source_fingerprint != breakdown.source_fingerprint:
            raise ProductionPlanBuildError("共享场景与制片拆解的来源指纹不一致。")
        if not breakdown.validation.valid:
            raise ProductionPlanBuildError("制片一致性校验未通过，不能生成规划。")
        expected = [scene.id for scene in screenplay.scenes]
        actual = [analysis.scene_id for analysis in breakdown.scenes]
        if actual != expected:
            raise ProductionPlanBuildError("制片逐场结果没有按共享剧本顺序完整覆盖。")
