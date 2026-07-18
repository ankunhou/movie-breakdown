"""制片元素拆解测试使用的三场确定性工厂。"""

from movie_breakdown.application.production_aggregation import (
    ConservativeProductionCatalogBuilder,
)
from movie_breakdown.domain.base import Confidence, StageStatus
from movie_breakdown.domain.production_catalog import (
    GlobalProductionCatalog,
    ProductionBreakdown,
    ProductionValidationReport,
)
from movie_breakdown.domain.production_common import (
    CastAppearanceKind,
    ComplexityDimension,
    ComplexityLevel,
    DayPhase,
    InteriorExterior,
    ProductionElementKind,
    QuantityBasis,
    QuantityEstimate,
    RequirementBasis,
)
from movie_breakdown.domain.production_scene import (
    CastRequirement,
    ComplexityFactor,
    ProductionElement,
    SceneProductionAnalysis,
    SceneProductionComplexity,
    SceneProductionRecord,
    SceneSetting,
)
from movie_breakdown.domain.scene_analysis import Evidence
from movie_breakdown.domain.source import Scene, Screenplay, SourceSpan
from tests.factories import make_screenplay


def scene_evidence(scene: Scene, *, heading: bool = False) -> Evidence:
    """为测试场景构造可逐字定位的标题或动作证据。

    Args:
        scene: 证据所属场景。
        heading: 是否引用标题行；否则引用最后一行动作。

    Returns:
        行号与原文完全匹配的高置信度证据。
    """
    lines = scene.text.splitlines()
    line_number = scene.source_span.line_start if heading else scene.source_span.line_end
    excerpt = lines[0] if heading else lines[-1]
    return Evidence(
        scene_id=scene.id,
        source_span=SourceSpan(line_start=line_number, line_end=line_number),
        excerpt=excerpt,
        confidence=Confidence.HIGH,
    )


def make_production_analysis(scene: Scene) -> SceneProductionAnalysis:
    """构造与一个测试场景严格匹配的制片拆解。

    Args:
        scene: 待拆解的测试场景。

    Returns:
        包含设置、演员、可选车辆及复杂度的逐场结果。
    """
    heading_evidence = scene_evidence(scene, heading=True)
    action_evidence = scene_evidence(scene)
    tokens = scene.heading.split()
    time_of_day = DayPhase.NIGHT if "夜" in scene.heading else DayPhase.DAY
    interior_exterior = (
        InteriorExterior.INTERIOR if "内" in scene.heading else InteriorExterior.EXTERIOR
    )
    cast = CastRequirement(
        id="cast-xiaowang",
        character_name="小王",
        character_id=None,
        appearance_kind=CastAppearanceKind.ON_SCREEN,
        performance_notes=[],
        basis=RequirementBasis.EXPLICIT,
        confidence=Confidence.HIGH,
        evidence=[action_evidence],
    )
    elements: list[ProductionElement] = []
    factors: list[ComplexityFactor] = []
    score = 1
    level = ComplexityLevel.LOW
    if scene.id == "scene-0003":
        elements.append(
            ProductionElement(
                id="element-train",
                kind=ProductionElementKind.VEHICLE,
                name="列车",
                description="需要呈现小王乘坐的列车。",
                quantity=QuantityEstimate(
                    minimum=1,
                    maximum=1,
                    unit="列",
                    basis=QuantityBasis.EXACT,
                ),
                associated_cast_ids=[cast.id],
                special_requirements=["运动中的列车场景"],
                basis=RequirementBasis.EXPLICIT,
                confidence=Confidence.HIGH,
                evidence=[action_evidence],
            )
        )
        factors.append(
            ComplexityFactor(
                dimension=ComplexityDimension.LOCATION_LOGISTICS,
                score=4,
                rationale="列车运动与演员表演需要协同。",
                related_requirement_ids=["element-train"],
                evidence=[action_evidence],
            )
        )
        score = 4
        level = ComplexityLevel.HIGH
    return SceneProductionAnalysis(
        scene_id=scene.id,
        setting=SceneSetting(
            raw_heading=scene.heading,
            location_name=tokens[0],
            interior_exterior=interior_exterior,
            time_of_day=time_of_day,
            raw_time_label="夜" if time_of_day == DayPhase.NIGHT else "日",
            weather_requirements=[],
            basis=RequirementBasis.EXPLICIT,
            confidence=Confidence.HIGH,
            evidence=[heading_evidence],
        ),
        cast=[cast],
        background=[],
        elements=elements,
        complexity=SceneProductionComplexity(
            score=score,
            level=level,
            factors=factors,
            scheduling_notes=[],
        ),
        uncertainties=[],
    )


def make_production_records(
    screenplay: Screenplay | None = None,
) -> list[SceneProductionRecord]:
    """构造覆盖测试剧本全部场景的成功记录。

    Args:
        screenplay: 可选的测试剧本；省略时使用默认三场剧本。

    Returns:
        顺序与剧本一致的制片逐场记录。
    """
    target = screenplay or make_screenplay()
    return [
        SceneProductionRecord(
            scene_id=scene.id,
            cache_key=f"production-cache-{scene.ordinal}",
            status=StageStatus.SUCCESS,
            analysis=make_production_analysis(scene),
            attempts=1,
        )
        for scene in target.scenes
    ]


def make_production_catalog(
    screenplay: Screenplay | None = None,
) -> GlobalProductionCatalog:
    """构造与默认逐场制片记录完全一致的总表。

    Args:
        screenplay: 可选的测试剧本；省略时使用默认三场剧本。

    Returns:
        覆盖地点、演员、列车及高复杂度场景的目录。
    """
    target = screenplay or make_screenplay()
    return ConservativeProductionCatalogBuilder().build(
        target,
        [make_production_analysis(scene) for scene in target.scenes],
    )


def make_production_breakdown(
    screenplay: Screenplay | None = None,
) -> ProductionBreakdown:
    """构造已经通过基础一致性校验的完整制片拆解。

    Args:
        screenplay: 可选的测试剧本；省略时使用默认三场剧本。

    Returns:
        可用于规划层本地派生的基础制片拆解。
    """
    target = screenplay or make_screenplay()
    records = make_production_records(target)
    catalog = make_production_catalog(target)
    return ProductionBreakdown(
        title=target.title,
        source_fingerprint=target.source_fingerprint,
        scenes=[record.analysis for record in records if record.analysis is not None],
        catalog=catalog,
        validation=ProductionValidationReport(
            valid=True,
            scene_count=len(target.scenes),
            analyzed_scene_count=len(target.scenes),
            coverage=1,
            catalog_item_count=sum(
                len(items)
                for items in (
                    catalog.locations,
                    catalog.cast,
                    catalog.background,
                    catalog.elements,
                )
            ),
            issues=[],
        ),
    )
