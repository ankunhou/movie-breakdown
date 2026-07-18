"""跨场景实体、事件、关系和叙事结构模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from movie_breakdown.domain.base import Confidence, StrictModel
from movie_breakdown.domain.scene_analysis import Evidence


class Character(StrictModel):
    """完成别名归一的剧本人物。"""

    id: str
    name: str
    aliases: list[str]
    description: str
    first_scene_id: str
    scene_ids: list[str]
    confidence: Confidence
    evidence: list[Evidence]


class Location(StrictModel):
    """完成别名归一的剧本地点。"""

    id: str
    name: str
    aliases: list[str]
    description: str
    scene_ids: list[str]
    evidence: list[Evidence]


class StoryEvent(StrictModel):
    """具有参与者和因果关系的全局故事事件。"""

    id: str
    summary: str
    scene_id: str
    participant_ids: list[str]
    cause_event_ids: list[str]
    consequences: list[str]
    evidence: list[Evidence]


class CharacterRelation(StrictModel):
    """两个已归一人物之间的发展关系。"""

    id: str
    source_character_id: str
    target_character_id: str
    relation_type: str
    development: str
    scene_ids: list[str]
    evidence: list[Evidence]


class ArcTurningPoint(StrictModel):
    """人物弧光中的关键转折。"""

    summary: str
    scene_ids: list[str]
    evidence: list[Evidence]


class CharacterArc(StrictModel):
    """人物从初始状态到最终状态的变化轨迹。"""

    character_id: str
    initial_state: str
    desire: str
    need: str | None
    turning_points: list[ArcTurningPoint]
    final_state: str
    evidence: list[Evidence]


class PlotThread(StrictModel):
    """故事主线或支线。"""

    id: str
    name: str
    kind: Literal["primary", "subplot"]
    summary: str
    scene_ids: list[str]
    status: Literal["resolved", "open", "unclear"]
    evidence: list[Evidence]


class Beat(StrictModel):
    """三幕结构中的关键叙事节拍。"""

    id: str
    name: str
    act: int = Field(ge=1, le=3)
    summary: str
    scene_ids: list[str]
    evidence: list[Evidence]


class ForeshadowingLink(StrictModel):
    """伏笔设置与回收之间的关系。"""

    id: str
    description: str
    setup_scene_ids: list[str]
    payoff_scene_ids: list[str]
    status: Literal["paid_off", "open", "unclear"]
    evidence: list[Evidence]


class ActAnalysis(StrictModel):
    """三幕结构中单幕的分析结果。"""

    act: int = Field(ge=1, le=3)
    title: str
    summary: str
    scene_ids: list[str]
    turning_point: str | None
    evidence: list[Evidence]


class EntityCatalog(StrictModel):
    """人物和地点实体目录。"""

    schema_version: str = "1.0"
    characters: list[Character]
    locations: list[Location]


class EventCatalog(StrictModel):
    """全局故事事件目录。"""

    schema_version: str = "1.0"
    events: list[StoryEvent]


class RelationshipCatalog(StrictModel):
    """人物关系与人物弧光目录。"""

    schema_version: str = "1.0"
    relationships: list[CharacterRelation]
    character_arcs: list[CharacterArc]


class StructureAnalysis(StrictModel):
    """剧本的全局叙事结构分析。"""

    schema_version: str = "1.0"
    logline: str
    synopsis: str
    acts: list[ActAnalysis]
    beats: list[Beat]
    plot_threads: list[PlotThread]
    foreshadowing: list[ForeshadowingLink]
    themes: list[str]
    motifs: list[str]
    pacing: str
    evidence: list[Evidence]


class GlobalAnalysisResult(StrictModel):
    """单次全局分析返回的完整结构化结果。"""

    schema_version: str = "1.0"
    entities: EntityCatalog
    events: EventCatalog
    relationships: RelationshipCatalog
    structure: StructureAnalysis
