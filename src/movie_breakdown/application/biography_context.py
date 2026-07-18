"""为核心人物构建稳定、有限且可追溯的小传分析上下文。"""

from __future__ import annotations

from pydantic import Field

from movie_breakdown.application.character_dossiers import RuleBasedCharacterDossierStrategy
from movie_breakdown.domain.base import StrictModel
from movie_breakdown.domain.character_dossier import CharacterDossierCatalog, CharacterDossierTier
from movie_breakdown.domain.global_analysis import (
    Character,
    CharacterArc,
    CharacterRelation,
    GlobalAnalysisResult,
    StoryEvent,
)
from movie_breakdown.domain.scene_analysis import SceneAnalysis
from movie_breakdown.domain.source import Scene, Screenplay

MAX_SOURCE_SCENES = 8


class BiographyCharacterIndexItem(StrictModel):
    """供小传模型解析人物 ID 和别名的紧凑索引项。"""

    id: str
    name: str
    aliases: list[str]


class BiographyAnalysisContext(StrictModel):
    """单个人物小传调用所需的受限原文与已验证叙事产物。

    Attributes:
        character: 当前待分析的完整归一人物。
        character_index: 保持实体目录顺序的紧凑人物索引。
        source_scenes: 最多八个按剧本顺序排列的原文场景。
        scene_analyses: 人物相关场景的全部已验证逐场分析。
        events: 以当前人物为参与者的全局事件。
        relationships: 以当前人物为任一端点的人物关系。
        character_arc: 当前人物已有的可选弧光。
    """

    character: Character
    character_index: list[BiographyCharacterIndexItem]
    source_scenes: list[Scene] = Field(max_length=MAX_SOURCE_SCENES)
    scene_analyses: list[SceneAnalysis]
    events: list[StoryEvent]
    relationships: list[CharacterRelation]
    character_arc: CharacterArc | None


def build_biography_contexts(
    screenplay: Screenplay,
    scene_analyses: list[SceneAnalysis],
    global_result: GlobalAnalysisResult,
    dossiers: CharacterDossierCatalog | None = None,
) -> list[BiographyAnalysisContext]:
    """为人物弧光角色和高频角色构建按首次出现排序的上下文。

    Args:
        screenplay: 保留场景原文和稳定顺序的完整剧本。
        scene_analyses: 按场景完成并通过校验的逐场分析。
        global_result: 已归一人物、事件、关系和人物弧光的全局结果。
        dossiers: 可选的当前全人物分级档案；缺省时按默认规则即时构建。

    Returns:
        所有选中人物的稳定小传分析上下文。
    """
    characters = global_result.entities.characters
    selected = select_biography_characters(screenplay, global_result, dossiers)
    index = [
        BiographyCharacterIndexItem(id=item.id, name=item.name, aliases=item.aliases)
        for item in characters
    ]
    analysis_by_scene = {item.scene_id: item for item in scene_analyses}
    scene_order = {scene.id: scene.ordinal for scene in screenplay.scenes}
    return [
        _build_context(
            character,
            index,
            screenplay,
            analysis_by_scene,
            global_result,
            scene_order,
        )
        for character in selected
    ]


def select_biography_characters(
    screenplay: Screenplay,
    global_result: GlobalAnalysisResult,
    dossiers: CharacterDossierCatalog | None = None,
) -> list[Character]:
    """选择分级档案中的全部核心人物。

    Args:
        screenplay: 提供稳定场景顺序的完整剧本。
        global_result: 已归一人物和人物弧光的全局分析。
        dossiers: 可选的全人物分级档案；缺省时使用默认规则构建。

    Returns:
        按首次出场顺序排列的核心人物列表。
    """
    characters = global_result.entities.characters
    scene_order = {scene.id: scene.ordinal for scene in screenplay.scenes}
    entity_order = {character.id: index for index, character in enumerate(characters)}
    catalog = dossiers or RuleBasedCharacterDossierStrategy().build(screenplay, global_result)
    selected_ids = {
        item.character_id for item in catalog.dossiers if item.tier == CharacterDossierTier.CORE
    }
    selected = [item for item in characters if item.id in selected_ids]
    return sorted(
        selected,
        key=lambda item: (
            _first_appearance(item, scene_order),
            entity_order[item.id],
        ),
    )


