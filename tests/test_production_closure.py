"""制片规划到模拟专家评测封版的纯本地闭环测试。"""

from pathlib import Path

from movie_breakdown.application.production_closure import ProductionClosureService
from movie_breakdown.application.production_planning_context import (
    ProductionPlanningInputs,
)
from movie_breakdown.domain.production_correction import (
    ProductionCorrectionSet,
    ReplaceEntityRegistryCorrection,
)
from movie_breakdown.domain.production_planning import (
    NormalizationBasis,
    ResolutionStatus,
)
from movie_breakdown.domain.production_release import (
    ProductionReleaseProfile,
    ProductionReleaseState,
)
from movie_breakdown.domain.production_review import (
    ProductionReviewerKind,
    ProductionReviewTargetKind,
    ProductionReviewVerdict,
)
from movie_breakdown.infrastructure.fingerprint import content_fingerprint
from movie_breakdown.infrastructure.production_storage import ProductionStore
from movie_breakdown.infrastructure.storage import ProjectStore
from tests.factories import make_screenplay
from tests.production_factories import make_production_breakdown


class _StaticPlanningLoader:
    """测试使用的无文件、无模型规划输入加载器。"""

    def __init__(self, inputs: ProductionPlanningInputs) -> None:
        self.inputs = inputs

    def load(self) -> ProductionPlanningInputs:
        """返回固定测试输入。"""
        return self.inputs


def test_dry_run_writes_nothing_and_simulated_expert_reaches_evaluation(tmp_path: Path) -> None:
    screenplay = make_screenplay()
    breakdown = make_production_breakdown(screenplay)
    store = ProductionStore(ProjectStore(tmp_path))
    service = ProductionClosureService(
        store,
        loader=_StaticPlanningLoader(ProductionPlanningInputs(screenplay, breakdown)),
    )
    planned = service.plan()
    pending = service.review()
    answers, correction_set = _entity_correction(planned.plan, pending)
    before = _snapshot(tmp_path)

    dry_run = service.correct(correction_set, answers, dry_run=True)

    assert dry_run.generation_id is None
    assert dry_run.exports == {}
    assert _snapshot(tmp_path) == before

    corrected = service.correct(correction_set, answers)
    assert corrected.generation_id
    assert service.repository.load_official_plan() == corrected.plan

    final_pending = service.review()
    final_answers = final_pending.answers
    final_answers.reviewer = "AI 模拟制片专家"
    final_answers.reviewer_kind = ProductionReviewerKind.AI_SIMULATED
    final_answers.responses = [
        response.model_copy(
            update={
                "verdict": ProductionReviewVerdict.SUPPORTED,
                "notes": "已逐项核对，仅形成评测版结论。",
                "ratings": [
                    rating.model_copy(update={"score": 5, "comment": "已核对。"})
                    for rating in response.ratings
                ],
            }
        )
        for response in final_answers.responses
    ]
    service.review(final_answers)

    released = service.finalize(ProductionReleaseProfile.EVALUATION)

    assert released.report.releasable is True
    assert released.report.state == ProductionReleaseState.EVALUATION_READY
    assert released.release_id
    assert set(released.exports) == {"release-evaluation.json", "release-evaluation.md"}


def _entity_correction(plan, pending):
    """为初次评审构造候选实体确认答案和结构化修正集。"""
    target = next(
        item for item in pending.report.targets if item.kind == ProductionReviewTargetKind.ENTITY
    )
    correction_id = "correction-confirm-entity"
    answers = pending.answers
    answers.reviewer = "AI 模拟制片专家"
    answers.reviewer_kind = ProductionReviewerKind.AI_SIMULATED
    answers.responses = [
        response.model_copy(
            update={
                "verdict": ProductionReviewVerdict.NEEDS_CORRECTION,
                "notes": "需把同一角色的跨场出现项归一。",
                "correction_ids": [correction_id],
            }
        )
        if response.target_id == target.id
        else response
        for response in answers.responses
    ]
    replacement = [
        entity.model_copy(
            update={
                "status": ResolutionStatus.CONFIRMED,
                "basis": NormalizationBasis.AI_REVIEWED,
                "notes": ["AI 模拟专家确认，仅供评测版。"],
            }
        )
        for entity in plan.entities
    ]
    correction = ReplaceEntityRegistryCorrection(
        id=correction_id,
        review_target_ids=[target.id],
        expected_value_fingerprint=content_fingerprint(plan.entities),
        rationale="把同一角色的跨场出现项合并为连续性实体。",
        evidence=target.evidence,
        replacement=replacement,
    )
    correction_set = ProductionCorrectionSet(
        source_fingerprint=plan.source_fingerprint,
        base_plan_fingerprint=content_fingerprint(plan),
        target_set_fingerprint=pending.report.target_set_fingerprint,
        review_answers_fingerprint=content_fingerprint(answers),
        rubric_version=pending.report.rubric_version,
        safety_policy_version=pending.report.safety_policy_version,
        reviewer=answers.reviewer,
        reviewer_kind=answers.reviewer_kind,
        corrections=[correction],
    )
    return answers, correction_set


def _snapshot(root: Path) -> dict[str, bytes]:
    """读取目录中全部文件字节用于验证 dry-run 零写入。"""
    return {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }
