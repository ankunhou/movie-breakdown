"""确定性制片高危候选使用的受控规则表。"""

from __future__ import annotations

from dataclasses import dataclass

from movie_breakdown.domain.production_common import ProductionElementKind
from movie_breakdown.domain.production_safety import (
    HazardKind,
    SafetyRiskLevel,
)


@dataclass(frozen=True, slots=True)
class SafetyHazardRule:
    """一个稳定的结构化类别与关键词并集规则。"""

    id: str
    kind: HazardKind
    level: SafetyRiskLevel
    terms: tuple[str, ...]
    element_kinds: tuple[ProductionElementKind, ...]
    roles: tuple[str, ...]
    controls: tuple[str, ...]
    prohibited: tuple[str, ...] = ()


SAFETY_HAZARD_RULES = (
    SafetyHazardRule(
        "safety.firearm.v1",
        HazardKind.FIREARM,
        SafetyRiskLevel.CRITICAL,
        ("枪", "射击", "开火", "中弹", "子弹", "机炮"),
        (),
        ("枪械负责人", "动作指导", "现场安全负责人"),
        ("使用受控道具枪或非发火复制枪", "建立枪口方向与人员隔离方案", "逐镜头核定弹着和声音实现"),
        ("片场实弹", "无人监管的可发火枪械"),
    ),
    SafetyHazardRule(
        "safety.pyrotechnics.v1",
        HazardKind.PYROTECHNICS,
        SafetyRiskLevel.CRITICAL,
        (
            "爆炸",
            "爆破",
            "手雷",
            "地雷",
            "炮弹",
            "炸弹",
            "弹着",
            "烟火",
            "枪火",
            "枪口火焰",
        ),
        (),
        ("现场特效负责人", "现场安全负责人", "动作指导"),
        ("由持证团队制定装药与安全距离", "预留假体、机械或视效替代方案", "逐次记录重置与点火许可"),
        ("演员处于未经隔离的爆点", "无专业人员操作烟火"),
    ),
    SafetyHazardRule(
        "safety.open_flame.v1",
        HazardKind.OPEN_FLAME,
        SafetyRiskLevel.CRITICAL,
        ("燃烧", "火海", "着火", "明火", "火焰"),
        (),
        ("现场特效负责人", "消防安全负责人", "动作指导"),
        ("载人表演与燃烧景片分离", "配置快速解脱、灭火和医疗预案", "核定防火材料与风向"),
        ("演员与不可控明火直接接触",),
    ),
    SafetyHazardRule(
        "safety.blade.v1",
        HazardKind.BLADE_COMBAT,
        SafetyRiskLevel.HIGH,
        ("匕首", "刺刀", "刀刺", "劈砍", "拼刺", "割下"),
        (),
        ("动作指导", "道具负责人", "现场安全负责人"),
        ("近身表演使用软质或可收缩复制件", "真刃与表演道具分区保管", "按镜头排练接触距离"),
        ("真刀接触表演者",),
    ),
    SafetyHazardRule(
        "safety.vehicle.v1",
        HazardKind.VEHICLE_ACTION,
        SafetyRiskLevel.HIGH,
        ("驾驶", "行驶", "冲入", "碾压", "碰撞", "坦克", "车队"),
        (ProductionElementKind.VEHICLE,),
        ("车辆协调", "动作指导", "现场安全负责人"),
        ("规划车辆与人员隔离路线", "使用锁速、远程或合成分板", "核定驾驶员资质和通信口令"),
        ("真人进入无隔离的车辆碾压路径",),
    ),
    SafetyHazardRule(
        "safety.animal.v1",
        HazardKind.ANIMAL_ACTION,
        SafetyRiskLevel.CRITICAL,
        ("骡", "马", "动物", "牲畜"),
        (ProductionElementKind.ANIMAL,),
        ("动物协调与福利负责人", "兽医", "动作指导"),
        (
            "活体只承担无接触可控动作",
            "受伤、倒地和中弹使用假体、机械或视效",
            "核定噪声、群体和爆点距离",
        ),
        ("危险活体动作作为默认方案", "活体动物配合血包倒地"),
    ),
    SafetyHazardRule(
        "safety.height.v1",
        HazardKind.HEIGHT_RIGGING,
        SafetyRiskLevel.CRITICAL,
        ("高空", "悬崖", "树上", "屋顶", "坠落", "攀爬"),
        (),
        ("动作指导", "索具负责人", "现场安全负责人"),
        ("建立防坠和快速救援系统", "载人结构与破坏结构分离", "逐次检查锚点和替身方案"),
    ),
    SafetyHazardRule(
        "safety.water.v1",
        HazardKind.WATER_DROWNING,
        SafetyRiskLevel.CRITICAL,
        ("水下", "溺水", "淹没", "河中", "冰面", "落水"),
        (),
        ("水上安全负责人", "潜水安全团队", "现场医疗"),
        ("核定水温、流速和能见度", "设置水下通信与救援潜水员", "准备替身、水槽或合成方案"),
    ),
    SafetyHazardRule(
        "safety.crowd.v1",
        HazardKind.CROWD_ACTION,
        SafetyRiskLevel.HIGH,
        ("人群踩踏", "集体冲锋", "群体冲锋", "大规模群演", "群体逃散"),
        (),
        ("群演协调", "动作指导", "现场安全负责人"),
        (
            "分区编组并控制密度与流线",
            "设置停止口令、隔离带和医疗通道",
            "分层拍摄并优先使用合成扩充",
        ),
        ("无分区的大群体真实踩踏",),
    ),
    SafetyHazardRule(
        "safety.minor.v1",
        HazardKind.MINOR_PERFORMER,
        SafetyRiskLevel.HIGH,
        ("未成年人", "儿童演员", "婴儿", "小孩", "少年士兵", "少年儿童"),
        (),
        ("未成年人协调", "监护与福利负责人", "现场安全负责人"),
        (
            "核定工时、监护、教育和休息要求",
            "与枪火、车辆、动物及极端环境风险隔离",
            "准备替身或合成方案",
        ),
        ("未成年人直接进入高危动作范围",),
    ),
    SafetyHazardRule(
        "safety.gore.v1",
        HazardKind.PROSTHETIC_GORE,
        SafetyRiskLevel.HIGH,
        (
            "爆头",
            "断臂",
            "断肢",
            "肠",
            "开放伤",
            "伤口",
            "伤妆",
            "战伤",
            "血包",
            "血浆",
            "血迹",
            "流血",
            "鲜血",
            "全是血",
            "伤亡",
            "遗容",
            "假体",
            "肢解",
            "尸体",
        ),
        (),
        ("人体特效化妆负责人", "动作指导", "现场医疗"),
        ("区分演员妆、假体、假人和视效分板", "核定过敏测试、穿戴时间和快速移除"),
    ),
    SafetyHazardRule(
        "safety.environment.v1",
        HazardKind.EXTREME_ENVIRONMENT,
        SafetyRiskLevel.HIGH,
        ("严寒", "暴雪", "冻死", "酷暑", "暴晒", "缺氧"),
        (),
        ("现场安全负责人", "现场医疗", "制片协调"),
        ("制定暴露时长、轮换和保温降温方案", "持续监测天气及人员状态"),
    ),
)
