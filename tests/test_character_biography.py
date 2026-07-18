import pytest
from pydantic import ValidationError

from movie_breakdown.domain.base import Confidence, StageStatus
from movie_breakdown.domain.character_biography import (
    BiographyAnalysisRecord,
    BiographyCatalog,
    BiographyClaimBasis,
    BiographyClaimCategory,
    CharacterBiography,
    CharacterBiographyClaim,
)
from movie_breakdown.domain.scene_analysis import Evidence
from movie_breakdown.domain.source import SourceSpan


def _evidence(scene_id: str = "scene-0001") -> Evidence:
    return Evidence(
        scene_id=scene_id,
        source_span=SourceSpan(line_start=1, line_end=1),
        excerpt="小王走进车站。",
        confidence=Confidence.HIGH,
    )


def _claim(
    claim_id: str = "claim-overview",
    category: BiographyClaimCategory = BiographyClaimCategory.OVERVIEW,
    basis: BiographyClaimBasis = BiographyClaimBasis.OBSERVED,
    *,
    attribution: str | None = None,
    rationale: str | None = None,
) -> CharacterBiographyClaim:
    return CharacterBiographyClaim(
        id=claim_id,
        category=category,
        statement="小王主动踏上旅程。",
        basis=basis,
        attribution=attribution,
        confidence=Confidence.HIGH,
        rationale=rationale,
        alternatives=[],
        evidence=[_evidence()],
    )


def _biography() -> CharacterBiography:
    return CharacterBiography(
        character_id="char-xiaowang",
        context_scene_ids=["scene-0001"],
        summary=_claim(),
        claims=[
            _claim(
                "claim-goal",
                BiographyClaimCategory.GOAL,
                BiographyClaimBasis.INFERRED,
                rationale="他连续采取离乡行动。",
            )
        ],
        unknowns=[BiographyClaimCategory.AGE],
        key_relationship_ids=[],
        representative_lines=[_evidence()],
    )


def test_claim_basis_contract_accepts_supported_variants() -> None:
    observed = _claim()
    reported = _claim(
        basis=BiographyClaimBasis.REPORTED,
        attribution="母亲",
    )
    inferred = _claim(
        basis=BiographyClaimBasis.INFERRED,
        rationale="多次行动呈现同一选择倾向。",
    )

    assert observed.attribution is None
    assert reported.attribution == "母亲"
    assert inferred.rationale


@pytest.mark.parametrize(
    ("basis", "attribution", "rationale", "message"),
    [
        (BiographyClaimBasis.REPORTED, None, None, "必须提供 attribution"),
        (BiographyClaimBasis.OBSERVED, "旁白", None, "只有转述声明"),
        (BiographyClaimBasis.INFERRED, None, None, "必须提供 rationale"),
    ],
)
def test_claim_basis_contract_rejects_mismatched_fields(
    basis: BiographyClaimBasis,
    attribution: str | None,
    rationale: str | None,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        _claim(basis=basis, attribution=attribution, rationale=rationale)


def test_claim_requires_at_least_one_evidence() -> None:
    payload = _claim().model_dump(mode="json")
    payload["evidence"] = []

    with pytest.raises(ValidationError):
        CharacterBiographyClaim.model_validate(payload)


def test_biography_requires_overview_summary_and_unique_claim_ids() -> None:
    biography = _biography()
    payload = biography.model_dump(mode="json")
    payload["summary"]["category"] = BiographyClaimCategory.IDENTITY

    with pytest.raises(ValidationError, match="必须为 overview"):
        CharacterBiography.model_validate(payload)

    payload = biography.model_dump(mode="json")
    payload["claims"][0]["id"] = payload["summary"]["id"]
    with pytest.raises(ValidationError, match="claim id 必须唯一"):
        CharacterBiography.model_validate(payload)


def test_biography_rejects_unknown_overview_and_migrates_partial_category_unknown() -> None:
    payload = _biography().model_dump(mode="json")
    payload["unknowns"] = [BiographyClaimCategory.OVERVIEW]
    with pytest.raises(ValidationError, match="不能标记为 unknown"):
        CharacterBiography.model_validate(payload)

    payload["unknowns"] = [BiographyClaimCategory.GOAL]
    biography = CharacterBiography.model_validate(payload)

    assert biography.unknowns == []


def test_biography_limits_claims_relationships_and_lines() -> None:
    payload = _biography().model_dump(mode="json")
    payload["claims"] = [
        _claim(
            f"claim-{index}",
            BiographyClaimCategory.TRAIT,
        ).model_dump(mode="json")
        for index in range(13)
    ]
    with pytest.raises(ValidationError):
        CharacterBiography.model_validate(payload)

    payload = _biography().model_dump(mode="json")
    payload["key_relationship_ids"] = [f"relation-{index}" for index in range(7)]
    with pytest.raises(ValidationError):
        CharacterBiography.model_validate(payload)

    payload = _biography().model_dump(mode="json")
    payload["representative_lines"] = [_evidence().model_dump(mode="json")] * 4
    with pytest.raises(ValidationError):
        CharacterBiography.model_validate(payload)


def test_catalog_rejects_duplicate_characters_and_record_is_serializable() -> None:
    biography = _biography()
    with pytest.raises(ValidationError, match="character_id 必须唯一"):
        BiographyCatalog(biographies=[biography, biography])

    record = BiographyAnalysisRecord(
        character_id=biography.character_id,
        cache_key="cache-v1",
        status=StageStatus.SUCCESS,
        biography=biography,
        attempts=1,
    )

    restored = BiographyAnalysisRecord.model_validate(record.model_dump(mode="json"))
    assert restored.biography == biography
