import pytest
from pydantic import ValidationError

from movie_breakdown.application.manual_corrections import (
    CorrectionConflictError,
    CorrectionEvidenceError,
    CorrectionTargetError,
    NarrativeCorrectionService,
    StaleCorrectionSetError,
)
from movie_breakdown.domain.base import Confidence
from movie_breakdown.domain.global_analysis import CharacterRelation, ForeshadowingLink
from movie_breakdown.domain.manual_correction import (
    CorrectionField,
    CorrectionSet,
    NarrativeCorrection,
)
from movie_breakdown.domain.scene_analysis import Evidence
from movie_breakdown.domain.source import SourceSpan
from movie_breakdown.infrastructure.fingerprint import content_fingerprint
from tests.factories import make_breakdown


def _evidence(*, excerpt: str = "小王进站。", line: int = 2) -> Evidence:
    return Evidence(
        scene_id="scene-0001",
        source_span=SourceSpan(line_start=line, line_end=line),
        excerpt=excerpt,
        confidence=Confidence.HIGH,
    )


def _correction(
    identifier: str,
    field: CorrectionField,
    review_target_id: str,
    object_id: str,
    old_value: str | None,
    replacement: str | None,
    *,
    evidence: Evidence | None = None,
) -> NarrativeCorrection:
    return NarrativeCorrection(
        id=identifier,
        review_target_id=review_target_id,
        field=field,
        object_id=object_id,
        expected_value_fingerprint=content_fingerprint(old_value),
        replacement=replacement,
        rationale="专家结合上下文校正了原结论。",
        evidence=[evidence or _evidence()],
    )


def _correction_set(breakdown, corrections: list[NarrativeCorrection]) -> CorrectionSet:
    return CorrectionSet(
        source_fingerprint=breakdown.screenplay.source_fingerprint,
        base_analysis_fingerprint=content_fingerprint(breakdown),
        rubric_version="1.1",
        review_answers_fingerprint="review-answers-fingerprint",
        reviewer="叙事顾问",
        corrections=corrections,
    )


def _breakdown_with_all_targets():
    breakdown = make_breakdown()
    breakdown.relationships.relationships.append(
        CharacterRelation(
            id="relation-friend",
            source_character_id="char-xiaowang",
            target_character_id="char-xiaowang",
            relation_type="自我关系",
            development="从犹豫到自我确认。",
            scene_ids=["scene-0001", "scene-0003"],
            evidence=[_evidence()],
        )
    )
    breakdown.structure.foreshadowing.append(
        ForeshadowingLink(
            id="foreshadow-ticket",
            description="进站预示即将离乡。",
            setup_scene_ids=["scene-0001"],
            payoff_scene_ids=["scene-0003"],
            status="paid_off",
            evidence=[_evidence()],
        )
    )
    return breakdown


