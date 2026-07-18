"""把模型数量安全降格为剧本事实并规范原始单位。"""

from __future__ import annotations

import re
import unicodedata

from movie_breakdown.domain.production_common import (
    ProductionElementKind,
    QuantityBasis,
    RequirementBasis,
)
from movie_breakdown.domain.production_planning import (
    QuantityBounds,
    QuantityFact,
    QuantityProvenance,
    QuantityRole,
    ResourceOccurrence,
    UnitCode,
)
from movie_breakdown.domain.production_scene import (
    BackgroundRequirement,
    ProductionElement,
)

_PERSON_UNITS = {"人", "名", "位", "人次", "person", "persons", "people", "角色"}
_ANIMAL_UNITS = {"匹", "头", "只"}
_VEHICLE_UNITS = {"辆", "台", "架", "部", "列"}
_PAIR_UNITS = {"双", "副", "pair", "pairs"}
_SET_UNITS = {"套", "组", "set", "sets", "捆", "包"}
_EVENT_UNITS = {"次", "场", "段", "sequence", "cue", "effect", "effects", "效果", "项"}
_SHOT_UNITS = {"镜头", "组镜头", "shot", "shots"}
_LOCATION_UNITS = {"场景", "处", "间", "座"}
_ITEM_UNITS = {
    "个",
    "件",
    "把",
    "本",
    "顶",
    "堆",
    "份",
    "幅",
    "根",
    "管",
    "罐",
    "盒",
    "具",
    "卷",
    "棵",
    "颗",
    "块",
    "枚",
    "门",
    "面",
    "捧",
    "片",
    "条",
    "挺",
    "碗",
    "张",
    "支",
    "种",
    "item",
    "items",
    "piece",
    "pieces",
}
_WEAPON_TERMS = re.compile(r"枪|炮|雷|弹|刀|剑|匕首|刺刀|武器")
_CHINESE_NUMBER = re.compile(r"[零〇一二两三四五六七八九十百千]+")
_ELEMENT_KIND_UNITS = {
    ProductionElementKind.ANIMAL: UnitCode.ANIMAL,
    ProductionElementKind.VEHICLE: UnitCode.VEHICLE,
    ProductionElementKind.COSTUME: UnitCode.COSTUME,
    ProductionElementKind.HAIR_MAKEUP: UnitCode.SET,
    ProductionElementKind.SET_DRESSING: UnitCode.SET,
    ProductionElementKind.STUNT_ACTION: UnitCode.EVENT,
    ProductionElementKind.PRACTICAL_EFFECT: UnitCode.EVENT,
    ProductionElementKind.SOUND_MUSIC: UnitCode.EVENT,
    ProductionElementKind.SPECIAL_EQUIPMENT: UnitCode.DEVICE,
}


class QuantityFactBuilder:
    """从逐场需求构建不冒充实拍或采购计划的数量事实。"""

    def build(
        self,
        occurrence: ResourceOccurrence,
        requirement: BackgroundRequirement | ProductionElement,
    ) -> QuantityFact:
        """规范单位并只保留由逐字证据支持的上下界。

        Args:
            occurrence: 数量事实所属的资源出现项。
            requirement: 原始群演或制片元素需求。

        Returns:
            不会把模型估算转换为剧本事实的数量记录。
        """
        quantity = requirement.quantity
        unit = normalize_unit(
            quantity.unit,
            element_kind=requirement.kind if isinstance(requirement, ProductionElement) else None,
            name=(
                requirement.name
                if isinstance(requirement, ProductionElement)
                else requirement.group_name
            ),
        )
        supported = (
            requirement.basis == RequirementBasis.EXPLICIT
            and quantity.basis not in {QuantityBasis.ESTIMATED, QuantityBasis.UNKNOWN}
            and quantity_values_are_supported(
                quantity.minimum,
                quantity.maximum,
                requirement.evidence,
            )
        )
        bounds = (
            QuantityBounds(minimum=quantity.minimum, maximum=quantity.maximum)
            if supported
            else QuantityBounds()
        )
        provenance = QuantityProvenance.EXPLICIT_TEXT if supported else QuantityProvenance.UNKNOWN
        return QuantityFact(
            id=f"{occurrence.id}/quantity-001",
            occurrence_id=occurrence.id,
            bounds=bounds,
            unit=unit,
            raw_unit=quantity.unit,
            raw_expression=_raw_expression(requirement, supported),
            role=_quantity_role(requirement, unit),
            provenance=provenance,
            evidence=requirement.evidence,
        )


