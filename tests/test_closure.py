from pathlib import Path

import pytest
from pydantic import ValidationError

from movie_breakdown.application.closure import NarrativeClosureService
from movie_breakdown.application.quality import RUBRIC_VERSION, NarrativeQualityService
from movie_breakdown.domain.export import NarrativeBreakdown
from movie_breakdown.domain.manual_correction import (
    CorrectionField,
    CorrectionReceipt,
    CorrectionSet,
    NarrativeCorrection,
)
from movie_breakdown.domain.quality import (
    DimensionRating,
    HumanReviewAnswers,
    ReviewResponse,
    ReviewVerdict,
    SemanticQualityReport,
)
from movie_breakdown.domain.release import ReleaseGateReport
from movie_breakdown.domain.scene_analysis import Evidence
from movie_breakdown.domain.source import SourceSpan
from movie_breakdown.infrastructure.fingerprint import content_fingerprint
from movie_breakdown.infrastructure.storage import ProjectStore
from tests.test_pipeline import _FakeAnalyzer, _pipeline


class _ClosureAnalyzer(_FakeAnalyzer):
    def analyze_global(self, screenplay, analyses, config):
        result = super().analyze_global(screenplay, analyses, config)
        result.content.structure.themes.append("离别")
        return result


def _project(tmp_path: Path) -> tuple[ProjectStore, NarrativeBreakdown]:
    pipeline = _pipeline(tmp_path, _ClosureAnalyzer())
    pipeline.run()
    return pipeline.store, pipeline.load_base_breakdown()


def _correction_inputs(
    store: ProjectStore,
    base: NarrativeBreakdown,
    *,
    base_fingerprint: str | None = None,
) -> tuple[Path, Path]:
    scene = base.screenplay.scenes[0]
    current_summary = base.scene_analyses[0].summary
    replacement = "小王抵达车站，准备离乡。"
    target_id = f"scene-summary:{scene.id}"
    answers = HumanReviewAnswers(
        analysis_fingerprint=content_fingerprint(base),
        rubric_version=RUBRIC_VERSION,
        reviewer="叙事顾问",
        responses=[
            ReviewResponse(
                target_id=target_id,
                verdict=ReviewVerdict.PARTIALLY_SUPPORTED,
                ratings=[],
                notes="原摘要遗漏即将离乡的行动目的。",
                proposed_correction=replacement,
            )
        ],
    )
    action_line = scene.source_span.line_end
    correction_set = CorrectionSet(
        source_fingerprint=base.screenplay.source_fingerprint,
        base_analysis_fingerprint=base_fingerprint or content_fingerprint(base),
        rubric_version=RUBRIC_VERSION,
        review_answers_fingerprint=content_fingerprint(answers),
        reviewer=answers.reviewer,
        corrections=[
            NarrativeCorrection(
                id="correction-scene-0001",
                review_target_id=target_id,
                field=CorrectionField.SCENE_SUMMARY,
                object_id=scene.id,
                expected_value_fingerprint=content_fingerprint(current_summary),
                replacement=replacement,
                rationale="结合后续启程动作补足场景目的。",
                evidence=[
                    Evidence(
                        scene_id=scene.id,
                        source_span=SourceSpan(line_start=action_line, line_end=action_line),
                        excerpt="小王进站。",
                        confidence="high",
                    )
                ],
            )
        ],
    )
    correction_path = store.root.parent / "corrections.json"
    answers_path = store.root.parent / "answers.json"
    store.write_model(correction_path, correction_set)
    store.write_model(answers_path, answers)
    return correction_path, answers_path


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _quality(breakdown: NarrativeBreakdown, *, stable: bool) -> SemanticQualityReport:
    service = NarrativeQualityService()
    pending = service.review(breakdown, sample_size=16)
    assert len(pending.human_review.targets) == 16
    responses = [
        ReviewResponse(
            target_id=target.id,
            verdict=(ReviewVerdict.SUPPORTED if stable or index > 0 else ReviewVerdict.UNSUPPORTED),
            ratings=[
                DimensionRating(dimension=dimension, score=4) for dimension in target.dimensions
            ],
            notes="经逐项核对，可作为本轮专家结论。",
        )
        for index, target in enumerate(pending.human_review.targets)
    ]
    answers = HumanReviewAnswers(
        analysis_fingerprint=pending.analysis_fingerprint,
        rubric_version=RUBRIC_VERSION,
        reviewer="叙事顾问模拟评审",
        responses=responses,
    )
    return service.review(breakdown, sample_size=16, answers=answers)