def _first_appearance(character: Character, scene_order: dict[str, int]) -> int:
    """返回人物有效首次出场顺序，无法定位时排在末尾。"""
    direct = scene_order.get(character.first_scene_id)
    if direct is not None:
        return direct
    known = [scene_order[item] for item in character.scene_ids if item in scene_order]
    return min(known, default=len(scene_order) + 1)


def _build_context(
    character: Character,
    character_index: list[BiographyCharacterIndexItem],
    screenplay: Screenplay,
    analysis_by_scene: dict[str, SceneAnalysis],
    global_result: GlobalAnalysisResult,
    scene_order: dict[str, int],
) -> BiographyAnalysisContext:
    """组合一个人物的原文样本、分析、事件、关系和弧光。"""
    relationships = [
        item
        for item in global_result.relationships.relationships
        if character.id in {item.source_character_id, item.target_character_id}
    ]
    events = [item for item in global_result.events.events if character.id in item.participant_ids]
    arc = next(
        (
            item
            for item in global_result.relationships.character_arcs
            if item.character_id == character.id
        ),
        None,
    )
    source_ids = _select_source_scene_ids(
        character,
        arc,
        relationships,
        scene_order,
    )
    scene_by_id = {scene.id: scene for scene in screenplay.scenes}
    relevant_ids = set(character.scene_ids).union(source_ids)
    return BiographyAnalysisContext(
        character=character,
        character_index=character_index,
        source_scenes=[scene_by_id[item] for item in source_ids],
        scene_analyses=[
            analysis_by_scene[scene.id]
            for scene in screenplay.scenes
            if scene.id in relevant_ids and scene.id in analysis_by_scene
        ],
        events=events,
        relationships=relationships,
        character_arc=arc,
    )


def _select_source_scene_ids(
    character: Character,
    arc: CharacterArc | None,
    relationships: list[CharacterRelation],
    scene_order: dict[str, int],
) -> list[str]:
    """按首末、弧转折、关系、实体证据和均匀补位选择原文场景。"""
    appearance_ids = _ordered_known(character.scene_ids, scene_order)
    priority: list[str] = []
    if appearance_ids:
        priority.extend([appearance_ids[0], appearance_ids[-1]])
    if arc:
        for point in arc.turning_points:
            priority.extend(point.scene_ids)
    for relationship in relationships:
        priority.extend(relationship.scene_ids)
    priority.extend(item.scene_id for item in character.evidence)
    selected = _unique_known(priority, scene_order)[:MAX_SOURCE_SCENES]
    remaining = [item for item in appearance_ids if item not in selected]
    slots = MAX_SOURCE_SCENES - len(selected)
    selected.extend(_evenly_spaced(remaining, slots))
    return sorted(set(selected), key=scene_order.__getitem__)


def _ordered_known(values: list[str], scene_order: dict[str, int]) -> list[str]:
    """去重并按剧本顺序返回有效场景 ID。"""
    return sorted({item for item in values if item in scene_order}, key=scene_order.__getitem__)


def _unique_known(values: list[str], scene_order: dict[str, int]) -> list[str]:
    """按输入优先级去重并忽略无法定位的场景 ID。"""
    result: list[str] = []
    for value in values:
        if value in scene_order and value not in result:
            result.append(value)
    return result


def _evenly_spaced(values: list[str], count: int) -> list[str]:
    """从有序候选的等宽区间中心稳定选择指定数量。"""
    if count <= 0 or not values:
        return []
    if count >= len(values):
        return values
    size = len(values)
    return [values[((2 * index + 1) * size) // (2 * count)] for index in range(count)]
