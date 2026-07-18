"""制片资源规划使用的结果类型与保守辅助规则。"""

from __future__ import annotations

from dataclasses import dataclass

from movie_breakdown.domain.production_common import ProductionElementKind
from movie_breakdown.domain.production_planning import (
    IdentityScope,
    ProductionEntity,
    ProductionResourceClass,
    QuantityFact,
    ResourceOccurrence,
    ShootingUnit,
)
from movie_breakdown.domain.production_scene import ProductionElement

_CONTINUITY_TERMS = ("手表", "笔记本", "臂章", "勋章", "日记", "遗物", "老罗", "骡子")


@dataclass(frozen=True, slots=True)
class ProductionResourcePlanResult:
    """确定性资源规划的各类强类型结果。

    Attributes:
        resource_classes: 规范名称与标准单位一致的资源类别。
        entities: 跨场连续性候选实体。
        occurrences: 资源在具体拍摄单元中的出现项。
        quantity_facts: 与制作计划分离的剧本数量事实。
        shooting_units: 已回填出现项引用的拍摄单元。
    """

    resource_classes: list[ProductionResourceClass]
    entities: list[ProductionEntity]
    occurrences: list[ResourceOccurrence]
    quantity_facts: list[QuantityFact]
    shooting_units: list[ShootingUnit]


def unit_for_line(units: list[ShootingUnit], line: int) -> ShootingUnit:
    """返回覆盖指定证据行的单元，异常输入保守附着首单元。

    Args:
        units: 当前场景按顺序排列的全部拍摄单元。
        line: 资源第一条证据的全局行号。

    Returns:
        覆盖证据行的单元；异常行号使用首单元等待后续校验阻断。
    """
    return next(
        (
            unit
            for unit in units
            if unit.source_span.line_start <= line <= unit.source_span.line_end
        ),
        units[0],
    )


def identity_scope(item: ProductionElement) -> IdentityScope:
    """保守识别动物、具名英雄道具和有状态变化的连续性资源。

    Args:
        item: 当前逐场制片元素。

    Returns:
        该资源应视为可替代类别还是连续性实体。
    """
    if item.kind == ProductionElementKind.ANIMAL:
        return IdentityScope.CONTINUITY
    text = f"{item.name} {item.state_or_continuity or ''}"
    if item.state_or_continuity or any(term in text for term in _CONTINUITY_TERMS):
        return IdentityScope.CONTINUITY
    return IdentityScope.FUNGIBLE
