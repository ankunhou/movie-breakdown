"""使用可替换规则策略为全部归一人物构建分级档案。"""

from __future__ import annotations

from typing import Protocol

from movie_breakdown.domain.character_dossier import (
    CharacterDossier,
    CharacterDossierCatalog,
    CharacterDossierTier,
    CharacterImportanceSignals,
)
from movie_breakdown.domain.global_analysis import Character, GlobalAnalysisResult
from movie_breakdown.domain.source import Screenplay


class CharacterDossierStrategy(Protocol):
    """全人物档案分级与构建的可替换策略接口。"""

    def build(
        self,
        screenplay: Screenplay,
        global_result: GlobalAnalysisResult,
    ) -> CharacterDossierCatalog:
        """根据已验证全局产物生成全人物档案。

        Args:
            screenplay: 提供稳定场景顺序的完整剧本。
            global_result: 已归一人物、事件、关系与弧光的全局结果。

        Returns:
            与全局人物一一对应且顺序稳定的分级档案目录。
        """
        ...


class RuleBasedCharacterDossierStrategy:
    """使用弧光、频次、事件和关系信号确定人物档案层级。

    核心人物沿用既有“小传人物”口径：全部弧光人物加高频前三名。
    其余人物按跨场持续性、全局事件和人物关系依次归入重要配角、
    功能人物或背景人物。策略不调用模型，也不保存运行期可变状态。

    Attributes:
        core_frequency_count: 无论是否有弧光都归为核心人物的高频名额。
        recurring_percent: 重要配角持续出场阈值占全片场景数的百分比。
        minimum_recurring_count: 短剧本的重要配角最小持续次数。
    """

    POLICY_VERSION = "rule-based-v1"

    def __init__(
        self,
        core_frequency_count: int = 3,
        recurring_percent: int = 5,
        minimum_recurring_count: int = 5,
    ) -> None:
        """创建确定性人物档案策略。

        Args:
            core_frequency_count: 按出现场景数量补入核心层的最大人数。
            recurring_percent: 重要配角持续出场阈值占全片场景数的百分比。
            minimum_recurring_count: 短剧本的重要配角最小持续次数。

        Raises:
            ValueError: 任一策略参数不在有效正整数范围内。
        """
        if core_frequency_count < 1:
            raise ValueError("核心高频人物数量必须大于零。")
        if not 1 <= recurring_percent <= 100:
            raise ValueError("持续出场百分比必须位于 1 到 100。")
        if minimum_recurring_count < 1:
            raise ValueError("最小持续次数必须大于零。")
        self.core_frequency_count = core_frequency_count
        self.recurring_percent = recurring_percent
        self.minimum_recurring_count = minimum_recurring_count

    def build(
        self,
        screenplay: Screenplay,
        global_result: GlobalAnalysisResult,
    ) -> CharacterDossierCatalog:
        """构建覆盖所有归一人物的可解释分级档案。

        Args:
            screenplay: 提供稳定场景顺序的完整剧本。
            global_result: 已归一人物、事件、关系与弧光的全局结果。

        Returns:
            按全局人物实体顺序排列的分级档案目录。
        """
        characters = global_result.entities.characters
        scene_order = {scene.id: scene.ordinal for scene in screenplay.scenes}
        entity_order = {item.id: index for index, item in enumerate(characters)}
        ranked = sorted(
            characters,
            key=lambda item: (
                -len({scene_id for scene_id in item.scene_ids if scene_id in scene_order}),
                _first_appearance(item, scene_order),
                entity_order[item.id],
            ),
        )
        ranked_with_scenes = [
            item for item in ranked if any(scene_id in scene_order for scene_id in item.scene_ids)
        ]
        top_ranks = {
            item.id: rank
            for rank, item in enumerate(
                ranked_with_scenes[: self.core_frequency_count],
                start=1,
            )
        }
        arc_ids = {item.character_id for item in global_result.relationships.character_arcs}
        valid_character_ids = {item.id for item in ranked_with_scenes}
        core_ids = arc_ids.intersection(valid_character_ids).union(top_ranks)
        scene_threshold = max(
            self.minimum_recurring_count,
            _percentage_ceiling(len(screenplay.scenes), self.recurring_percent),
        )
        event_threshold = max(
            self.minimum_recurring_count,
            _percentage_ceiling(
                len(global_result.events.events),
                self.recurring_percent,
            ),
        )
        known_scene_ids = set(scene_order)
        dossiers = [
            self._build_one(
                character,
                global_result,
                arc_ids,
                core_ids,
                top_ranks,
                known_scene_ids,
                scene_threshold,
                event_threshold,
            )
            for character in characters
        ]
        return CharacterDossierCatalog(
            policy_version=self.POLICY_VERSION,
            scene_recurring_threshold=scene_threshold,
            event_recurring_threshold=event_threshold,
            dossiers=dossiers,
        )

    def _build_one(
        self,
        character: Character,
        global_result: GlobalAnalysisResult,
        arc_ids: set[str],
        core_ids: set[str],
        top_ranks: dict[str, int],
        known_scene_ids: set[str],
        scene_threshold: int,
        event_threshold: int,
    ) -> CharacterDossier:
        """为一个人物汇总全局引用并解释档案层级。"""
        scene_ids = [item for item in dict.fromkeys(character.scene_ids) if item in known_scene_ids]
        event_ids = [
            item.id for item in global_result.events.events if character.id in item.participant_ids
        ]
        relationship_ids = [
            item.id
            for item in global_result.relationships.relationships
            if character.id in {item.source_character_id, item.target_character_id}
        ]
        core_relationship_count = sum(
            1
            for item in global_result.relationships.relationships
            if character.id in {item.source_character_id, item.target_character_id}
            and (
                item.target_character_id
                if item.source_character_id == character.id
                else item.source_character_id
            )
            in core_ids
        )
        has_arc = character.id in arc_ids
        rank = top_ranks.get(character.id)
        tier, reasons = _classify_tier(
            len(scene_ids),
            len(event_ids),
            len(relationship_ids),
            core_relationship_count,
            has_arc,
            rank,
            scene_threshold,
            event_threshold,
        )
        return CharacterDossier(
            character_id=character.id,
            name=character.name,
            tier=tier,
            summary=character.description or "剧本仅识别到人物名称，暂无可靠描述。",
            aliases=list(dict.fromkeys(character.aliases)),
            first_scene_id=(
                character.first_scene_id
                if character.first_scene_id in known_scene_ids
                else next(iter(scene_ids), None)
            ),
            scene_ids=scene_ids,
            event_ids=event_ids,
            relationship_ids=relationship_ids,
            signals=CharacterImportanceSignals(
                scene_count=len(scene_ids),
                event_count=len(event_ids),
                relationship_count=len(relationship_ids),
                core_relationship_count=core_relationship_count,
                has_character_arc=has_arc,
                top_frequency_rank=rank,
            ),
            classification_reasons=reasons,
            evidence=character.evidence,
        )


