from movie_breakdown.application.production_recovery import (
    normalize_production_identity,
    normalize_production_references,
)
from tests.factories import make_screenplay
from tests.production_factories import make_production_analysis


def test_reference_recovery_keeps_valid_ids_and_drops_unknown_ids() -> None:
    scene = make_screenplay().scenes[-1]
    analysis = make_production_analysis(scene)
    element = analysis.elements[0]
    analysis.elements = [
        element.model_copy(
            update={"associated_cast_ids": [analysis.cast[0].id, "background-incorrect"]}
        )
    ]
    factor = analysis.complexity.factors[0]
    analysis.complexity.factors = [
        factor.model_copy(update={"related_requirement_ids": [element.id, "missing-requirement"]})
    ]

    recovered = normalize_production_references(analysis)

    assert recovered.elements[0].associated_cast_ids == [analysis.cast[0].id]
    assert recovered.complexity.factors[0].related_requirement_ids == [element.id]
    assert recovered.uncertainties[-1].subject == "结构化引用待人工确认"
    assert "background-incorrect" in recovered.uncertainties[-1].description
    assert "missing-requirement" in recovered.uncertainties[-1].description


def test_reference_recovery_returns_unchanged_analysis_without_dangling_ids() -> None:
    analysis = make_production_analysis(make_screenplay().scenes[-1])

    recovered = normalize_production_references(analysis)

    assert recovered is analysis


def test_identity_recovery_uses_scoped_scene_id_and_heading() -> None:
    scene = make_screenplay().scenes[0]
    analysis = make_production_analysis(scene).model_copy(
        update={
            "scene_id": "scene-wrong",
            "setting": make_production_analysis(scene).setting.model_copy(
                update={"raw_heading": f"场景：{scene.heading}"}
            ),
        }
    )

    recovered = normalize_production_identity(scene, analysis)

    assert recovered.scene_id == scene.id
    assert recovered.setting.raw_heading == scene.heading
    assert recovered.setting.location_name == analysis.setting.location_name