def test_dry_run_does_not_write_project_files(tmp_path: Path) -> None:
    store, base = _project(tmp_path)
    correction_path, answers_path = _correction_inputs(store, base)
    before = _snapshot(store.root)

    result = NarrativeClosureService(store).apply_corrections(
        correction_path,
        answers_path,
        dry_run=True,
    )

    assert result.dry_run is True
    assert result.exports == {}
    assert result.receipt.applied_count == 1
    assert _snapshot(store.root) == before


def test_activation_persists_receipt_and_reexports_corrected_breakdown(tmp_path: Path) -> None:
    store, base = _project(tmp_path)
    correction_path, answers_path = _correction_inputs(store, base)

    result = NarrativeClosureService(store).apply_corrections(correction_path, answers_path)

    assert result.dry_run is False
    assert set(result.exports) == {"json", "markdown"}
    assert store.read_model(store.artifact_path("correction_receipt"), CorrectionReceipt) == (
        result.receipt
    )
    active = store.read_model(store.root / "corrections" / "active.json", CorrectionSet)
    assert active.corrections[0].id == "correction-scene-0001"
    exported = NarrativeBreakdown.model_validate_json(
        Path(result.exports["json"]).read_text(encoding="utf-8")
    )
    assert exported.scene_analyses[0].summary == "小王抵达车站，准备离乡。"
    assert exported.correction_receipt == result.receipt
    assert "小王抵达车站，准备离乡。" in Path(result.exports["markdown"]).read_text(
        encoding="utf-8"
    )


def test_stale_correction_input_fails_without_activation(tmp_path: Path) -> None:
    store, base = _project(tmp_path)
    correction_path, answers_path = _correction_inputs(
        store,
        base,
        base_fingerprint="stale-analysis",
    )

    with pytest.raises(ValueError, match="分析指纹不匹配"):
        NarrativeClosureService(store).apply_corrections(correction_path, answers_path)

    assert not (store.root / "corrections" / "active.json").exists()
    assert not store.artifact_path("correction_receipt").exists()


def test_invalid_answer_schema_fails_without_activation(tmp_path: Path) -> None:
    store, base = _project(tmp_path)
    correction_path, answers_path = _correction_inputs(store, base)
    answers_path.write_text("{}", encoding="utf-8")

    with pytest.raises(ValidationError):
        NarrativeClosureService(store).apply_corrections(correction_path, answers_path)

    assert not (store.root / "corrections" / "active.json").exists()


@pytest.mark.parametrize("stable", [True, False])
def test_finalize_always_exports_stable_or_blocking_decision(
    tmp_path: Path,
    stable: bool,
) -> None:
    store, _ = _project(tmp_path)
    official = NarrativeClosureService(store)
    breakdown = official.store.read_artifact("manual_corrections", NarrativeBreakdown).data
    store.write_model(store.artifact_path("semantic_quality"), _quality(breakdown, stable=stable))

    result = official.finalize()

    assert result.report.stable is stable
    assert set(result.exports) == {"artifact", "markdown"}
    assert all(Path(path).is_file() for path in result.exports.values())
    assert store.read_model(Path(result.exports["artifact"]), ReleaseGateReport) == result.report
    markdown = Path(result.exports["markdown"]).read_text(encoding="utf-8")
    assert ("稳定，可封版" if stable else "阻断，不可封版") in markdown
