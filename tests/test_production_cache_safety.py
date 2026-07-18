from pathlib import Path

import pytest

from movie_breakdown.domain.base import Confidence, StageStatus
from movie_breakdown.domain.production_scene import SceneProductionRecord
from movie_breakdown.domain.scene_analysis import Evidence
from movie_breakdown.domain.source import SourceSpan
from movie_breakdown.pipeline.runtime import PipelineStageError
from tests.test_production_scene_stages import _FakeProductionAnalyzer, _service


def test_successful_production_cache_is_renormalized_without_model_call(
    tmp_path: Path,
) -> None:
    analyzer = _FakeProductionAnalyzer()
    production, project, screenplay, service = _service(tmp_path, analyzer)
    first = service.analyze(project, screenplay)
    record = first.records[0]
    assert record.analysis is not None
    evidence = record.analysis.setting.evidence[0]
    numbered = evidence.model_copy(update={"excerpt": f"1: {evidence.excerpt}"})
    setting = record.analysis.setting.model_copy(update={"evidence": [numbered]})
    altered = record.model_copy(
        update={"analysis": record.analysis.model_copy(update={"setting": setting})}
    )
    production.write_jsonl("scene_elements", [altered, *first.records[1:]])

    result = service.analyze(project, screenplay)
    persisted = production.read_jsonl("scene_elements", SceneProductionRecord)

    assert analyzer.calls == 3
    assert result.records[0].analysis is not None
    assert result.records[0].analysis.setting.evidence[0].excerpt == evidence.excerpt
    assert persisted[0] == result.records[0]


def test_invalid_successful_production_cache_is_rerun(tmp_path: Path) -> None:
    analyzer = _FakeProductionAnalyzer()
    production, project, screenplay, service = _service(tmp_path, analyzer)
    first = service.analyze(project, screenplay)
    record = first.records[1]
    assert record.analysis is not None
    invalid_evidence = Evidence(
        scene_id=record.scene_id,
        source_span=SourceSpan(line_start=999, line_end=999),
        excerpt="当前场景不存在的证据",
        confidence=Confidence.HIGH,
    )
    setting = record.analysis.setting.model_copy(update={"evidence": [invalid_evidence]})
    invalid = record.model_copy(
        update={"analysis": record.analysis.model_copy(update={"setting": setting})}
    )
    production.write_jsonl(
        "scene_elements",
        [first.records[0], invalid, first.records[2]],
    )

    result = service.analyze(project, screenplay)

    assert analyzer.calls == 4
    assert result.records[1].status == StageStatus.SUCCESS
    assert result.records[1].attempts == 2
    assert result.records[1].usage.total_tokens == 20
    assert result.records[1].analysis is not None
    assert result.records[1].analysis.setting.evidence[0].source_span.line_start != 999


@pytest.mark.parametrize("broken_content", ["{not-json}\n", "{}\n"])
def test_broken_production_jsonl_stops_before_model_call(
    tmp_path: Path,
    broken_content: str,
) -> None:
    analyzer = _FakeProductionAnalyzer()
    production, project, screenplay, service = _service(tmp_path, analyzer)
    path = production.artifacts_dir / "scene_elements.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(broken_content, encoding="utf-8")

    with pytest.raises(
        PipelineStageError,
        match=r"无法安全读取或校验.*未调用模型",
    ):
        service.analyze(project, screenplay)

    stage = production.load_manifest().stages["production_scene_analysis"]
    assert analyzer.calls == 0
    assert stage.status == StageStatus.FAILED
    assert stage.error is not None
    assert "未调用模型" in stage.error
    assert path.read_text(encoding="utf-8") == broken_content
