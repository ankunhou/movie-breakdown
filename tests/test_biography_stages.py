from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from movie_breakdown.application.ports import ModelCallResult
from movie_breakdown.domain.base import Confidence, StageStatus
from movie_breakdown.domain.character_biography import (
    BiographyAnalysisRecord,
    BiographyClaimBasis,
    BiographyClaimCategory,
    CharacterBiography,
    CharacterBiographyClaim,
)
from movie_breakdown.domain.global_analysis import Character, CharacterRelation
from movie_breakdown.domain.run import ProjectConfig
from movie_breakdown.domain.scene_analysis import Evidence, TokenUsage
from movie_breakdown.infrastructure.fingerprint import content_fingerprint
from movie_breakdown.infrastructure.llm.agno_analyzer import ModelAnalysisError
from movie_breakdown.infrastructure.storage import ProjectStore
from movie_breakdown.pipeline.artifacts import make_artifact
from movie_breakdown.pipeline.biography_stages import (
    BiographyStageService,
    load_biography_result,
)
from movie_breakdown.pipeline.definitions import stage_versions
from movie_breakdown.pipeline.narrative_stages import GlobalStageResult, SceneStageResult
from movie_breakdown.pipeline.runtime import PipelineStageError, StageRuntime
from tests.factories import make_global_result, make_records, make_screenplay


class _FakeBiographyAnalyzer:
    def __init__(
        self,
        fail_once: set[str] | None = None,
        relationship_refs: dict[str, str] | None = None,
        wrong_id_once: set[str] | None = None,
    ) -> None:
        self.calls: Counter[str] = Counter()
        self.fail_once = set(fail_once or ())
        self.relationship_refs = relationship_refs or {}
        self.wrong_id_once = set(wrong_id_once or ())

    @property
    def biography_prompt_fingerprint(self) -> str:
        return "biography-v1"

    def analyze_biography(self, context, config) -> ModelCallResult[CharacterBiography]:
        character_id = context.character.id
        self.calls[character_id] += 1
        if character_id in self.fail_once:
            self.fail_once.remove(character_id)
            raise ModelAnalysisError(
                "模拟人物小传失败",
                TokenUsage(input_tokens=4, output_tokens=3, total_tokens=7),
                2,
            )
        scene = context.source_scenes[0]
        evidence = Evidence(
            scene_id=scene.id,
            source_span=scene.source_span,
            excerpt=scene.text,
            confidence=Confidence.HIGH,
        )
        summary = CharacterBiographyClaim(
            id=f"{character_id}-overview",
            category=BiographyClaimCategory.OVERVIEW,
            statement=f"{context.character.name}的人物概览。",
            basis=BiographyClaimBasis.OBSERVED,
            attribution=None,
            confidence=Confidence.HIGH,
            rationale=None,
            alternatives=[],
            evidence=[evidence],
        )
        biography = CharacterBiography(
            character_id=("char-wrong" if character_id in self.wrong_id_once else character_id),
            context_scene_ids=[item.id for item in context.source_scenes],
            summary=summary,
            claims=[],
            unknowns=[BiographyClaimCategory.AGE],
            key_relationship_ids=[self.relationship_refs[character_id]]
            if character_id in self.relationship_refs
            else [],
            representative_lines=[evidence],
        )
        self.wrong_id_once.discard(character_id)
        return ModelCallResult(
            content=biography,
            usage=TokenUsage(input_tokens=3, output_tokens=2, total_tokens=5),
            attempts=1,
        )


def _setup_stage(tmp_path: Path, analyzer: _FakeBiographyAnalyzer):
    source = tmp_path / "source.txt"
    source.write_text("示例剧本", encoding="utf-8")
    store = ProjectStore(tmp_path / "project")
    project, manifest = store.initialize(
        source,
        ProjectConfig(concurrency=2),
        stage_versions(),
    )
    screenplay = make_screenplay()
    screenplay_artifact = make_artifact(
        stage_name="scenes",
        cache_key="scenes-key",
        data=screenplay,
        source_fingerprint=screenplay.source_fingerprint,
        upstream_fingerprints=["normalized-fingerprint"],
    )
    records = make_records(screenplay)
    scene_result = SceneStageResult(records, content_fingerprint(records))
    global_content = make_global_result()
    global_content.entities.characters.append(
        Character(
            id="char-xiaoli",
            name="小李",
            aliases=[],
            description="小王的同行者。",
            first_scene_id="scene-0002",
            scene_ids=["scene-0002", "scene-0003"],
            confidence=Confidence.HIGH,
            evidence=[],
        )
    )
    global_content.relationships.relationships.append(
        CharacterRelation(
            id="relation-travel-companions",
            source_character_id="char-xiaowang",
            target_character_id="char-xiaoli",
            relation_type="同行者",
            development="两人结伴离开。",
            scene_ids=["scene-0002", "scene-0003"],
            evidence=[],
        )
    )
    global_result = GlobalStageResult(global_content, content_fingerprint(global_content))
    service = BiographyStageService(StageRuntime(store, manifest), analyzer)
    return service, store, project, screenplay_artifact, scene_result, global_result


