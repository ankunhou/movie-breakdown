from types import SimpleNamespace
from typing import ClassVar

import pytest
from pydantic import SecretStr, ValidationError

from movie_breakdown.domain.character_biography import CharacterBiography
from movie_breakdown.domain.run import ProjectConfig
from movie_breakdown.domain.scene_analysis import SceneAnalysis
from movie_breakdown.domain.source import Scene, SourceSpan
from movie_breakdown.infrastructure.llm import agno_client
from movie_breakdown.infrastructure.llm.agno_analyzer import (
    AgnoNarrativeAnalyzer,
    ModelAnalysisError,
)
from movie_breakdown.infrastructure.llm.agno_support import extract_usage
from tests.factories import make_biographies


def _scene() -> Scene:
    return Scene(
        id="scene-0001",
        ordinal=1,
        heading="车站 日 外",
        text="1、车站 日 外\n小王走进车站。",
        source_span=SourceSpan(line_start=10, line_end=11),
        content_fingerprint="fingerprint",
    )


def _valid_analysis() -> dict:
    return SceneAnalysis(
        scene_id="scene-0001",
        summary="小王进入车站。",
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
        plot_functions=["建立人物行动"],
        uncertainties=[],
        evidence=[],
    ).model_dump(mode="json")


class _FakeModel:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


class _RetryingAgent:
    calls = 0
    prompts: ClassVar[list[str]] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    def run(self, prompt: str, stream: bool):
        type(self).calls += 1
        type(self).prompts.append(prompt)
        content = {"scene_id": "scene-0001"} if self.calls == 1 else _valid_analysis()
        provider = SimpleNamespace(
            provider_metrics={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        )
        return SimpleNamespace(
            content=content,
            metrics=SimpleNamespace(details={"deepseek": [provider]}),
        )


class _InvalidAgent(_RetryingAgent):
    def run(self, prompt: str, stream: bool):
        type(self).calls += 1
        return SimpleNamespace(
            content={},
            metrics=SimpleNamespace(input_tokens=8, output_tokens=2, total_tokens=10),
        )


class _TransientAgent(_RetryingAgent):
    def run(self, prompt: str, stream: bool):
        type(self).calls += 1
        if self.calls == 1:
            raise ConnectionError("模拟上游瞬时断开")
        return SimpleNamespace(
            content=_valid_analysis(),
            metrics=SimpleNamespace(input_tokens=8, output_tokens=2, total_tokens=10),
        )


def test_analyzer_repairs_invalid_structured_output(monkeypatch) -> None:
    _RetryingAgent.calls = 0
    _RetryingAgent.prompts = []
    monkeypatch.setattr(agno_client, "DeepSeek", _FakeModel)
    monkeypatch.setattr(agno_client, "Agent", _RetryingAgent)
    analyzer = AgnoNarrativeAnalyzer(SecretStr("secret"), timeout_seconds=30)

    result = analyzer.analyze_scene(_scene(), ProjectConfig(max_retries=1))

    assert result.content.scene_id == "scene-0001"
    assert result.attempts == 2
    assert result.usage.total_tokens == 30
    assert "上一次输出无效" in _RetryingAgent.prompts[-1]
    assert analyzer.api_key == "secret"


def test_analyzer_raises_after_retry_budget(monkeypatch) -> None:
    _InvalidAgent.calls = 0
    monkeypatch.setattr(agno_client, "DeepSeek", _FakeModel)
    monkeypatch.setattr(agno_client, "Agent", _InvalidAgent)
    analyzer = AgnoNarrativeAnalyzer("secret")

    with pytest.raises(ModelAnalysisError, match="已尝试 2 次") as raised:
        analyzer.analyze_scene(_scene(), ProjectConfig(max_retries=1))

    assert raised.value.attempts == 2
    assert raised.value.usage.total_tokens == 20


def test_analyzer_retries_transient_provider_error(monkeypatch) -> None:
    _TransientAgent.calls = 0
    monkeypatch.setattr(agno_client, "DeepSeek", _FakeModel)
    monkeypatch.setattr(agno_client, "Agent", _TransientAgent)
    analyzer = AgnoNarrativeAnalyzer("secret")

    result = analyzer.analyze_scene(_scene(), ProjectConfig(max_retries=1))

    assert result.attempts == 2
    assert result.content.scene_id == "scene-0001"
    assert result.usage.total_tokens == 10


def test_extract_usage_supports_agno_run_metrics() -> None:
    response = SimpleNamespace(
        metrics=SimpleNamespace(
            input_tokens=120,
            output_tokens=30,
            total_tokens=150,
            details=None,
        )
    )

    usage = extract_usage(response)

    assert usage.input_tokens == 120
    assert usage.output_tokens == 30
    assert usage.total_tokens == 150


def test_coerce_truncates_overlong_evidence_before_validation() -> None:
    payload = _valid_analysis()
    payload["evidence"] = [
        {
            "scene_id": "scene-0001",
            "source_span": {"line_start": 10, "line_end": 10},
            "excerpt": "原" * 400,
            "confidence": "high",
        }
    ]

    analysis = AgnoNarrativeAnalyzer._coerce(SceneAnalysis, payload)

    assert len(analysis.evidence[0].excerpt) == 300


def test_coerce_limits_model_supplied_biography_context_before_validation() -> None:
    payload = make_biographies().biographies[0].model_dump(mode="json")
    payload["context_scene_ids"] = [
        "scene-0001",
        "scene-0001",
        *(f"scene-{index:04d}" for index in range(2, 12)),
    ]
    payload["key_relationship_ids"] = [
        "relationship-1",
        "relationship-1",
        *(f"relationship-{index}" for index in range(2, 9)),
    ]
    payload["representative_lines"] = [payload["summary"]["evidence"][0] for _ in range(5)]
    payload["unknowns"] = [
        "age",
        "age",
        "overview",
        "goal",
        "appearance",
    ]

    biography = AgnoNarrativeAnalyzer._coerce(CharacterBiography, payload)

    assert biography.context_scene_ids == [
        "scene-0001",
        "scene-0002",
        "scene-0003",
        "scene-0004",
        "scene-0005",
        "scene-0006",
        "scene-0007",
        "scene-0008",
    ]
    assert biography.key_relationship_ids == [
        "relationship-1",
        "relationship-2",
        "relationship-3",
        "relationship-4",
        "relationship-5",
        "relationship-6",
    ]
    assert len(biography.representative_lines) == 3
    assert [category.value for category in biography.unknowns] == ["age", "appearance"]


def test_coerce_does_not_truncate_biography_claims() -> None:
    payload = make_biographies().biographies[0].model_dump(mode="json")
    claim = payload["claims"][0]
    payload["claims"] = [{**claim, "id": f"claim-{index}"} for index in range(13)]

    with pytest.raises(ValidationError, match="List should have at most 12 items"):
        AgnoNarrativeAnalyzer._coerce(CharacterBiography, payload)


def test_agent_leaves_pydantic_validation_to_recovery_layer(monkeypatch) -> None:
    monkeypatch.setattr(agno_client, "DeepSeek", _FakeModel)
    monkeypatch.setattr(agno_client, "Agent", _RetryingAgent)
    analyzer = AgnoNarrativeAnalyzer("secret")

    agent = analyzer._build_agent(SceneAnalysis, "instructions", ProjectConfig())

    assert agent.kwargs["parse_response"] is True
    assert isinstance(agent.kwargs["output_schema"], dict)
    assert "properties" in agent.kwargs["output_schema"]
    assert agent.kwargs["output_schema"]["title"] == "SceneAnalysis"