def _classify_tier(
    scene_count: int,
    event_count: int,
    relationship_count: int,
    core_relationship_count: int,
    has_arc: bool,
    frequency_rank: int | None,
    scene_threshold: int,
    event_threshold: int,
) -> tuple[CharacterDossierTier, list[str]]:
    """依据统计信号返回人物层级和可展示原因。"""
    if scene_count and (has_arc or frequency_rank is not None):
        reasons: list[str] = []
        if has_arc:
            reasons.append("存在跨场景人物弧光")
        if frequency_rank is not None:
            reasons.append(f"出现场景数位列全剧第 {frequency_rank} 名")
        return CharacterDossierTier.CORE, reasons
    if (
        scene_count >= scene_threshold
        or event_count >= event_threshold
        or (core_relationship_count and scene_count >= 2)
    ):
        reasons = []
        if core_relationship_count and scene_count >= 2:
            reasons.append(f"通过 {core_relationship_count} 条关系直接连接核心人物")
        if scene_count >= scene_threshold:
            reasons.append(f"在 {scene_count} 个场景持续出现")
        if event_count >= event_threshold:
            reasons.append(f"参与 {event_count} 个全局事件")
        return CharacterDossierTier.SUPPORTING, reasons
    if scene_count or event_count or relationship_count:
        reason = (
            f"参与 {event_count} 个具体剧情事件" if event_count else f"在 {scene_count} 个场景出现"
        )
        return CharacterDossierTier.FUNCTIONAL, [reason]
    return CharacterDossierTier.BACKGROUND, ["仅有单场或实体索引信息"]


def _first_appearance(character: Character, scene_order: dict[str, int]) -> int:
    """返回人物可定位的首次出场顺序。"""
    direct = scene_order.get(character.first_scene_id)
    if direct is not None:
        return direct
    known = [scene_order[item] for item in character.scene_ids if item in scene_order]
    return min(known, default=len(scene_order) + 1)


def _percentage_ceiling(total: int, percent: int) -> int:
    """使用整数运算计算百分比向上取整结果。"""
    return (total * percent + 99) // 100