def test_biography_stage_reuses_complete_catalog_cache(tmp_path: Path) -> None:
    analyzer = _FakeBiographyAnalyzer()
    service, store, project, screenplay, scenes, global_result = _setup_stage(
        tmp_path,
        analyzer,
    )

    first = service.analyze(project, screenplay, scenes, global_result)
    second_service = BiographyStageService(StageRuntime(store, store.load_manifest()), analyzer)
    second = second_service.analyze(project, screenplay, scenes, global_result)

    assert [item.character_id for item in first.content.biographies] == [
        "char-xiaowang",
        "char-xiaoli",
    ]
    assert second == first
    assert analyzer.calls == Counter({"char-xiaowang": 1, "char-xiaoli": 1})
    assert load_biography_result(store) == first
    assert load_biography_result(store, "outdated-cache-key") is None
    assert len(store.read_jsonl("character_biographies", BiographyAnalysisRecord)) == 2


def test_biography_stage_resume_retries_only_failed_character(tmp_path: Path) -> None:
    analyzer = _FakeBiographyAnalyzer({"char-xiaoli"})
    service, store, project, screenplay, scenes, global_result = _setup_stage(
        tmp_path,
        analyzer,
    )

    with pytest.raises(PipelineStageError, match="1 个人物小传分析失败"):
        service.analyze(project, screenplay, scenes, global_result)

    assert store.load_manifest().stages["character_biographies"].usage.total_tokens == 12

    retry = BiographyStageService(StageRuntime(store, store.load_manifest()), analyzer)
    result = retry.analyze(project, screenplay, scenes, global_result)
    records = {
        item.character_id: item
        for item in store.read_jsonl("character_biographies", BiographyAnalysisRecord)
    }

    assert len(result.content.biographies) == 2
    assert analyzer.calls == Counter({"char-xiaoli": 2, "char-xiaowang": 1})
    assert records["char-xiaoli"].attempts == 3
    assert records["char-xiaoli"].usage.total_tokens == 12
    assert store.load_manifest().stages["character_biographies"].status == StageStatus.SUCCESS


def test_biography_stage_invalidates_only_changed_character_context(tmp_path: Path) -> None:
    analyzer = _FakeBiographyAnalyzer()
    service, store, project, screenplay, scenes, global_result = _setup_stage(
        tmp_path,
        analyzer,
    )
    service.analyze(project, screenplay, scenes, global_result)
    global_result.content.entities.characters[1].description = "小王后来结识的同行者。"
    changed_global = GlobalStageResult(
        global_result.content,
        content_fingerprint(global_result.content),
    )

    rerun = BiographyStageService(StageRuntime(store, store.load_manifest()), analyzer)
    rerun.analyze(project, screenplay, scenes, changed_global)

    assert analyzer.calls == Counter({"char-xiaoli": 2, "char-xiaowang": 1})


def test_biography_stage_stale_status_locally_revalidates_valid_records(tmp_path: Path) -> None:
    analyzer = _FakeBiographyAnalyzer()
    service, store, project, screenplay, scenes, global_result = _setup_stage(
        tmp_path,
        analyzer,
    )
    service.analyze(project, screenplay, scenes, global_result)
    manifest = store.load_manifest()
    manifest.stages["character_biographies"].status = StageStatus.STALE
    store.save_manifest(manifest)

    rerun = BiographyStageService(StageRuntime(store, store.load_manifest()), analyzer)
    rerun.analyze(project, screenplay, scenes, global_result)

    assert analyzer.calls == Counter({"char-xiaowang": 1, "char-xiaoli": 1})


def test_biography_stage_maps_character_reference_to_relationship_id(tmp_path: Path) -> None:
    analyzer = _FakeBiographyAnalyzer(relationship_refs={"char-xiaowang": "char-xiaoli"})
    service, _, project, screenplay, scenes, global_result = _setup_stage(
        tmp_path,
        analyzer,
    )

    result = service.analyze(project, screenplay, scenes, global_result)
    biographies = {item.character_id: item for item in result.content.biographies}

    assert biographies["char-xiaowang"].key_relationship_ids == ["relation-travel-companions"]


def test_biography_stage_preserves_call_cost_when_postprocessing_fails(tmp_path: Path) -> None:
    analyzer = _FakeBiographyAnalyzer(wrong_id_once={"char-xiaoli"})
    service, store, project, screenplay, scenes, global_result = _setup_stage(
        tmp_path,
        analyzer,
    )

    with pytest.raises(PipelineStageError, match="1 个人物小传分析失败"):
        service.analyze(project, screenplay, scenes, global_result)
    failed = {
        item.character_id: item
        for item in store.read_jsonl("character_biographies", BiographyAnalysisRecord)
    }

    assert failed["char-xiaoli"].attempts == 1
    assert failed["char-xiaoli"].usage.total_tokens == 5
    assert store.load_manifest().stages["character_biographies"].usage.total_tokens == 10


def test_biography_stage_rejects_mismatched_cached_character(tmp_path: Path) -> None:
    analyzer = _FakeBiographyAnalyzer()
    service, store, project, screenplay, scenes, global_result = _setup_stage(
        tmp_path,
        analyzer,
    )
    service.analyze(project, screenplay, scenes, global_result)
    records = store.read_jsonl("character_biographies", BiographyAnalysisRecord)
    records[1] = records[1].model_copy(
        update={
            "biography": records[1].biography.model_copy(update={"character_id": "char-xiaowang"})
        }
    )
    store.write_jsonl("character_biographies", records)
    manifest = store.load_manifest()
    manifest.stages["character_biographies"].status = StageStatus.STALE
    store.save_manifest(manifest)

    rerun = BiographyStageService(StageRuntime(store, store.load_manifest()), analyzer)
    rerun.analyze(project, screenplay, scenes, global_result)

    assert analyzer.calls == Counter({"char-xiaoli": 2, "char-xiaowang": 1})
