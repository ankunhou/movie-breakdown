from movie_breakdown.application.validation import ValidationService
from movie_breakdown.domain.character_dossier import CharacterDossierTier
from tests.factories import (
    make_biographies,
    make_dossiers,
    make_global_result,
    make_records,
    make_screenplay,
)


def test_validation_requires_all_character_dossiers() -> None:
    screenplay = make_screenplay()

    report = ValidationService().validate(
        screenplay,
        make_records(screenplay),
        make_global_result(),
        make_biographies(),
    )

    assert not report.valid
    assert "dossier.missing" in {item.code for item in report.issues}


def test_validation_rejects_missing_and_stale_dossier_snapshot() -> None:
    screenplay = make_screenplay()
    global_result = make_global_result()
    dossiers = make_dossiers(screenplay, global_result)
    dossiers.dossiers = []

    report = ValidationService().validate(
        screenplay,
        make_records(screenplay),
        global_result,
        make_biographies(),
        dossiers,
    )
    codes = {item.code for item in report.issues}

    assert not report.valid
    assert "dossier.coverage" in codes


def test_validation_rejects_dossier_refs_and_non_core_biography() -> None:
    screenplay = make_screenplay()
    global_result = make_global_result()
    dossiers = make_dossiers(screenplay, global_result)
    dossier = dossiers.dossiers[0]
    dossiers.dossiers[0] = dossier.model_copy(
        update={
            "tier": CharacterDossierTier.FUNCTIONAL,
            "event_ids": ["event-missing"],
            "relationship_ids": ["relation-missing"],
        }
    )

    report = ValidationService().validate(
        screenplay,
        make_records(screenplay),
        global_result,
        make_biographies(),
        dossiers,
    )
    codes = {item.code for item in report.issues}

    assert not report.valid
    assert {
        "dossier.event_ref",
        "dossier.relationship_ref",
        "dossier.snapshot",
        "biography.non_core",
    } <= codes
