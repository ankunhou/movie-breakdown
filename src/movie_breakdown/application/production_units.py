"""从共享场景生成保守的场内拍摄单元基线。"""

from __future__ import annotations

import re
from typing import Protocol

from movie_breakdown.domain.base import Confidence
from movie_breakdown.domain.production_common import DayPhase
from movie_breakdown.domain.production_planning import (
    ShootingUnit,
    ShootingUnitSplitReason,
)
from movie_breakdown.domain.production_scene import SceneProductionAnalysis
from movie_breakdown.domain.scene_analysis import Evidence
from movie_breakdown.domain.source import Scene, SourceSpan

_TIME_MARKER = re.compile(
    r"^(?:次日|第二天|翌日|当天夜里|夜里|夜晚|黄昏|黎明|清晨|拂晓|天黑|"
    r"月光下|日出|夜幕|入夜|白天|傍晚)(?:[，。；：:\s]|$)"
)
_MONTAGE_MARKER = re.compile(
    r"^(?:蒙太奇|画面|镜头|闪回|字幕|航拍|与此同时|切至|转场|尾声|多年后|"
    r"各种过去的场景)(?:[，。；：:\s]|$)"
)
_INLINE_HEADING = re.compile(
    r"^(?:\d+[.、．]\s*)?(?:内景|外景|内/外|INT\.?|EXT\.?)(?:[.、．\s-]|$)",
    re.IGNORECASE,
)
_ACTION_PHASE = re.compile(
    r"^(?:战斗开始|战斗结束|交火|爆炸后|枪声停下|突然安静|投降后|战后|"
    r"行动开始|行动结束)(?:[，。；：\s]|$)"
)
_NIGHT = re.compile(r"夜|月光|天黑|入夜|黄昏|傍晚")
_DAWN = re.compile(r"黎明|清晨|拂晓|日出")
_DAY = re.compile(r"白天|次日|第二天|翌日")


class ShootingUnitBuilder(Protocol):
    """场内拍摄单元构建策略。"""

    def build(
        self,
        scene: Scene,
        analysis: SceneProductionAnalysis,
    ) -> list[ShootingUnit]:
        """生成覆盖一个共享场景的有序拍摄单元。

        Args:
            scene: 保留原文行号的共享场景。
            analysis: 已验证的逐场制片需求。

        Returns:
            至少包含一个单元的有序列表。
        """
        ...


class DeterministicShootingUnitBuilder:
    """用受控转场信号生成保守基线，并把复杂语义留给人工修正。"""

    def build(
        self,
        scene: Scene,
        analysis: SceneProductionAnalysis,
    ) -> list[ShootingUnit]:
        """按明确时间、蒙太奇和动作阶段标记划分场内单元。

        Args:
            scene: 保留原文行号的共享场景。
            analysis: 提供默认地点、内外景和时段的逐场结果。

        Returns:
            连续覆盖场景全部行且 ID、序号稳定的单元列表。
        """
        lines = scene.text.splitlines() or [scene.heading]
        boundaries = [(0, [ShootingUnitSplitReason.SCENE_START])]
        for index, line in enumerate(lines[1:], start=1):
            reasons = _split_reasons(line.strip())
            if reasons:
                boundaries.append((index, reasons))
        units: list[ShootingUnit] = []
        for position, (start, reasons) in enumerate(boundaries):
            end = (
                boundaries[position + 1][0] - 1
                if position + 1 < len(boundaries)
                else len(lines) - 1
            )
            absolute_start = scene.source_span.line_start + start
            absolute_end = scene.source_span.line_start + end
            first_line = lines[start].strip() or scene.heading
            unit_id = f"{scene.id}/unit-{position + 1:03d}"
            units.append(
                ShootingUnit(
                    id=unit_id,
                    scene_id=scene.id,
                    ordinal=position + 1,
                    label=_unit_label(first_line, position, analysis),
                    description=f"从“{first_line[:160]}”开始的场内拍摄单元。",
                    source_span=SourceSpan(
                        line_start=absolute_start,
                        line_end=absolute_end,
                    ),
                    split_reasons=reasons,
                    location_name=analysis.setting.location_name,
                    sub_location=analysis.setting.sub_location,
                    interior_exterior=analysis.setting.interior_exterior,
                    time_of_day=_time_of_day(first_line, analysis.setting.time_of_day),
                    raw_time_label=analysis.setting.raw_time_label,
                    evidence=[
                        Evidence(
                            scene_id=scene.id,
                            source_span=SourceSpan(
                                line_start=absolute_start,
                                line_end=absolute_start,
                            ),
                            excerpt=first_line[:300],
                            confidence=Confidence.MEDIUM,
                        )
                    ],
                )
            )
        return units


def suspected_composite_reasons(
    scene: Scene,
    analysis: SceneProductionAnalysis,
    units: list[ShootingUnit],
) -> list[str]:
    """返回仍需要专家判断是否漏拆的确定性风险信号。

    Args:
        scene: 当前共享场景。
        analysis: 当前逐场制片拆解。
        units: 已生成的拍摄单元。

    Returns:
        稳定排序且不重复的中文风险原因。
    """
    text = scene.text
    reasons: list[str] = []
    if len(units) == 1 and analysis.setting.time_of_day == DayPhase.CONTINUOUS:
        reasons.append("逐场时段为 continuous，但仍只有一个拍摄单元。")
    if len(units) == 1 and re.search(r"蒙太奇|各种过去的场景|多年后|次日|天黑", text):
        reasons.append("原文包含复合时间或蒙太奇标记，但仍只有一个拍摄单元。")
    if len(units) == 1 and analysis.complexity.score >= 4 and len(text.splitlines()) >= 20:
        reasons.append("高复杂度长场景仍只有一个拍摄单元。")
    if any(
        len(line) > 180 and re.search(r"航拍|城市|仓库|蒙太奇|镜头", line)
        for line in text.splitlines()
    ):
        reasons.append("原文存在可能在同一行内切换地点或蒙太奇节拍的长句。")
    return list(dict.fromkeys(reasons))


def _split_reasons(line: str) -> list[ShootingUnitSplitReason]:
    """把一行受控转场信号转换为稳定拆分原因。"""
    reasons: list[ShootingUnitSplitReason] = []
    if _TIME_MARKER.search(line):
        reasons.append(ShootingUnitSplitReason.TIME_CHANGE)
    if _MONTAGE_MARKER.search(line):
        reasons.append(
            ShootingUnitSplitReason.TITLE_CARD
            if line.startswith("字幕")
            else ShootingUnitSplitReason.MONTAGE_BEAT
        )
    if _INLINE_HEADING.search(line):
        reasons.append(ShootingUnitSplitReason.LOCATION_CHANGE)
    if _ACTION_PHASE.search(line):
        reasons.append(ShootingUnitSplitReason.ACTION_PHASE)
    return list(dict.fromkeys(reasons))


def _time_of_day(line: str, fallback: DayPhase) -> DayPhase:
    """从单元起始行识别明确时段，否则继承场景设置。"""
    if _DAWN.search(line):
        return DayPhase.DAWN
    if _NIGHT.search(line):
        return DayPhase.NIGHT
    if _DAY.search(line):
        return DayPhase.DAY
    return fallback


def _unit_label(line: str, index: int, analysis: SceneProductionAnalysis) -> str:
    """生成不依赖数组外状态的中文单元名称。"""
    if index == 0:
        return f"{analysis.setting.location_name}·主单元"
    cleaned = re.sub(r"\s+", " ", line).strip("：:，,。 ")
    return cleaned[:80] or f"补充分段 {index + 1}"
