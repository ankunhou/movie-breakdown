from movie_breakdown.application.ports import ModelCallResult
from movie_breakdown.domain.production_run import ProductionConfig
from movie_breakdown.domain.production_scene import SceneProductionAnalysis
from movie_breakdown.domain.scene_analysis import TokenUsage
from movie_breakdown.infrastructure.llm.agno_production_analyzer import (
    AgnoProductionAnalyzer,
)
from tests.factories import make_screenplay
from tests.production_factories import make_production_analysis


def test_production_analyzer_builds_numbered_prompt(monkeypatch) -> None:
    scene = make_screenplay().scenes[0]
    analyzer = AgnoProductionAnalyzer("secret")
    captured: dict[str, object] = {}

    def fake_call(schema, instructions, prompt, config, payload_normalizer):
        captured.update(
            schema=schema,
            instructions=instructions,
            prompt=prompt,
            config=config,
            payload_normalizer=payload_normalizer,
        )
        return ModelCallResult(
            make_production_analysis(scene),
            TokenUsage(total_tokens=12),
            1,
        )

    monkeypatch.setattr(analyzer.client, "call", fake_call)
    config = ProductionConfig(concurrency=2)

    result = analyzer.analyze_scene(scene, config)

    assert result.content.scene_id == scene.id
    assert captured["schema"] is SceneProductionAnalysis
    assert captured["config"] is config
    assert f"场景 ID：{scene.id}" in str(captured["prompt"])
    assert f"{scene.source_span.line_start}: {scene.heading}" in str(captured["prompt"])
    assert "不得替剧组决定" in str(captured["instructions"])

    payload = make_production_analysis(scene).model_dump(mode="json")
    payload["complexity"]["score"] = 2
    payload["complexity"]["level"] = "low"
    normalized = captured["payload_normalizer"](payload)
    assert normalized["complexity"]["level"] == "medium"


def test_production_prompt_fingerprint_is_stable() -> None:
    first = AgnoProductionAnalyzer("first")
    second = AgnoProductionAnalyzer("second")

    assert first.production_prompt_fingerprint == second.production_prompt_fingerprint
    assert len(first.production_prompt_fingerprint) == 64
