from movie_breakdown.application.character_dossiers import RuleBasedCharacterDossierStrategy
from movie_breakdown.application.validation import ValidationService
from movie_breakdown.domain.base import Confidence, StageStatus
from movie_breakdown.domain.character_biography import (
    BiographyCatalog,
    BiographyClaimBasis,
    BiographyClaimCategory,
    CharacterBiography,
    CharacterBiographyClaim,
)
from movie_breakdown.domain.character_dossier import CharacterDossierCatalog
from movie_breakdown.domain.export import NarrativeBreakdown
from movie_breakdown.domain.global_analysis import (
    ActAnalysis,
    ArcTurningPoint,
    Beat,
    Character,
    CharacterArc,
    EntityCatalog,
    EventCatalog,
    GlobalAnalysisResult,
    Location,
    PlotThread,
    RelationshipCatalog,
    StoryEvent,
    StructureAnalysis,
)
from movie_breakdown.domain.scene_analysis import Evidence, SceneAnalysis, SceneAnalysisRecord
from movie_breakdown.domain.source import Scene, Screenplay, SourceSpan


def make_screenplay() -> Screenplay:
    scenes = []
    headings = ["车站 日 外", "月台 日 外", "列车 夜 内"]
    actions = ["小王进站。", "小王登上月台。", "小王乘车离开。"]
    for ordinal, (heading, action) in enumerate(zip(headings, actions, strict=True), start=1):
        start = ordinal * 2 - 1
        scenes.append(
            Scene(
                id=f"scene-{ordinal:04d}",
                ordinal=ordinal,
                heading=heading,
                text=f"{heading}\n{action}",
                source_span=SourceSpan(line_start=start, line_end=start + 1),
                content_fingerprint=f"fingerprint-{ordinal}",
            )
        )
    return Screenplay(title="示例电影", source_fingerprint="source", scenes=scenes)


def make_records(screenplay: Screenplay) -> list[SceneAnalysisRecord]:
    records = []
    for scene in screenplay.scenes:
        analysis = SceneAnalysis(
            scene_id=scene.id,
            summary=scene.text.splitlines()[-1],
            character_names=["小王"],
            objectives=[],
            obstacles=[],
            core_conflict=None,
            events=[],
            state_before=[],
            state_after=[],
            revelations=[],
            suspense=[],
            foreshadowing_candidates=[],
            plot_functions=["推进旅程"],
            uncertainties=[],
            evidence=[],
        )
        records.append(
            SceneAnalysisRecord(
                scene_id=scene.id,
                cache_key=f"cache-{scene.ordinal}",
                status=StageStatus.SUCCESS,
                analysis=analysis,
                attempts=1,
            )
        )
    return records


def make_global_result() -> GlobalAnalysisResult:
    character = Character(
        id="char-xiaowang",
        name="小王",
        aliases=[],
        description="离开故乡的青年。",
        first_scene_id="scene-0001",
        scene_ids=["scene-0001", "scene-0002", "scene-0003"],
        confidence=Confidence.HIGH,
        evidence=[],
    )
    location = Location(
        id="loc-station",
        name="车站",
        aliases=["月台"],
        description="故事的出发地点。",
        scene_ids=["scene-0001", "scene-0002"],
        evidence=[],
    )
    event = StoryEvent(
        id="event-departure",
        summary="小王乘车离开。",
        scene_id="scene-0003",
        participant_ids=["char-xiaowang"],
        cause_event_ids=[],
        consequences=["旅程开始"],
        evidence=[],
    )
    arc = CharacterArc(
        character_id="char-xiaowang",
        initial_state="准备出发",
        desire="离开故乡",
        need=None,
        turning_points=[ArcTurningPoint(summary="登上月台", scene_ids=["scene-0002"], evidence=[])],
        final_state="已经出发",
        evidence=[],
    )
    structure = StructureAnalysis(
        logline="青年小王踏上离乡列车。",
        synopsis="小王到达车站，走上月台，最终乘车离开。",
        acts=[
            ActAnalysis(
                act=index,
                title=title,
                summary=summary,
                scene_ids=[f"scene-{index:04d}"],
                turning_point=summary,
                evidence=[],
            )
            for index, (title, summary) in enumerate(
                [("准备", "小王到站"), ("行动", "小王登台"), ("出发", "小王离开")],
                start=1,
            )
        ],
        beats=[
            Beat(
                id="beat-departure",
                name="启程",
                act=3,
                summary="小王离开。",
                scene_ids=["scene-0003"],
                evidence=[],
            )
        ],
        plot_threads=[
            PlotThread(
                id="plot-main",
                name="离乡",
                kind="primary",
                summary="小王完成离乡。",
                scene_ids=["scene-0001", "scene-0002", "scene-0003"],
                status="resolved",
                evidence=[],
            )
        ],
        foreshadowing=[],
        themes=["成长"],
        motifs=["列车"],
        pacing="短促、连续。",
        evidence=[],
    )
    return GlobalAnalysisResult(
        entities=EntityCatalog(characters=[character], locations=[location]),
        events=EventCatalog(events=[event]),
        relationships=RelationshipCatalog(relationships=[], character_arcs=[arc]),
        structure=structure,
    )


def make_biographies() -> BiographyCatalog:
    evidence = Evidence(
        scene_id="scene-0001",
        source_span=SourceSpan(line_start=2, line_end=2),
        excerpt="小王进站。",
        confidence=Confidence.HIGH,
    )
    summary = CharacterBiographyClaim(
        id="bio-xiaowang-summary",
        category=BiographyClaimCategory.OVERVIEW,
        statement="小王是主动踏上离乡旅程的青年。",
        basis=BiographyClaimBasis.INFERRED,
        confidence=Confidence.HIGH,
        rationale="连续行动均指向离开故乡。",
        alternatives=[],
        evidence=[evidence],
    )
    goal = CharacterBiographyClaim(
        id="bio-xiaowang-goal",
        category=BiographyClaimCategory.GOAL,
        statement="离开故乡。",
        basis=BiographyClaimBasis.INFERRED,
        confidence=Confidence.HIGH,
        rationale="人物持续向列车移动并最终出发。",
        alternatives=[],
        evidence=[evidence],
    )
    return BiographyCatalog(
        biographies=[
            CharacterBiography(
                character_id="char-xiaowang",
                context_scene_ids=["scene-0001", "scene-0003"],
                summary=summary,
                claims=[goal],
                unknowns=[BiographyClaimCategory.AGE, BiographyClaimCategory.APPEARANCE],
                key_relationship_ids=[],
                representative_lines=[],
            )
        ]
    )


def make_dossiers(
    screenplay: Screenplay | None = None,
    global_result: GlobalAnalysisResult | None = None,
) -> CharacterDossierCatalog:
    """使用默认策略构造与测试全局产物匹配的人物档案。"""
    return RuleBasedCharacterDossierStrategy().build(
        screenplay or make_screenplay(),
        global_result or make_global_result(),
    )


def make_breakdown() -> NarrativeBreakdown:
    screenplay = make_screenplay()
    records = make_records(screenplay)
    global_result = make_global_result()
    biographies = make_biographies()
    dossiers = make_dossiers(screenplay, global_result)
    validation = ValidationService().validate(
        screenplay,
        records,
        global_result,
        biographies,
        dossiers,
    )
    return NarrativeBreakdown(
        screenplay=screenplay,
        scene_analyses=[record.analysis for record in records if record.analysis],
        entities=global_result.entities,
        events=global_result.events,
        relationships=global_result.relationships,
        dossiers=dossiers,
        biographies=biographies,
        structure=global_result.structure,
        validation=validation,
    )