def test_service_atomically_applies_every_supported_field() -> None:
    breakdown = _breakdown_with_all_targets()
    biography = breakdown.biographies.biographies[0]
    claim = biography.claims[0]
    arc = breakdown.relationships.character_arcs[0]
    corrections = [
        _correction(
            "c01",
            CorrectionField.SCENE_SUMMARY,
            "scene-summary:scene-0001",
            "scene-0001",
            "小王进站。",
            "小王抵达车站，准备离乡。",
        ),
        _correction(
            "c02",
            CorrectionField.EVENT_SUMMARY,
            "event:event-departure",
            "event-departure",
            "小王乘车离开。",
            "小王登车后正式离开故乡。",
        ),
        _correction(
            "c03",
            CorrectionField.ACT_SUMMARY,
            "act:1",
            "1",
            "小王到站",
            "小王抵达车站并作出出发准备。",
        ),
        _correction(
            "c04",
            CorrectionField.ACT_TURNING_POINT,
            "act:1",
            "1",
            "小王到站",
            "小王决定继续走向月台。",
        ),
        _correction(
            "c05",
            CorrectionField.BEAT_SUMMARY,
            "beat:beat-departure",
            "beat-departure",
            "小王离开。",
            "小王完成不可逆的离乡行动。",
        ),
        _correction(
            "c06",
            CorrectionField.PLOT_SUMMARY,
            "plot:plot-main",
            "plot-main",
            "小王完成离乡。",
            "小王从准备到行动，最终完成离乡。",
        ),
        _correction(
            "c07",
            CorrectionField.FORESHADOW_DESCRIPTION,
            "foreshadow:foreshadow-ticket",
            "foreshadow-ticket",
            "进站预示即将离乡。",
            "进站动作预示人物最终登车离乡。",
        ),
        _correction(
            "c08",
            CorrectionField.ARC_INITIAL_STATE,
            "arc:char-xiaowang",
            "char-xiaowang",
            arc.initial_state,
            "虽已进站但仍未真正启程",
        ),
        _correction(
            "c09",
            CorrectionField.ARC_DESIRE,
            "arc:char-xiaowang",
            "char-xiaowang",
            arc.desire,
            "主动离开故乡",
        ),
        _correction(
            "c10",
            CorrectionField.ARC_NEED,
            "arc:char-xiaowang",
            "char-xiaowang",
            arc.need,
            "接受离乡选择的不可逆性",
        ),
        _correction(
            "c11",
            CorrectionField.ARC_FINAL_STATE,
            "arc:char-xiaowang",
            "char-xiaowang",
            arc.final_state,
            "已经乘车离乡",
        ),
        _correction(
            "c12",
            CorrectionField.RELATION_DEVELOPMENT,
            "relation:relation-friend",
            "relation-friend",
            "从犹豫到自我确认。",
            "人物通过行动完成自我确认。",
        ),
        _correction(
            "c13",
            CorrectionField.BIOGRAPHY_SUMMARY,
            "biography:char-xiaowang",
            "char-xiaowang",
            biography.summary.statement,
            "小王是以实际行动完成离乡选择的青年。",
        ),
        _correction(
            "c14",
            CorrectionField.BIOGRAPHY_CLAIM_STATEMENT,
            f"biography-claim:char-xiaowang:{claim.id}",
            claim.id,
            claim.statement,
            "小王的明确目标是乘车离开故乡。",
        ),
        _correction(
            "c15",
            CorrectionField.BIOGRAPHY_CLAIM_RATIONALE,
            f"biography-claim:char-xiaowang:{claim.id}",
            claim.id,
            claim.rationale,
            "从进站、登台到乘车的连续动作构成直接依据。",
        ),
        _correction(
            "c16", CorrectionField.THEME, "theme:1", "1", "成长", "成长来自对自主选择后果的承担"
        ),
        _correction(
            "c17", CorrectionField.MOTIF, "motif:1", "motif:1", "列车", "列车与持续向前的移动"
        ),
    ]

    corrected, receipt = NarrativeCorrectionService().apply(
        breakdown,
        _correction_set(breakdown, corrections),
    )

    assert breakdown.scene_analyses[0].summary == "小王进站。"
    assert corrected.scene_analyses[0].summary == "小王抵达车站，准备离乡。"
    assert corrected.events.events[0].summary == "小王登车后正式离开故乡。"
    assert corrected.structure.acts[0].turning_point == "小王决定继续走向月台。"
    assert corrected.structure.foreshadowing[0].description == "进站动作预示人物最终登车离乡。"
    assert corrected.relationships.character_arcs[0].need == "接受离乡选择的不可逆性"
    assert corrected.relationships.relationships[0].development == "人物通过行动完成自我确认。"
    assert corrected.biographies.biographies[0].summary.statement.startswith("小王是以实际行动")
    assert corrected.biographies.biographies[0].claims[0].rationale.startswith("从进站")
    assert corrected.structure.themes == ["成长来自对自主选择后果的承担"]
    assert corrected.structure.motifs == ["列车与持续向前的移动"]
    assert receipt.applied_count == len(CorrectionField)
    assert receipt.corrected_analysis_fingerprint == content_fingerprint(corrected)


