from pathlib import Path

import pytest

from movie_breakdown.application.ports import ModelCallResult
from movie_breakdown.application.splitting import AdaptiveSceneSplitter, SceneSplitError
from movie_breakdown.domain.base import Confidence
from movie_breakdown.domain.run import ProjectConfig
from movie_breakdown.domain.scene_analysis import TokenUsage
from movie_breakdown.domain.source import SceneFormatProfile
from movie_breakdown.infrastructure.parsers import read_and_normalize
from movie_breakdown.infrastructure.scene_sampling import sample_format_pages


class _Detector:
    def __init__(self, pattern: str) -> None:
        self.pattern = pattern
        self.calls = 0

    @property
    def format_prompt_fingerprint(self) -> str:
        return "format-prompt"

    def detect_format(self, document, config):
        self.calls += 1
        profile = SceneFormatProfile(
            format_name="自定义 SCENE 标题",
            scene_start_regex=self.pattern,
            heading_examples=["SCENE 1 - 车站", "SCENE 2 - 月台"],
            confidence=Confidence.HIGH,
            rationale="首尾样本使用同一格式。",
        )
        return ModelCallResult(profile, TokenUsage(input_tokens=10, total_tokens=10), 1)


def _document(tmp_path: Path, content: str):
    path = tmp_path / "剧本.txt"
    path.write_text(content, encoding="utf-8")
    return read_and_normalize(path)


def test_auto_uses_local_rules_when_confident(tmp_path: Path) -> None:
    detector = _Detector(r"^SCENE\s+\d+\s*-\s*(?P<title>.+)$")
    document = _document(
        tmp_path,
        "片名\n1、地点一 日 外\n" + "动作。\n" * 50 + "2、地点二 夜 内\n" + "对白。\n" * 50,
    )

    result = AdaptiveSceneSplitter(detector).split(document, ProjectConfig())

    assert result.screenplay.split_method == "builtin"
    assert detector.calls == 0


def test_model_profile_handles_unknown_format(tmp_path: Path) -> None:
    detector = _Detector(r"^SCENE\s+\d+\s*-\s*(?P<title>.+)$")
    document = _document(
        tmp_path,
        "片名\nSCENE 1 - 车站\n" + "动作。\n" * 50 + "SCENE 2 - 月台\n" + "对白。\n" * 50,
    )

    result = AdaptiveSceneSplitter(detector).split(document, ProjectConfig())

    assert result.screenplay.split_method == "model"
    assert [scene.heading for scene in result.screenplay.scenes] == ["车站", "月台"]
    assert result.usage.input_tokens == 10


def test_model_mode_rejects_unsafe_regex(tmp_path: Path) -> None:
    detector = _Detector(r"^(?=SCENE).+$")
    document = _document(
        tmp_path,
        "片名\nSCENE 1 - 车站\n" + "动作。\n" * 50 + "SCENE 2 - 月台\n" + "对白。\n" * 50,
    )

    with pytest.raises(SceneSplitError, match="禁止"):
        AdaptiveSceneSplitter(detector).split(
            document,
            ProjectConfig(format_detection="model"),
        )


def test_text_format_sample_contains_first_and_last_lines(tmp_path: Path) -> None:
    lines = [f"第 {index} 行剧本文本" for index in range(1, 401)]
    document = _document(tmp_path, "\n".join(lines))

    sample = sample_format_pages(document, lines_each_side=3)

    assert "第 1 行剧本文本" in sample
    assert "第 400 行剧本文本" in sample
    assert "第 200 行剧本文本" not in sample
