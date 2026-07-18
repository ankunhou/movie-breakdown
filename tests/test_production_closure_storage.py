from __future__ import annotations

from pathlib import Path

import pytest

from movie_breakdown.application.production_plan_validation import (
    ProductionPlanValidationService,
)
from movie_breakdown.application.production_planning import ProductionPlanBuilder
from movie_breakdown.application.production_release import ProductionReleaseService
from movie_breakdown.application.production_review import ProductionReviewService
from movie_breakdown.domain.production_correction import (
    ProductionCorrectionReceipt,
    ProductionCorrectionSet,
    ReplaceEntityRegistryCorrection,
)
from movie_breakdown.domain.production_planning import ProductionPlan, ResolutionStatus
from movie_breakdown.domain.production_release import ProductionReleaseProfile
from movie_breakdown.domain.production_review import (
    ProductionDimensionRating,
    ProductionReviewAnswers,
    ProductionReviewerKind,
    ProductionReviewResponse,
    ProductionReviewVerdict,
)
from movie_breakdown.infrastructure.fingerprint import content_fingerprint
from movie_breakdown.infrastructure.production_closure_storage import (
    ProductionClosureRepository,
    ProductionClosureStorageError,
)
from movie_breakdown.infrastructure.production_storage import ProductionStore
from movie_breakdown.infrastructure.storage import ProjectStore
from tests.factories import make_screenplay
from tests.production_factories import make_production_breakdown, make_production_records


def _context():
    screenplay = make_screenplay()
    breakdown = make_production_breakdown(screenplay)
    plan = ProductionPlanBuilder().build(screenplay, breakdown)
    validation = ProductionPlanValidationService().validate(screenplay, breakdown, plan)
    return screenplay, breakdown, plan, validation


def _repository(tmp_path: Path) -> tuple[ProductionStore, ProductionClosureRepository]:
    production = ProductionStore(ProjectStore(tmp_path / "project"))
    return production, ProductionClosureRepository(production)


def _generation_inputs(plan, validation, review, *, reviewer: str):
    target = review.targets[0]
    correction_id = f"correction-{content_fingerprint(reviewer)[:12]}"
    response = review.responses[0].model_copy(
        update={
            "verdict": ProductionReviewVerdict.NEEDS_CORRECTION,
            "notes": "已核对跨场实体，需要建立受控注册表快照。",
            "correction_ids": [correction_id],
        }
    )
    answers = ProductionReviewAnswers(
        plan_fingerprint=review.plan_fingerprint,
        target_set_fingerprint=review.target_set_fingerprint,
        rubric_version=review.rubric_version,
        safety_policy_version=review.safety_policy_version,
        reviewer=reviewer,
        reviewer_kind=ProductionReviewerKind.AI_SIMULATED,
        reviewer_roles=["制片统筹"],
        responses=[response],
    )
    correction_set = ProductionCorrectionSet(
        source_fingerprint=plan.source_fingerprint,
        base_plan_fingerprint=content_fingerprint(plan),
        target_set_fingerprint=review.target_set_fingerprint,
        review_answers_fingerprint=content_fingerprint(answers),
        rubric_version=review.rubric_version,
        safety_policy_version=review.safety_policy_version,
        reviewer=reviewer,
        reviewer_kind=ProductionReviewerKind.AI_SIMULATED,
        corrections=[
            ReplaceEntityRegistryCorrection(
                id=correction_id,
                review_target_ids=[target.id],
                expected_value_fingerprint=content_fingerprint(plan.entities),
                rationale="以当前完整注册表建立可审计的累计修正快照。",
                evidence=target.evidence,
                replacement=plan.entities,
            )
        ],
    )
    receipt = ProductionCorrectionReceipt(
        source_fingerprint=plan.source_fingerprint,
        base_plan_fingerprint=content_fingerprint(plan),
        corrected_plan_fingerprint=content_fingerprint(plan),
        target_set_fingerprint=review.target_set_fingerprint,
        correction_set_fingerprint=content_fingerprint(correction_set),
        review_answers_fingerprint=content_fingerprint(answers),
        rubric_version=review.rubric_version,
        safety_policy_version=review.safety_policy_version,
        reviewer=reviewer,
        reviewer_kind=ProductionReviewerKind.AI_SIMULATED,
        applied_correction_ids=[correction_id],
        applied_count=1,
    )
    return correction_set, answers, receipt, plan, validation


def _confirm_entities(plan: ProductionPlan) -> None:
    plan.entities = [
        item.model_copy(update={"status": ResolutionStatus.CONFIRMED}) for item in plan.entities
    ]
    entity_ids = {item.id for item in plan.entities}
    plan.occurrences = [
        item.model_copy(
            update={
                "resolution_status": (
                    ResolutionStatus.CONFIRMED
                    if item.entity_id in entity_ids
                    else item.resolution_status
                )
            }
        )
        for item in plan.occurrences
    ]


def _completed_review(screenplay, breakdown, plan):
    service = ProductionReviewService()
    pending = service.review(screenplay, breakdown, plan)
    answers = ProductionReviewAnswers(
        plan_fingerprint=pending.plan_fingerprint,
        target_set_fingerprint=pending.target_set_fingerprint,
        rubric_version=pending.rubric_version,
        safety_policy_version=pending.safety_policy_version,
        reviewer="制片专家模拟",
        reviewer_kind=ProductionReviewerKind.AI_SIMULATED,
        reviewer_roles=["制片统筹"],
        responses=[
            ProductionReviewResponse(
                target_id=target.id,
                verdict=ProductionReviewVerdict.SUPPORTED,
                ratings=[
                    ProductionDimensionRating(dimension=dimension, score=5, comment="已核对。")
                    for dimension in target.dimensions
                ],
                notes="已逐行核对证据与执行边界。",
            )
            for target in pending.targets
        ],
    )
    return service.review(screenplay, breakdown, plan, answers)


