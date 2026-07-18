import os

import pytest

from movie_breakdown.config import get_settings
from movie_breakdown.domain.run import ProjectConfig
from movie_breakdown.domain.source import Scene, SourceSpan
from movie_breakdown.infrastructure.fingerprint import content_fingerprint
from movie_breakdown.infrastructure.llm.agno_analyzer import AgnoNarrativeAnalyzer


@pytest.mark.live
@pytest.mark.skipif(
    os.getenv("MOVIE_BREAKDOWN_RUN_LIVE_TESTS") != "1",
    reason="设置 MOVIE_BREAKDOWN_RUN_LIVE_TESTS=1 后才调用真实 DeepSeek API",
)
def test_deepseek_scene_analysis_contract() -> None:
    settings = get_settings()
    if settings.deepseek_api_key is None:
        pytest.skip("未配置 DEEPSEEK_API_KEY")
    text = "咖啡馆 日 内\n林然推门而入，看到桌上的旧照片。"
    scene = Scene(
        id="scene-0001",
        ordinal=1,
        heading="咖啡馆 日 内",
        text=text,
        source_span=SourceSpan(line_start=1, line_end=2),
        content_fingerprint=content_fingerprint(text),
    )
    analyzer = AgnoNarrativeAnalyzer(
        settings.deepseek_api_key,
        timeout_seconds=settings.request_timeout_seconds,
    )

    result = analyzer.analyze_scene(scene, ProjectConfig(max_retries=1))

    assert result.content.scene_id == scene.id
    assert result.attempts in {1, 2}
