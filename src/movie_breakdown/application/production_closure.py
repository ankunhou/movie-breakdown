"""编排制片规划、专家复核、原子修正与分级封版的纯本地闭环。"""

from __future__ import annotations

from movie_breakdown.application.production_closure_results import (
    ProductionCorrectionClosureResult,
    ProductionFinalizeClosureResult,
    ProductionPlanClosureResult,
    ProductionReviewClosureResult,
)
from movie_breakdown.application.production_corrections import (
    ProductionCorrectionService,
)
from movie_breakdown.application.production_plan_validation import (
    ProductionPlanValidationService,
)
from movie_breakdown.application.production_planning import ProductionPlanBuilder
from movie_breakdown.application.production_planning_context import (
    ProductionPlanningContextLoader,
)
from movie_breakdown.application.production_planning_export import (
    ProductionPlanningExportService,
)
from movie_breakdown.application.production_release import ProductionReleaseService
from movie_breakdown.application.production_release_export import (
    render_production_release_json,
    render_production_release_markdown,
)
from movie_breakdown.application.production_review import ProductionReviewService
from movie_breakdown.domain.production_correction import ProductionCorrectionSet
from movie_breakdown.domain.production_planning import ProductionPlan
from movie_breakdown.domain.production_planning_validation import (
    ProductionPlanningValidationReport,
)
from movie_breakdown.domain.production_release import (
    ProductionReleaseProfile,
    ProductionReleaseReport,
)
from movie_breakdown.domain.production_review import ProductionReviewAnswers
from movie_breakdown.infrastructure.fingerprint import content_fingerprint
from movie_breakdown.infrastructure.production_closure_storage import (
    ProductionClosureRepository,
    ProductionClosureStorageError,
)
from movie_breakdown.infrastructure.production_storage import ProductionStore