def normalize_unit(
    raw_unit: str,
    *,
    element_kind: ProductionElementKind | None,
    name: str,
) -> UnitCode:
    """结合资源类别把自由文本单位映射为稳定代码。

    Args:
        raw_unit: 模型保留的原始单位。
        element_kind: 元素类别；群演数量没有此值。
        name: 资源显示名称，用于区分武器等上下文单位。

    Returns:
        可确定比较的标准单位；无法安全映射时返回 ``unknown``。
    """
    normalized = unicodedata.normalize("NFKC", raw_unit).casefold().strip()
    if element_kind is None:
        return UnitCode.PERSON
    forced = _ELEMENT_KIND_UNITS.get(element_kind)
    if forced is not None:
        return forced
    if element_kind == ProductionElementKind.VFX:
        return UnitCode.SHOT if normalized in _SHOT_UNITS else UnitCode.EVENT
    if element_kind == ProductionElementKind.HAND_PROP and _WEAPON_TERMS.search(name):
        return UnitCode.WEAPON
    if normalized in _PAIR_UNITS:
        return UnitCode.PAIR
    if normalized in _SET_UNITS:
        return UnitCode.SET
    if normalized in _EVENT_UNITS:
        return UnitCode.EVENT
    if normalized in _SHOT_UNITS:
        return UnitCode.SHOT
    if normalized in _LOCATION_UNITS:
        return UnitCode.LOCATION
    if normalized in _ITEM_UNITS | _ANIMAL_UNITS | _VEHICLE_UNITS:
        return UnitCode.ITEM
    return UnitCode.UNKNOWN


def _quantity_role(
    requirement: BackgroundRequirement | ProductionElement,
    unit: UnitCode,
) -> QuantityRole:
    """根据需求类型区分画面规模、事件次数和资源总量。"""
    if isinstance(requirement, BackgroundRequirement):
        return QuantityRole.SCREEN_SCALE
    if unit in {UnitCode.EVENT, UnitCode.SHOT}:
        return QuantityRole.EVENT
    return QuantityRole.TOTAL


def quantity_values_are_supported(
    minimum: int | None,
    maximum: int | None,
    evidence,
) -> bool:
    """检查数量上下界是否直接出现在证据正文中。

    Args:
        minimum: 待验证的数量下界。
        maximum: 可选的数量上界。
        evidence: 包含逐字摘录的证据序列。

    Returns:
        下界及已知上界都能从阿拉伯或常见中文数字解析时为真。
    """
    if minimum is None:
        return False
    numbers: set[int] = set()
    for item in evidence:
        numbers.update(int(value) for value in re.findall(r"\d+", item.excerpt))
        numbers.update(
            value
            for token in _CHINESE_NUMBER.findall(item.excerpt)
            if (value := _parse_chinese_number(token)) is not None
        )
    return minimum in numbers and (maximum is None or maximum in numbers)


def _parse_chinese_number(token: str) -> int | None:
    """解析一千以内常见中文整数，无法确认时返回空值。"""
    digits = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    units = {"十": 10, "百": 100, "千": 1000}
    if all(character in digits for character in token):
        if len(token) == 1:
            return digits[token]
        return None
    total = 0
    current = 0
    for character in token:
        if character in digits:
            current = digits[character]
        elif character in units:
            total += (current or 1) * units[character]
            current = 0
        else:
            return None
    return total + current


def _raw_expression(
    requirement: BackgroundRequirement | ProductionElement,
    supported: bool,
) -> str:
    """披露保留或拒绝模型数量的原因，避免丢失审计上下文。"""
    quantity = requirement.quantity
    bounds = (
        "未知"
        if quantity.minimum is None
        else str(quantity.minimum)
        if quantity.maximum in {None, quantity.minimum}
        else f"{quantity.minimum}-{quantity.maximum}"
    )
    if supported:
        return f"原文证据支持 {bounds} {quantity.unit}。"
    return f"模型曾给出 {bounds} {quantity.unit}，未作为可执行剧本数量采信。"
