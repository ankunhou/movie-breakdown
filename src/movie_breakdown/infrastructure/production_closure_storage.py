"""制片规划、专家修正 generation 与不可变发布的本地仓库入口。"""

from __future__ import annotations

from pathlib import Path

from movie_breakdown.domain.production_planning import ProductionPlan
from movie_breakdown.domain.production_planning_validation import (
    ProductionPlanningValidationReport,
)
from movie_breakdown.domain.production_review import (
    ProductionReviewAnswers,
    ProductionReviewReport,
)
from movie_breakdown.infrastructure.fingerprint import content_fingerprint
from movie_breakdown.infrastructure.production_closure_storage_support import (
    ProductionClosureStorageError,
)
from movie_breakdown.infrastructure.production_generation_storage import (
    _ProductionGenerationStorage,
)
from movie_breakdown.infrastructure.production_release_storage import (
    _ProductionReleaseStorage,
)
from movie_breakdown.infrastructure.production_storage import ProductionStore

__all__ = ["ProductionClosureRepository", "ProductionClosureStorageError"]


class ProductionClosureRepository(
    _ProductionGenerationStorage,
    _ProductionReleaseStorage,
):
    """管理一个制片作用域的本地规划、评审、修正与发布快照。

    Attributes:
        store: 已绑定主项目且提供原子 JSON 读写的制片仓库。
    """

    def __init__(self, store: ProductionStore) -> None:
        """创建闭环仓库但不提前建立目录。

        Args:
            store: 当前项目的独立制片仓库。
        """
        super().__init__(store)

    def save_base(
        self,
        plan: ProductionPlan,
        validation: ProductionPlanningValidationReport,
    ) -> tuple[Path, Path]:
        """原子保存本地派生的基础规划及其分级校验。

        Args:
            plan: 尚未应用人工修正的基础制片规划。
            validation: 必须绑定该规划指纹的校验报告。

        Returns:
            基础规划和校验报告的绝对路径。

        Raises:
            ProductionClosureStorageError: 校验报告已经过期。
        """
        _require_plan_validation(plan, validation)
        plan_path = self.planning_dir / "base.json"
        validation_path = self.planning_dir / "validation.json"
        self.store.project_store.write_model(plan_path, plan)
        self.store.project_store.write_model(validation_path, validation)
        return plan_path, validation_path

    def load_base(self) -> tuple[ProductionPlan, ProductionPlanningValidationReport]:
        """读取并重新校验基础规划和分级校验报告。

        Returns:
            通过 Pydantic Schema 与内容指纹校验的基础规划和报告。

        Raises:
            ProductionClosureStorageError: 任一文件缺失、损坏或过期。
        """
        plan = self._read_required(self.planning_dir / "base.json", ProductionPlan, "基础规划")
        validation = self._read_required(
            self.planning_dir / "validation.json",
            ProductionPlanningValidationReport,
            "基础规划校验报告",
        )
        _require_plan_validation(plan, validation)
        return plan, validation

    def save_review(
        self,
        report: ProductionReviewReport,
        answers_template: ProductionReviewAnswers,
    ) -> tuple[Path, Path]:
        """保存当前规划的专家评审报告和可填写答案模板。

        Args:
            report: 绑定完整目标集的专家评审报告。
            answers_template: 与报告版本、规划和目标集一致的答案模板。

        Returns:
            评审报告和答案模板的绝对路径。

        Raises:
            ProductionClosureStorageError: 报告与模板的绑定不一致。
        """
        _require_review_bindings(report, answers_template)
        report_path = self.reviews_dir / "report.json"
        template_path = self.reviews_dir / "answers-template.json"
        self.store.project_store.write_model(report_path, report)
        self.store.project_store.write_model(template_path, answers_template)
        return report_path, template_path

    def load_review(self) -> tuple[ProductionReviewReport, ProductionReviewAnswers]:
        """读取并重新校验当前专家评审报告和答案模板。

        Returns:
            通过严格绑定校验的评审报告和答案模板。

        Raises:
            ProductionClosureStorageError: 任一文件缺失、损坏或不匹配。
        """
        report = self._read_required(
            self.reviews_dir / "report.json",
            ProductionReviewReport,
            "制片专家评审报告",
        )
        template = self._read_required(
            self.reviews_dir / "answers-template.json",
            ProductionReviewAnswers,
            "制片专家答案模板",
        )
        _require_review_bindings(report, template)
        return report, template


def _require_plan_validation(
    plan: ProductionPlan,
    validation: ProductionPlanningValidationReport,
) -> None:
    """拒绝不属于当前规划内容的分级校验报告。"""
    if validation.plan_fingerprint != content_fingerprint(plan):
        raise ProductionClosureStorageError("制片规划校验报告与规划内容指纹不一致。")


def _require_review_bindings(
    report: ProductionReviewReport,
    answers: ProductionReviewAnswers,
) -> None:
    """拒绝规划、目标集、标准版本或答案结构不一致的评审模板。"""
    fields = (
        "plan_fingerprint",
        "target_set_fingerprint",
        "rubric_version",
        "safety_policy_version",
    )
    if any(getattr(report, field) != getattr(answers, field) for field in fields):
        raise ProductionClosureStorageError("制片专家评审报告与答案模板绑定不一致。")
    report_targets = [item.id for item in report.targets]
    answer_targets = [item.target_id for item in answers.responses]
    if report_targets != answer_targets:
        raise ProductionClosureStorageError("制片专家答案模板没有按报告顺序覆盖完整目标集。")
