from copy import deepcopy

from movie_breakdown.application.production_validation import ProductionValidationService
from movie_breakdown.domain.production_common import (
    ComplexityDimension,
    ProductionElementKind,
    QuantityEstimate,
)
from movie_breakdown.domain.scene_analysis import Evidence
from movie_breakdown.domain.source import SourceSpan
from tests.factories import make_screenplay
from tests.production_factories import make_production_catalog, make_production_records


def test_complete_production_breakdown_passes_validation() -> None:
    screenplay = make_screenplay()

    report = ProductionValidationService().validate(
        screenplay,
        make_production_records(screenplay),
        make_production_catalog(screenplay),
    )

    assert report.valid
    assert report.coverage == 1
    assert report.analyzed_scene_count == 3
    assert report.catalog_item_count == 5
    assert report.issues == []


def test_missing_record_and_catalog_item_are_blocking() -> None:
    screenplay = make_screenplay()
    records = make_production_records(screenplay)[:-1]
    catalog = make_production_catalog(screenplay)
    catalog.elements = []

    report = ProductionValidationService().validate(screenplay, records, catalog)
    codes = {issue.code for issue in report.issues}

    assert not report.valid
    assert report.coverage == 2 / 3
    assert "production.scene_coverage" in codes
    assert "production.catalog_source_ref" in codes


def test_scene_requirement_rejects_dangling_cast_and_wrong_evidence() -> None:
    screenplay = make_screenplay()
    records = make_production_records(screenplay)
    train = records[-1].analysis
    assert train is not None
    train.elements[0].associated_cast_ids = ["cast-missing"]
    train.elements[0].evidence[0].excerpt = "剧本中不存在的列车描述"

    report = ProductionValidationService().validate(
        screenplay,
        records,
        make_production_catalog(screenplay),
    )
    codes = {issue.code for issue in report.issues}

    assert not report.valid
    assert "production.element_cast_ref" in codes
    assert "production.evidence_excerpt" in codes


def test_catalog_cannot_merge_element_into_wrong_kind() -> None:
    screenplay = make_screenplay()
    catalog = deepcopy(make_production_catalog(screenplay))
    catalog.elements[0].kind = ProductionElementKind.HAND_PROP

    report = ProductionValidationService().validate(
        screenplay,
        make_production_records(screenplay),
        catalog,
    )

    assert not report.valid
    assert any(issue.code == "production.catalog_kind" for issue in report.issues)


def test_evidence_excerpt_must_match_declared_source_span() -> None:
    screenplay = make_screenplay()
    records = make_production_records(screenplay)
    first = records[0].analysis
    assert first is not None
    first.setting.evidence = [
        Evidence(
            scene_id="scene-0001",
            source_span=SourceSpan(line_start=2, line_end=2),
            excerpt="车站 日 外",
            confidence=first.setting.confidence,
        )
    ]

    report = ProductionValidationService().validate(
        screenplay,
        records,
        make_production_catalog(screenplay),
    )

    assert not report.valid
    assert any(issue.code == "production.evidence_excerpt" for issue in report.issues)


def test_catalog_location_tampering_is_blocking() -> None:
    screenplay = make_screenplay()
    catalog = deepcopy(make_production_catalog(screenplay))
    catalog.locations[0].name = "被篡改的地点"

    report = ProductionValidationService().validate(
        screenplay,
        make_production_records(screenplay),
        catalog,
    )

    assert not report.valid
    assert any(issue.code == "production.catalog_derivation_mismatch" for issue in report.issues)


def test_catalog_peak_quantity_tampering_is_blocking() -> None:
    screenplay = make_screenplay()
    catalog = deepcopy(make_production_catalog(screenplay))
    quantity = catalog.elements[0].peak_quantity
    catalog.elements[0].peak_quantity = QuantityEstimate(
        minimum=2,
        maximum=2,
        unit=quantity.unit,
        basis=quantity.basis,
    )

    report = ProductionValidationService().validate(
        screenplay,
        make_production_records(screenplay),
        catalog,
    )

    assert not report.valid
    assert any(issue.code == "production.catalog_derivation_mismatch" for issue in report.issues)


def test_catalog_continuity_and_special_requirement_tampering_is_blocking() -> None:
    screenplay = make_screenplay()
    catalog = deepcopy(make_production_catalog(screenplay))
    catalog.elements[0].continuity_notes = ["新增连续性要求"]
    catalog.elements[0].special_requirements = ["被篡改的特殊要求"]

    report = ProductionValidationService().validate(
        screenplay,
        make_production_records(screenplay),
        catalog,
    )

    assert not report.valid
    assert any(issue.code == "production.catalog_derivation_mismatch" for issue in report.issues)


def test_catalog_complexity_dimensions_tampering_is_blocking() -> None:
    screenplay = make_screenplay()
    catalog = deepcopy(make_production_catalog(screenplay))
    catalog.complex_scenes[0].dimensions = [ComplexityDimension.ACTION_SAFETY]

    report = ProductionValidationService().validate(
        screenplay,
        make_production_records(screenplay),
        catalog,
    )

    assert not report.valid
    assert any(issue.code == "production.catalog_derivation_mismatch" for issue in report.issues)
