"""大群体和未成年人强制安全候选规则测试。"""

from movie_breakdown.application.production_planning import ProductionPlanBuilder
from movie_breakdown.domain.base import Confidence
from movie_breakdown.domain.production_common import (
    ProductionElementKind,
    QuantityBasis,
    QuantityEstimate,
    RequirementBasis,
)
from movie_breakdown.domain.production_safety import HazardKind
from movie_breakdown.domain.production_scene import BackgroundRequirement, ProductionElement
from tests.factories import make_screenplay
from tests.production_factories import make_production_breakdown


def test_background_count_of_fifty_forces_crowd_action_review() -> None:
    screenplay = make_screenplay()
    breakdown = make_production_breakdown(screenplay)
    analysis = breakdown.scenes[0]
    analysis.background.append(_background(60, analysis.cast[0].evidence))

    plan = ProductionPlanBuilder().build(screenplay, breakdown)
    hazard = next(item for item in plan.safety_hazards if item.kind == HazardKind.CROWD_ACTION)

    assert set(hazard.required_reviewer_roles) == {
        "群演协调",
        "动作指导",
        "现场安全负责人",
    }
    assert "无分区的大群体真实踩踏" in hazard.prohibited_methods


def test_small_static_background_does_not_trigger_crowd_action() -> None:
    screenplay = make_screenplay()
    breakdown = make_production_breakdown(screenplay)
    analysis = breakdown.scenes[0]
    analysis.background.append(_background(49, analysis.cast[0].evidence))

    plan = ProductionPlanBuilder().build(screenplay, breakdown)

    assert all(item.kind != HazardKind.CROWD_ACTION for item in plan.safety_hazards)


def test_minor_performer_term_forces_welfare_and_safety_review() -> None:
    screenplay = make_screenplay()
    breakdown = make_production_breakdown(screenplay)
    breakdown.scenes[0].cast[0].performance_notes = ["由儿童演员完成安静站立表演。"]

    plan = ProductionPlanBuilder().build(screenplay, breakdown)
    hazard = next(item for item in plan.safety_hazards if item.kind == HazardKind.MINOR_PERFORMER)

    assert set(hazard.required_reviewer_roles) == {
        "未成年人协调",
        "监护与福利负责人",
        "现场安全负责人",
    }
    assert any("高危动作范围" in item for item in hazard.prohibited_methods)


def test_ordinary_makeup_white_hair_and_warming_effect_do_not_create_hazards() -> None:
    """验证元素类别本身不会把普通妆发或取暖效果升级为高危。"""
    screenplay = make_screenplay()
    breakdown = make_production_breakdown(screenplay)
    analysis = breakdown.scenes[0]
    evidence = analysis.cast[0].evidence
    analysis.elements.extend(
        [
            _element(
                "element-white-hair",
                ProductionElementKind.HAIR_MAKEUP,
                "老年白发造型",
                "满头白发的普通人物造型。",
                evidence,
            ),
            _element(
                "element-warming",
                ProductionElementKind.PRACTICAL_EFFECT,
                "雪地取暖气氛",
                "演员围坐取暖并呈现低温呼吸效果。",
                evidence,
            ),
        ]
    )

    plan = ProductionPlanBuilder().build(screenplay, breakdown)
    kinds = {item.kind for item in plan.safety_hazards if item.scene_id == analysis.scene_id}

    assert HazardKind.PROSTHETIC_GORE not in kinds
    assert HazardKind.PYROTECHNICS not in kinds
    assert HazardKind.OPEN_FLAME not in kinds


def test_injury_makeup_and_muzzle_explosion_semantics_still_create_hazards() -> None:
    """验证收紧类别规则后，真实伤效与烟火语义仍强制进入复核。"""
    screenplay = make_screenplay()
    breakdown = make_production_breakdown(screenplay)
    analysis = breakdown.scenes[0]
    evidence = analysis.cast[0].evidence
    analysis.elements.extend(
        [
            _element(
                "element-gore",
                ProductionElementKind.HAIR_MAKEUP,
                "断肢血浆伤效",
                "使用断臂假体和血浆表现伤口。",
                evidence,
            ),
            _element(
                "element-pyro",
                ProductionElementKind.PRACTICAL_EFFECT,
                "枪口火焰与弹着爆炸",
                "表现枪口火焰、弹着和爆炸。",
                evidence,
            ),
        ]
    )

    plan = ProductionPlanBuilder().build(screenplay, breakdown)
    kinds = {item.kind for item in plan.safety_hazards if item.scene_id == analysis.scene_id}

    assert HazardKind.PROSTHETIC_GORE in kinds
    assert HazardKind.PYROTECHNICS in kinds
    assert HazardKind.OPEN_FLAME in kinds


def _background(count: int, evidence) -> BackgroundRequirement:
    """构造没有动作关键词、仅由结构化人数决定的群演需求。"""
    return BackgroundRequirement(
        id=f"background-static-{count}",
        group_name="静态村民",
        description="村民在远处安静站立。",
        quantity=QuantityEstimate(
            minimum=count,
            maximum=count,
            unit="人",
            basis=QuantityBasis.EXACT,
        ),
        special_skills=[],
        basis=RequirementBasis.EXPLICIT,
        confidence=Confidence.HIGH,
        evidence=evidence,
    )


def _element(
    identifier: str,
    kind: ProductionElementKind,
    name: str,
    description: str,
    evidence,
) -> ProductionElement:
    """构造只用于确定性安全规则测试的制片元素。"""
    return ProductionElement(
        id=identifier,
        kind=kind,
        name=name,
        description=description,
        quantity=QuantityEstimate(
            minimum=None,
            maximum=None,
            unit="项",
            basis=QuantityBasis.UNKNOWN,
        ),
        basis=RequirementBasis.EXPLICIT,
        confidence=Confidence.HIGH,
        evidence=evidence,
    )
