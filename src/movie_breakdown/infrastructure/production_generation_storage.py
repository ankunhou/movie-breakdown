"""人工修正 generation 的内容寻址持久化与激活指针。"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ValidationError

from movie_breakdown.domain.production_correction import (
    ProductionCorrectionReceipt,
    ProductionCorrectionSet,
)
from movie_breakdown.domain.production_planning import ProductionPlan
from movie_breakdown.domain.production_planning_validation import (
    ProductionPlanningValidationReport,
)
from movie_breakdown.domain.production_review import ProductionReviewAnswers
from movie_breakdown.infrastructure.fingerprint import content_fingerprint
from movie_breakdown.infrastructure.production_closure_contracts import (
    ProductionCorrectionGeneration,
    ProductionGenerationManifest,
)
from movie_breakdown.infrastructure.production_closure_storage_support import (
    ProductionClosureStorageError,
    _ProductionStorageSupport,
)


class _ProductionGenerationStorage(_ProductionStorageSupport):
    """保存、激活并严格重放完整人工修正 generation。"""

    def save_correction_generation(
        self,
        correction_set: ProductionCorrectionSet,
        review_answers: ProductionReviewAnswers,
        receipt: ProductionCorrectionReceipt,
        official_plan: ProductionPlan,
        validation: ProductionPlanningValidationReport,
    ) -> ProductionGenerationManifest:
        """完整写入内容寻址 generation 后最后激活原子指针。

        Args:
            correction_set: 与基础规划和专家答案绑定的累计修正集合。
            review_answers: 产生修正操作的完整专家答案。
            receipt: 原子应用修正后生成的审计回执。
            official_plan: 应用修正后的正式规划快照。
            validation: 绑定正式规划指纹的分级校验报告。

        Returns:
            已激活 generation 的不可变内容清单。

        Raises:
            ProductionClosureStorageError: 审计链不一致或同 ID 文件被篡改。
            OSError: 任一文件无法原子写入；此时不会提前更新激活指针。
        """
        try:
            manifest = _make_generation_manifest(
                correction_set,
                review_answers,
                receipt,
                official_plan,
                validation,
            )
            bundle = ProductionCorrectionGeneration(
                manifest=manifest,
                correction_set=correction_set,
                review_answers=review_answers,
                receipt=receipt,
                official_plan=official_plan,
                validation=validation,
            )
        except (ValidationError, ValueError) as error:
            raise ProductionClosureStorageError(
                f"制片修正 generation 审计链无效：{error}"
            ) from error
        directory = self.generations_dir / manifest.generation_id
        self._write_generation(directory, bundle)
        self.store.project_store.write_model(self.active_path, manifest)
        return manifest

    def load_active_generation(self) -> ProductionCorrectionGeneration | None:
        """读取激活指针指向的完整 generation 并重验全部指纹。

        Returns:
            当前完整修正 generation；尚未激活人工修正时返回 ``None``。

        Raises:
            ProductionClosureStorageError: 指针或 generation 缺失、损坏或被篡改。
        """
        if not self.active_path.is_file():
            return None
        active = self._read_required(
            self.active_path,
            ProductionGenerationManifest,
            "制片修正激活指针",
        )
        directory = self.generations_dir / active.generation_id
        manifest = self._read_required(
            directory / "manifest.json",
            ProductionGenerationManifest,
            "制片修正 generation 清单",
        )
        if manifest != active:
            raise ProductionClosureStorageError("制片修正激活指针与 generation 清单不一致。")
        return self._load_generation(directory, manifest)

    def load_official_plan(self) -> ProductionPlan:
        """从激活 generation 读取正式规划，无激活修正时回退基础规划。

        Returns:
            当前唯一正式来源的制片规划。

        Raises:
            ProductionClosureStorageError: 激活 generation 或基础规划无效。
        """
        base_plan, _ = self.load_base()
        generation = self.load_active_generation()
        if generation is None:
            return base_plan
        if generation.manifest.base_plan_fingerprint != content_fingerprint(base_plan):
            raise ProductionClosureStorageError("激活制片修正绑定的基础规划已经过期。")
        return generation.official_plan

    def _load_generation(
        self,
        directory: Path,
        manifest: ProductionGenerationManifest,
    ) -> ProductionCorrectionGeneration:
        """从已验证指针指向的目录加载并交叉校验 generation。"""
        try:
            return ProductionCorrectionGeneration(
                manifest=manifest,
                correction_set=self._read_required(
                    directory / "correction_set.json",
                    ProductionCorrectionSet,
                    "制片修正集合",
                ),
                review_answers=self._read_required(
                    directory / "review_answers.json",
                    ProductionReviewAnswers,
                    "制片专家答案",
                ),
                receipt=self._read_required(
                    directory / "receipt.json",
                    ProductionCorrectionReceipt,
                    "制片修正回执",
                ),
                official_plan=self._read_required(
                    directory / "official_plan.json",
                    ProductionPlan,
                    "正式制片规划",
                ),
                validation=self._read_required(
                    directory / "validation.json",
                    ProductionPlanningValidationReport,
                    "正式制片规划校验报告",
                ),
            )
        except ValidationError as error:
            raise ProductionClosureStorageError(f"制片修正 generation 指纹无效：{error}") from error

    def _write_generation(
        self,
        directory: Path,
        bundle: ProductionCorrectionGeneration,
    ) -> None:
        """按固定顺序写完 generation 内容和清单，不更新激活指针。"""
        items: tuple[tuple[str, BaseModel], ...] = (
            ("correction_set.json", bundle.correction_set),
            ("review_answers.json", bundle.review_answers),
            ("receipt.json", bundle.receipt),
            ("official_plan.json", bundle.official_plan),
            ("validation.json", bundle.validation),
            ("manifest.json", bundle.manifest),
        )
        for name, model in items:
            self._write_immutable(directory / name, model)


def _make_generation_manifest(
    correction_set: ProductionCorrectionSet,
    answers: ProductionReviewAnswers,
    receipt: ProductionCorrectionReceipt,
    plan: ProductionPlan,
    validation: ProductionPlanningValidationReport,
) -> ProductionGenerationManifest:
    """根据五个严格产物构造可自校验的 generation 清单。"""
    values = {
        "schema_version": "1.0",
        "source_fingerprint": plan.source_fingerprint,
        "base_plan_fingerprint": correction_set.base_plan_fingerprint,
        "target_set_fingerprint": correction_set.target_set_fingerprint,
        "correction_set_fingerprint": content_fingerprint(correction_set),
        "review_answers_fingerprint": content_fingerprint(answers),
        "receipt_fingerprint": content_fingerprint(receipt),
        "official_plan_fingerprint": content_fingerprint(plan),
        "validation_fingerprint": content_fingerprint(validation),
    }
    return ProductionGenerationManifest(
        generation_id=content_fingerprint(values),
        **values,
    )
