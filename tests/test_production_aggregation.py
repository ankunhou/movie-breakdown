from copy import deepcopy

import pytest

from movie_breakdown.application.production_aggregation import (
    ConservativeProductionCatalogBuilder,
)
from movie_breakdown.application.production_aggregation_support import peak_quantity
from movie_breakdown.application.production_validation import ProductionValidationService
from movie_breakdown.domain.base import Confidence
from movie_breakdown.domain.production_common import (
    ProductionElementKind,
    QuantityBasis,
    QuantityEstimate,
    RequirementBasis,
)
from movie_breakdown.domain.production_scene import ProductionElement
from tests.factories import make_screenplay
from tests.production_factories import (
    make_production_analysis,
    make_production_records,
    scene_evidence,
)


def _analyses():
    screenplay = make_screenplay()
    return screenplay, [make_production_analysis(scene) for scene in screenplay.scenes]


def _element(scene, *, element_id, name, kind, subtype=None, quantity=None):
    return ProductionElement(
        id=element_id,
        kind=kind,
        name=name,
        subtype=subtype,
        description=f"需要准备{name}。",
        quantity=quantity
        or QuantityEstimate(minimum=1, maximum=1, unit="件", basis=QuantityBasis.EXACT),
        associated_cast_ids=[],
        special_requirements=[],
        basis=RequirementBasis.EXPLICIT,
        confidence=Confidence.HIGH,
        evidence=[scene_evidence(scene)],
    )


def test_builder_is_order_independent_and_passes_validation() -> None:
    screenplay, analyses = _analyses()
    builder = ConservativeProductionCatalogBuilder()

    first = builder.build(screenplay, analyses)
    second = builder.build(screenplay, list(reversed(analyses)))
    report = ProductionValidationService().validate(
        screenplay,
        make_production_records(screenplay),
        first,
    )

    assert first == second
    assert report.valid
    assert len(first.locations) == 3
    assert len(first.cast) == 1
    assert len(first.elements) == 1


def test_element_merge_uses_nfkc_whitespace_and_kind() -> None:
    screenplay, analyses = _analyses()
    scene = screenplay.scenes[0]
    analyses[2].elements[0].name = "TRAIN"
    analyses[0].elements = [
        _element(
            scene,
            element_id="element-train-variant",
            name="  ＴＲＡＩＮ  ",
            kind=ProductionElementKind.VEHICLE,
        ),
        _element(
            scene,
            element_id="element-train-prop",
            name="列车",
            kind=ProductionElementKind.HAND_PROP,
        ),
    ]

    catalog = ConservativeProductionCatalogBuilder().build(screenplay, analyses)

    vehicles = [item for item in catalog.elements if item.kind == ProductionElementKind.VEHICLE]
    props = [item for item in catalog.elements if item.kind == ProductionElementKind.HAND_PROP]
    assert len(vehicles) == 1
    assert vehicles[0].scene_ids == ["scene-0001", "scene-0003"]
    assert len(props) == 1


def test_other_subtypes_and_location_subareas_do_not_merge() -> None:
    screenplay, analyses = _analyses()
    first_scene, second_scene = screenplay.scenes[:2]
    analyses[0].setting.location_name = "车站"
    analyses[0].setting.sub_location = "候车厅"
    analyses[1].setting.location_name = "车站"
    analyses[1].setting.sub_location = "月台"
    analyses[0].elements = [
        _element(
            first_scene,
            element_id="element-other-a",
            name="特殊装置",
            kind=ProductionElementKind.OTHER,
            subtype="机关",
        )
    ]
    analyses[1].elements = [
        _element(
            second_scene,
            element_id="element-other-b",
            name="特殊装置",
            kind=ProductionElementKind.OTHER,
            subtype="监视器",
        )
    ]

    catalog = ConservativeProductionCatalogBuilder().build(screenplay, analyses)

    station_locations = [item for item in catalog.locations if item.name.startswith("车站")]
    other_elements = [item for item in catalog.elements if item.kind == ProductionElementKind.OTHER]
    assert {item.name for item in station_locations} == {"车站 / 候车厅", "车站 / 月台"}
    assert len(other_elements) == 2
    assert {tuple(item.subtypes) for item in other_elements} == {("机关",), ("监视器",)}