def test_service_rejects_stale_source_and_analysis_bindings() -> None:
    breakdown = make_breakdown()
    correction = _correction(
        "c01",
        CorrectionField.SCENE_SUMMARY,
        "scene-summary:scene-0001",
        "scene-0001",
        "小王进站。",
        "小王抵达车站。",
    )
    correction_set = _correction_set(breakdown, [correction])

    with pytest.raises(StaleCorrectionSetError, match="来源指纹"):
        NarrativeCorrectionService().apply(
            breakdown,
            correction_set.model_copy(update={"source_fingerprint": "other-source"}),
        )
    with pytest.raises(StaleCorrectionSetError, match="基础分析指纹"):
        NarrativeCorrectionService().apply(
            breakdown,
            correction_set.model_copy(update={"base_analysis_fingerprint": "stale"}),
        )


def test_service_rejects_conflicts_without_mutating_original() -> None:
    breakdown = make_breakdown()
    first = _correction(
        "c01",
        CorrectionField.SCENE_SUMMARY,
        "scene-summary:scene-0001",
        "scene-0001",
        "小王进站。",
        "第一次修正",
    )
    second = _correction(
        "c02",
        CorrectionField.SCENE_SUMMARY,
        "scene-summary:scene-0001",
        "scene-0001",
        "小王进站。",
        "第二次修正",
    )

    with pytest.raises(CorrectionConflictError, match="同一字段目标"):
        NarrativeCorrectionService().apply(
            breakdown,
            _correction_set(breakdown, [first, second]),
        )

    assert breakdown.scene_analyses[0].summary == "小王进站。"


def test_service_rejects_stale_value_and_mismatched_review_target() -> None:
    breakdown = make_breakdown()
    stale = _correction(
        "c01",
        CorrectionField.SCENE_SUMMARY,
        "scene-summary:scene-0001",
        "scene-0001",
        "错误旧值",
        "新摘要",
    )
    mismatched = _correction(
        "c02",
        CorrectionField.SCENE_SUMMARY,
        "scene-summary:scene-0002",
        "scene-0001",
        "小王进站。",
        "新摘要",
    )

    with pytest.raises(CorrectionTargetError, match="旧值指纹"):
        NarrativeCorrectionService().apply(breakdown, _correction_set(breakdown, [stale]))
    with pytest.raises(CorrectionTargetError, match="review_target_id"):
        NarrativeCorrectionService().apply(
            breakdown,
            _correction_set(breakdown, [mismatched]),
        )


def test_service_rejects_unlocatable_evidence() -> None:
    breakdown = make_breakdown()
    correction = _correction(
        "c01",
        CorrectionField.SCENE_SUMMARY,
        "scene-summary:scene-0001",
        "scene-0001",
        "小王进站。",
        "小王抵达车站。",
        evidence=_evidence(excerpt="原文中不存在", line=2),
    )

    with pytest.raises(CorrectionEvidenceError, match=r"无法.*定位"):
        NarrativeCorrectionService().apply(breakdown, _correction_set(breakdown, [correction]))


def test_correction_models_reject_duplicate_ids_and_blank_replacement() -> None:
    breakdown = make_breakdown()
    correction = _correction(
        "same",
        CorrectionField.SCENE_SUMMARY,
        "scene-summary:scene-0001",
        "scene-0001",
        "小王进站。",
        "新摘要",
    )

    with pytest.raises(ValidationError, match="重复 correction id"):
        _correction_set(breakdown, [correction, correction])
    with pytest.raises(ValidationError, match="replacement"):
        correction.model_copy(update={"replacement": "   "}).model_validate(
            {**correction.model_dump(), "replacement": "   "}
        )
