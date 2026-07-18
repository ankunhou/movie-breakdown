"""用本地受控规则生成模型无权关闭的制片高危候选。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from movie_breakdown.application.production_aggregation_support import stable_catalog_id
from movie_breakdown.application.production_safety_rules import (
    SAFETY_HAZARD_RULES,
    SafetyHazardRule,
)
from movie_breakdown.domain.production_common import ProductionElementKind
from movie_breakdown.domain.production_planning import ResourceOccurrence, ShootingUnit
from movie_breakdown.domain.production_safety import (
    HazardKind,
    SafetyHazard,
    SafetyRiskLevel,
)
from movie_breakdown.domain.production_scene import SceneProductionAnalysis
from movie_breakdown.domain.scene_analysis import Evidence
from movie_breakdown.infrastructure.fingerprint import content_fingerprint


@dataclass(frozen=True, slots=True)
class _RequirementContext:
    """风险规则需要的最小逐场需求上下文。"""

    text: str
    evidence: list[Evidence]
    element_kind: ProductionElementKind | None
    crowd_quantity: int | None = None


class ProductionSafetyDetector:
    """按结构化类别与受控关键词并集生成高危候选。"""

    def detect(
        self,
        analyses: list[SceneProductionAnalysis],
        occurrences: list[ResourceOccurrence],
        shooting_units: list[ShootingUnit],
    ) -> list[SafetyHazard]:
        """为所有命中规则的资源出现项生成不可静默删除的风险范围。

        Args:
            analyses: 当前逐场制片结果。
            occurrences: 已分配到拍摄单元的资源出现项。
            shooting_units: 当前完整拍摄单元。

        Returns:
            按场景、单元和风险类别稳定排序的安全候选。
        """
        contexts = _requirement_contexts(analyses)
        grouped: dict[
            tuple[str, HazardKind],
            list[tuple[ResourceOccurrence, SafetyHazardRule]],
        ] = defaultdict(list)
        for occurrence in occurrences:
            context = contexts.get(occurrence.source_requirement_id)
            if context is None:
                continue
            for rule in SAFETY_HAZARD_RULES:
                if _matches(rule, context):
                    grouped[(occurrence.shooting_unit_id, rule.kind)].append((occurrence, rule))
        unit_order = {unit.id: (unit.scene_id, unit.ordinal) for unit in shooting_units}
        hazards = [
            _hazard(unit_id, kind, entries, contexts)
            for (unit_id, kind), entries in grouped.items()
        ]
        return sorted(
            hazards,
            key=lambda item: (*unit_order[item.shooting_unit_id], item.kind.value),
        )


def _requirement_contexts(
    analyses: list[SceneProductionAnalysis],
) -> dict[str, _RequirementContext]:
    """把逐场需求转换为安全扫描使用的只读文本上下文。"""
    result: dict[str, _RequirementContext] = {}
    for analysis in analyses:
        for item in analysis.cast:
            result[f"{analysis.scene_id}/{item.id}"] = _RequirementContext(
                text=" ".join([item.character_name, *item.performance_notes]),
                evidence=item.evidence,
                element_kind=None,
            )
        for item in analysis.background:
            result[f"{analysis.scene_id}/{item.id}"] = _RequirementContext(
                text=" ".join([item.group_name, item.description, *item.special_skills]),
                evidence=item.evidence,
                element_kind=None,
                crowd_quantity=item.quantity.minimum,
            )
        for item in analysis.elements:
            result[f"{analysis.scene_id}/{item.id}"] = _RequirementContext(
                text=" ".join(
                    filter(
                        None,
                        [
                            item.name,
                            item.description,
                            item.state_or_continuity,
                            *item.special_requirements,
                        ],
                    )
                ),
                evidence=item.evidence,
                element_kind=item.kind,
            )
    return result


def _matches(rule: SafetyHazardRule, context: _RequirementContext) -> bool:
    """判断一个需求是否命中风险类别或任一受控关键词。"""
    if context.element_kind in rule.element_kinds:
        return True
    if any(term in context.text for term in rule.terms):
        return True
    return rule.kind == HazardKind.CROWD_ACTION and (context.crowd_quantity or 0) >= 50


def _hazard(
    unit_id: str,
    kind: HazardKind,
    entries: list[tuple[ResourceOccurrence, SafetyHazardRule]],
    contexts: dict[str, _RequirementContext],
) -> SafetyHazard:
    """合并同一单元同类命中并绑定不可变风险范围指纹。"""
    occurrences = list(dict.fromkeys(item.id for item, _ in entries))
    rules = list(dict.fromkeys(rule for _, rule in entries))
    evidence = _unique_evidence(
        item
        for occurrence, _ in entries
        for item in contexts[occurrence.source_requirement_id].evidence
    )
    scene_id = entries[0][0].scene_id
    scope = content_fingerprint(
        {
            "unit_id": unit_id,
            "kind": kind,
            "occurrences": occurrences,
            "rules": [rule.id for rule in rules],
            "evidence": evidence,
        }
    )
    return SafetyHazard(
        id=stable_catalog_id("hazard", (unit_id, kind.value)),
        scene_id=scene_id,
        shooting_unit_id=unit_id,
        kind=kind,
        risk_level=max((rule.level for rule in rules), key=_risk_rank),
        trigger_rule_ids=[rule.id for rule in rules],
        occurrence_ids=occurrences,
        required_reviewer_roles=list(dict.fromkeys(role for rule in rules for role in rule.roles)),
        description=f"{unit_id} 命中 {kind.value} 高危规则，必须独立专业复核。",
        mandatory_controls=list(dict.fromkeys(value for rule in rules for value in rule.controls)),
        prohibited_methods=list(
            dict.fromkeys(value for rule in rules for value in rule.prohibited)
        ),
        scope_fingerprint=scope,
        evidence=evidence,
    )


def _unique_evidence(values: Iterable[Evidence]) -> list[Evidence]:
    """按内容指纹去重安全证据并保留首次出现顺序。"""
    result: dict[str, Evidence] = {}
    for value in values:
        result.setdefault(content_fingerprint(value), value)
    return list(result.values())[:24]


def _risk_rank(level: SafetyRiskLevel) -> int:
    """把风险枚举映射为可比较的保守等级。"""
    return {
        SafetyRiskLevel.MEDIUM: 1,
        SafetyRiskLevel.HIGH: 2,
        SafetyRiskLevel.CRITICAL: 3,
    }[level]