class ProductionClosureService:
    """在现有制片模型产物之上执行不调用模型的版本化闭环。"""

    def __init__(
        self,
        store: ProductionStore,
        repository: ProductionClosureRepository | None = None,
        loader: ProductionPlanningContextLoader | None = None,
    ) -> None:
        """绑定存储并创建各职责清晰的本地领域服务。

        Args:
            store: 当前项目独立制片作用域的存储。
            repository: 可替换的内容寻址闭环仓库。
            loader: 可替换的只读规划输入加载器。
        """
        self.store = store
        self.repository = repository or ProductionClosureRepository(store)
        self._loader = loader or ProductionPlanningContextLoader(store)
        self._builder = ProductionPlanBuilder()
        self._validator = ProductionPlanValidationService()
        self._reviewer = ProductionReviewService()
        self._corrector = ProductionCorrectionService()
        self._releaser = ProductionReleaseService()
        self._exporter = ProductionPlanningExportService()

    def plan(self) -> ProductionPlanClosureResult:
        """重建并保存当前基础规划、校验和完整工作底稿导出。

        Returns:
            基础规划、准备度报告和八个用户可见导出路径。
        """
        inputs = self._loader.load()
        plan = self._builder.build(inputs.screenplay, inputs.breakdown)
        validation = self._validator.validate(inputs.screenplay, inputs.breakdown, plan)
        self.repository.save_base(plan, validation)
        exports = self._write_planning_exports(plan, validation)
        return ProductionPlanClosureResult(plan, validation, exports)

    def review(
        self,
        answers: ProductionReviewAnswers | None = None,
    ) -> ProductionReviewClosureResult:
        """生成全量强制目标或严格导入与当前指纹匹配的专家答案。

        Args:
            answers: 可选的外部专家答案；省略时生成空白模板。

        Returns:
            当前评审报告、完整答案和持久化路径。
        """
        inputs = self._loader.load()
        plan = self.repository.load_official_plan()
        report = self._reviewer.review(
            inputs.screenplay,
            inputs.breakdown,
            plan,
            answers,
        )
        merged_answers = self._reviewer.answers_template(report)
        report_path, answers_path = self.repository.save_review(report, merged_answers)
        return ProductionReviewClosureResult(
            report,
            merged_answers,
            {"report": str(report_path), "answers": str(answers_path)},
        )

    def correct(
        self,
        correction_set: ProductionCorrectionSet,
        answers: ProductionReviewAnswers,
        *,
        dry_run: bool = False,
    ) -> ProductionCorrectionClosureResult:
        """预演或原子激活与基础规划及专家答案绑定的累计修正。

        Args:
            correction_set: 替换完整作用域的结构化累计修正集。
            answers: 产生这些修正的专家答案。
            dry_run: 为真时只在内存校验，不写任何闭环或导出文件。

        Returns:
            修正规划、校验、回执、可选 generation ID 与导出路径。
        """
        inputs = self._loader.load()
        base_plan, _ = self.repository.load_base()
        corrected, receipt = self._corrector.apply(
            inputs.screenplay,
            inputs.breakdown,
            base_plan,
            correction_set,
            answers,
        )
        validation = self._validator.validate(
            inputs.screenplay,
            inputs.breakdown,
            corrected,
        )
        if dry_run:
            return ProductionCorrectionClosureResult(
                corrected,
                validation,
                receipt,
                None,
                {},
            )
        manifest = self.repository.save_correction_generation(
            correction_set,
            answers,
            receipt,
            corrected,
            validation,
        )
        exports = self._write_planning_exports(corrected, validation)
        return ProductionCorrectionClosureResult(
            corrected,
            validation,
            receipt,
            manifest.generation_id,
            exports,
        )

    def finalize(
        self,
        profile: ProductionReleaseProfile,
    ) -> ProductionFinalizeClosureResult:
        """重新计算当前正式规划、最终评审与指定等级的封版门禁。

        Args:
            profile: 请求评测封版或专业稳定版。

        Returns:
            无论通过或阻断都会保存的报告、可选发布 ID 和导出路径。

        Raises:
            ProductionClosureStorageError: 最终评审不是当前规划的确定性结果。
        """
        inputs = self._loader.load()
        plan = self.repository.load_official_plan()
        validation = self._validator.validate(inputs.screenplay, inputs.breakdown, plan)
        saved_report, answers = self.repository.load_review()
        current_review = self._reviewer.review(
            inputs.screenplay,
            inputs.breakdown,
            plan,
            answers,
        )
        if content_fingerprint(saved_report) != content_fingerprint(current_review):
            raise ProductionClosureStorageError("保存的制片专家报告无法由当前答案确定性重建。")
        generation = self.repository.load_active_generation()
        receipt = generation.receipt if generation is not None else None
        report = self._releaser.evaluate(
            plan,
            validation,
            current_review,
            profile,
            receipt,
        )
        self.repository.save_release_report(report)
        exports = self._write_release_exports(report)
        release_id = None
        if report.releasable:
            manifest = self.repository.save_immutable_release(
                report,
                plan,
                validation,
                current_review,
                receipt,
            )
            release_id = manifest.release_id
        return ProductionFinalizeClosureResult(report, release_id, exports)

    def _write_planning_exports(
        self,
        plan: ProductionPlan,
        validation: ProductionPlanningValidationReport,
    ) -> dict[str, str]:
        """原子写入固定名称的完整规划工作底稿。"""
        contents = self._exporter.render_contents(plan, validation)
        return {
            name: str(self.store.write_export(name, content)) for name, content in contents.items()
        }

    def _write_release_exports(
        self,
        report: ProductionReleaseReport,
    ) -> dict[str, str]:
        """原子写入带等级名称的最新封版 JSON 与 Markdown。"""
        prefix = f"release-{report.profile.value}"
        contents = {
            f"{prefix}.json": render_production_release_json(report),
            f"{prefix}.md": render_production_release_markdown(report),
        }
        return {
            name: str(self.store.write_export(name, content)) for name, content in contents.items()
        }