def test_same_name_with_different_character_ids_stays_separate() -> None:
    screenplay, analyses = _analyses()
    analyses[0].cast[0].character_id = "char-a"
    analyses[1].cast[0].character_id = "char-b"
    analyses[2].cast[0].character_id = None

    catalog = ConservativeProductionCatalogBuilder().build(screenplay, analyses)

    assert len(catalog.cast) == 3
    assert {item.character_id for item in catalog.cast} == {"char-a", "char-b", None}


@pytest.mark.parametrize(
    ("quantities", "expected"),
    [
        (
            [
                QuantityEstimate(minimum=2, maximum=2, unit="人", basis="exact"),
                QuantityEstimate(minimum=5, maximum=5, unit="人", basis="exact"),
            ],
            QuantityEstimate(minimum=5, maximum=5, unit="人", basis="exact"),
        ),
        (
            [
                QuantityEstimate(minimum=2, maximum=4, unit="人", basis="range"),
                QuantityEstimate(minimum=3, maximum=8, unit="人", basis="range"),
            ],
            QuantityEstimate(minimum=3, maximum=8, unit="人", basis="range"),
        ),
        (
            [
                QuantityEstimate(minimum=10, maximum=10, unit="人", basis="exact"),
                QuantityEstimate(minimum=3, unit="人", basis="minimum"),
            ],
            QuantityEstimate(minimum=10, unit="人", basis="minimum"),
        ),
        (
            [
                QuantityEstimate(minimum=10, maximum=10, unit="人", basis="exact"),
                QuantityEstimate(unit="人", basis="unknown"),
            ],
            QuantityEstimate(minimum=10, unit="人", basis="minimum"),
        ),
        (
            [
                QuantityEstimate(minimum=2, maximum=3, unit="人", basis="estimated"),
                QuantityEstimate(minimum=5, maximum=5, unit="人", basis="exact"),
            ],
            QuantityEstimate(minimum=5, maximum=5, unit="人", basis="estimated"),
        ),
    ],
)
def test_peak_quantity_is_conservative(quantities, expected) -> None:
    assert peak_quantity(quantities) == expected


def test_peak_quantity_does_not_convert_units() -> None:
    result = peak_quantity(
        [
            QuantityEstimate(minimum=2, maximum=2, unit="人", basis="exact"),
            QuantityEstimate(minimum=3, maximum=3, unit="名", basis="exact"),
        ]
    )

    assert result == QuantityEstimate(unit="单位待确认", basis=QuantityBasis.UNKNOWN)


def test_catalog_ids_do_not_change_when_unrelated_item_is_added() -> None:
    screenplay, analyses = _analyses()
    builder = ConservativeProductionCatalogBuilder()
    before = builder.build(screenplay, analyses)
    changed = deepcopy(analyses)
    changed[0].elements.append(
        _element(
            screenplay.scenes[0],
            element_id="element-ticket",
            name="车票",
            kind=ProductionElementKind.HAND_PROP,
        )
    )

    after = builder.build(screenplay, changed)

    before_train = next(item for item in before.elements if item.name == "列车")
    after_train = next(item for item in after.elements if item.name == "列车")
    assert before_train.id == after_train.id


def test_builder_rejects_duplicate_and_missing_scene_analysis() -> None:
    screenplay, analyses = _analyses()
    builder = ConservativeProductionCatalogBuilder()

    with pytest.raises(ValueError, match="重复场景"):
        builder.build(screenplay, [*analyses, analyses[0]])
    with pytest.raises(ValueError, match="缺少场景"):
        builder.build(screenplay, analyses[:-1])
