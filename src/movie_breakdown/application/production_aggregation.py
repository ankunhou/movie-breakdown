"""从逐场结果保守、确定性地构建全剧制片目录。"""

from __future__ import annotations

from collections import defaultdict
from typing import Protocol

from movie_breakdown.application.production_aggregation_support import (
    display_text,
    normalize_catalog_key,
    stable_catalog_id,
    unique_evidence,
    unique_texts,
    unique_values,
)
from movie_breakdown.application.production_resource_aggregation import (
    build_background_catalog,
    build_element_catalog,
)
from movie_breakdown.domain.production_catalog import (
    ComplexScene,
    GlobalProductionCatalog,
    ProductionCast,
    ProductionLocation,
)
from movie_breakdown.domain.production_scene import (
    CastRequirement,
    SceneProductionAnalysis,
    SceneSetting,
)
from movie_breakdown.domain.source import Screenplay


class ProductionCatalogBuilder(Protocol):
    """全剧制片目录的可替换构建策略。"""

    def build(
        self,
        screenplay: Screenplay,
        analyses: list[SceneProductionAnalysis],
    ) -> GlobalProductionCatalog:
        """把完整逐场结果转换为确定性总表。

        Args:
            screenplay: 提供合法场景集合和稳定顺序的共享剧本。
            analyses: 要求完整覆盖剧本的逐场制片结果。

        Returns:
            可由逐场来源完整反查的全剧制片目录。

        Raises:
            ValueError: 逐场结果重复、缺失或引用未知场景。
        """
        ...


class ConservativeProductionCatalogBuilder:
    """只按最小文本规范键合并、不猜测同义实体的目录策略。"""

    def build(
        self,
        screenplay: Screenplay,
        analyses: list[SceneProductionAnalysis],
    ) -> GlobalProductionCatalog:
        """构建地点、演员、群演、元素和高复杂度场景索引。

        Args:
            screenplay: 提供合法场景集合和稳定顺序的共享剧本。
            analyses: 要求完整覆盖剧本的逐场制片结果。

        Returns:
            输入顺序变化时内容仍一致的保守制片目录。

        Raises:
            ValueError: 逐场结果重复、缺失或引用未知场景。
        """
        ordered = self._ordered_analyses(screenplay, analyses)
        scene_order = {scene.id: scene.ordinal for scene in screenplay.scenes}
        return GlobalProductionCatalog(
            locations=self._locations(ordered, scene_order),
            cast=self._cast(ordered, scene_order),
            background=build_background_catalog(ordered, scene_order),
            elements=build_element_catalog(ordered, scene_order),
            complex_scenes=self._complex_scenes(ordered),
        )

    def _ordered_analyses(
        self,
        screenplay: Screenplay,
        analyses: list[SceneProductionAnalysis],
    ) -> list[SceneProductionAnalysis]:
        """拒绝非全覆盖输入并恢复剧本场景顺序。"""
        expected = [scene.id for scene in screenplay.scenes]
        actual = [analysis.scene_id for analysis in analyses]
        if len(actual) != len(set(actual)):
            raise ValueError("制片目录不能接收重复场景分析。")
        unknown = sorted(set(actual) - set(expected))
        if unknown:
            raise ValueError(f"制片目录包含未知场景：{', '.join(unknown)}")
        missing = sorted(set(expected) - set(actual))
        if missing:
            raise ValueError(f"制片目录缺少场景分析：{', '.join(missing)}")
        indexed = {analysis.scene_id: analysis for analysis in analyses}
        return [indexed[scene_id] for scene_id in expected]

    def _locations(
        self,
        analyses: list[SceneProductionAnalysis],
        scene_order: dict[str, int],
    ) -> list[ProductionLocation]:
        """按地点与子地点严格合并拍摄设置。"""
        groups: dict[tuple[str, str], list[tuple[str, SceneSetting]]] = defaultdict(list)
        for analysis in analyses:
            setting = analysis.setting
            key = (
                normalize_catalog_key(setting.location_name),
                normalize_catalog_key(setting.sub_location),
            )
            groups[key].append((analysis.scene_id, setting))
        result: list[ProductionLocation] = []
        for key in sorted(groups):
            entries = groups[key]
            names = unique_texts(self._location_name(setting) for _, setting in entries)
            result.append(
                ProductionLocation(
                    id=stable_catalog_id("location", key),
                    name=names[0],
                    aliases=names[1:],
                    scene_ids=unique_values(scene_id for scene_id, _ in entries),
                    interior_exterior_modes=unique_values(
                        setting.interior_exterior for _, setting in entries
                    ),
                    time_of_day_modes=unique_values(setting.time_of_day for _, setting in entries),
                    weather_requirements=unique_texts(
                        value for _, setting in entries for value in setting.weather_requirements
                    ),
                    evidence=unique_evidence(
                        (item for _, setting in entries for item in setting.evidence),
                        scene_order,
                    ),
                )
            )
        return result

    def _cast(
        self,
        analyses: list[SceneProductionAnalysis],
        scene_order: dict[str, int],
    ) -> list[ProductionCast]:
        """按角色 ID 优先、无 ID 姓名保守附着的规则汇总演员。"""
        name_ids: dict[str, set[str]] = defaultdict(set)
        for analysis in analyses:
            for item in analysis.cast:
                if item.character_id:
                    name_ids[normalize_catalog_key(item.character_name)].add(
                        normalize_catalog_key(item.character_id)
                    )
        groups: dict[tuple[str, str], list[tuple[str, CastRequirement]]] = defaultdict(list)
        for analysis in analyses:
            for item in analysis.cast:
                name_key = normalize_catalog_key(item.character_name)
                ids = name_ids[name_key]
                if item.character_id:
                    key = ("id", normalize_catalog_key(item.character_id))
                elif len(ids) == 1:
                    key = ("id", next(iter(ids)))
                else:
                    key = ("name", name_key)
                groups[key].append((analysis.scene_id, item))
        result: list[ProductionCast] = []
        for key in sorted(groups):
            entries = groups[key]
            names = unique_texts(item.character_name for _, item in entries)
            character_ids = unique_texts(item.character_id for _, item in entries)
            result.append(
                ProductionCast(
                    id=stable_catalog_id("cast", key),
                    name=names[0],
                    aliases=names[1:],
                    character_id=character_ids[0] if character_ids else None,
                    scene_ids=unique_values(scene_id for scene_id, _ in entries),
                    appearance_kinds=unique_values(item.appearance_kind for _, item in entries),
                    source_requirement_ids=[f"{scene_id}/{item.id}" for scene_id, item in entries],
                    evidence=unique_evidence(
                        (evidence for _, item in entries for evidence in item.evidence),
                        scene_order,
                    ),
                )
            )
        return result

    def _complex_scenes(
        self,
        analyses: list[SceneProductionAnalysis],
    ) -> list[ComplexScene]:
        """保留剧本顺序中评分四级以上的复杂场景。"""
        return [
            ComplexScene(
                scene_id=analysis.scene_id,
                score=analysis.complexity.score,
                level=analysis.complexity.level,
                dimensions=unique_values(
                    factor.dimension for factor in analysis.complexity.factors
                ),
            )
            for analysis in analyses
            if analysis.complexity.score >= 4
        ]

    @staticmethod
    def _location_name(setting: SceneSetting) -> str:
        """组合地点和可选子地点供首版目录清晰展示。"""
        parts = [setting.location_name, setting.sub_location]
        return " / ".join(display_text(part) for part in parts if part)