def test_base_and_review_round_trip_with_official_fallback(tmp_path: Path) -> None:
    screenplay, breakdown, plan, validation = _context()
    _, repository = _repository(tmp_path)
    review_service = ProductionReviewService()
    report = review_service.review(screenplay, breakdown, plan)
    template = review_service.answers_template(report)

    repository.save_base(plan, validation)
    repository.save_review(report, template)

    assert repository.load_base() == (plan, validation)
    assert repository.load_review() == (report, template)
    assert repository.load_official_plan() == plan

    stale = validation.model_copy(update={"plan_fingerprint": "stale"})
    with pytest.raises(ProductionClosureStorageError, match="内容指纹"):
        repository.save_base(plan, stale)


def test_generation_writes_active_pointer_last_and_preserves_jsonl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screenplay, breakdown, plan, validation = _context()
    production, repository = _repository(tmp_path)
    repository.save_base(plan, validation)
    review = ProductionReviewService().review(screenplay, breakdown, plan)
    inputs = _generation_inputs(plan, validation, review, reviewer="模拟专家甲")
    production.write_jsonl("scene_elements", make_production_records(screenplay))
    jsonl_path = production.artifacts_dir / "scene_elements.jsonl"
    original_jsonl = jsonl_path.read_bytes()
    writes: list[Path] = []
    original_write = production.project_store.write_model

    def tracked_write(path: Path, model) -> None:
        writes.append(path)
        original_write(path, model)

    monkeypatch.setattr(production.project_store, "write_model", tracked_write)
    manifest = repository.save_correction_generation(*inputs)

    assert writes[-1] == repository.active_path
    assert (repository.generations_dir / manifest.generation_id / "manifest.json").is_file()
    assert repository.load_active_generation().official_plan == plan
    assert repository.load_official_plan() == plan
    assert jsonl_path.read_bytes() == original_jsonl


def test_failed_generation_keeps_previous_active_pointer(tmp_path: Path, monkeypatch) -> None:
    screenplay, breakdown, plan, validation = _context()
    production, repository = _repository(tmp_path)
    repository.save_base(plan, validation)
    review = ProductionReviewService().review(screenplay, breakdown, plan)
    repository.save_correction_generation(
        *_generation_inputs(plan, validation, review, reviewer="模拟专家甲")
    )
    active_before = repository.active_path.read_bytes()
    original_write = production.project_store.write_model

    def fail_before_pointer(path: Path, model) -> None:
        if path.name == "receipt.json":
            raise OSError("模拟 generation 中途失败")
        original_write(path, model)

    monkeypatch.setattr(production.project_store, "write_model", fail_before_pointer)
    with pytest.raises(OSError, match="中途失败"):
        repository.save_correction_generation(
            *_generation_inputs(plan, validation, review, reviewer="模拟专家乙")
        )

    assert repository.active_path.read_bytes() == active_before
    assert repository.load_active_generation().receipt.reviewer == "模拟专家甲"


def test_generation_detects_valid_schema_content_tampering(tmp_path: Path) -> None:
    screenplay, breakdown, plan, validation = _context()
    production, repository = _repository(tmp_path)
    review = ProductionReviewService().review(screenplay, breakdown, plan)
    manifest = repository.save_correction_generation(
        *_generation_inputs(plan, validation, review, reviewer="模拟专家甲")
    )
    official_path = repository.generations_dir / manifest.generation_id / "official_plan.json"
    tampered = plan.model_copy(update={"schema_version": "tampered"})
    production.project_store.write_model(official_path, tampered)

    with pytest.raises(ProductionClosureStorageError, match="指纹"):
        repository.load_active_generation()


def test_official_plan_rejects_active_generation_after_base_changes(tmp_path: Path) -> None:
    screenplay, breakdown, plan, validation = _context()
    _, repository = _repository(tmp_path)
    repository.save_base(plan, validation)
    review = ProductionReviewService().review(screenplay, breakdown, plan)
    repository.save_correction_generation(
        *_generation_inputs(plan, validation, review, reviewer="模拟专家甲")
    )
    changed = plan.model_copy(update={"schema_version": "2.0"})
    changed_validation = validation.model_copy(
        update={"plan_fingerprint": content_fingerprint(changed)}
    )
    repository.save_base(changed, changed_validation)

    with pytest.raises(ProductionClosureStorageError, match="基础规划已经过期"):
        repository.load_official_plan()


def test_release_report_and_immutable_archive_are_separated(tmp_path: Path) -> None:
    screenplay, breakdown, plan, _ = _context()
    _confirm_entities(plan)
    validation = ProductionPlanValidationService().validate(screenplay, breakdown, plan)
    review = _completed_review(screenplay, breakdown, plan)
    release_service = ProductionReleaseService()
    report = release_service.evaluate(
        plan,
        validation,
        review,
        ProductionReleaseProfile.EVALUATION,
    )
    _, repository = _repository(tmp_path)

    repository.save_release_report(report)
    manifest = repository.save_immutable_release(report, plan, validation, review)
    archive = repository.load_immutable_release(manifest.release_id)

    assert archive.report == report
    assert archive.official_plan == plan
    assert repository.load_release_report() == report

    blocked = release_service.evaluate(
        plan,
        validation,
        review,
        ProductionReleaseProfile.PROFESSIONAL,
    )
    repository.save_release_report(blocked)
    assert repository.load_release_report() == blocked
    with pytest.raises(ProductionClosureStorageError, match="不能建立不可变发布"):
        repository.save_immutable_release(blocked, plan, validation, review)
