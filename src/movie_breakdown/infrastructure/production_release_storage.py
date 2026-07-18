"""制片发布门禁报告与内容寻址不可变归档的本地存储。"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ValidationError

from movie_breakdown.domain.production_correction import ProductionCorrectionReceipt
from movie_breakdown.domain.production_planning import ProductionPlan
from movie_breakdown.domain.production_planning_validation import (
    ProductionPlanningValidationReport,
)
from movie_breakdown.domain.production_release import ProductionReleaseReport
from movie_breakdown.domain.production_review import ProductionReviewReport
from movie_breakdown.infrastructure.fingerprint import content_fingerprint
from movie_breakdown.infrastructure.production_closure_contracts import (
    ProductionImmutableRelease,
    ProductionReleaseManifest,
)
from movie_breakdown.infrastructure.production_closure_storage_support import (
    ProductionClosureStorageError,
    _ProductionStorageSupport,
)


class _ProductionReleaseStorage(_ProductionStorageSupport):
    """保存最新门禁结论并维护可验证的不可变发布归档。"""

    def save_release_report(self, report: ProductionReleaseReport) -> Path:
        """原子保存最新发布门禁报告，包括被阻断的结论。

        Args:
            report: 已由发布服务完整覆盖全部检查的门禁报告。

        Returns:
            最新发布门禁报告的绝对路径。
        """
        self.store.project_store.write_model(self.release_report_path, report)
        return self.release_report_path

    def load_release_report(self) -> ProductionReleaseReport:
        """读取最新发布门禁报告。

        Returns:
            通过严格 Pydantic 校验的最新门禁报告。

        Raises:
            ProductionClosureStorageError: 报告缺失或损坏。
        """
        return self._read_required(
            self.release_report_path,
            ProductionReleaseReport,
            "制片发布门禁报告",
        )

    def save_immutable_release(
        self,
        report: ProductionReleaseReport,
        official_plan: ProductionPlan,
        validation: ProductionPlanningValidationReport,
        review_report: ProductionReviewReport,
        correction_receipt: ProductionCorrectionReceipt | None = None,
    ) -> ProductionReleaseManifest:
        """保存通过门禁且内容寻址的不可变制片发布归档。

        Args:
            report: 必须已经允许发布的门禁报告。
            official_plan: 门禁报告绑定的正式规划。
            validation: 与正式规划指纹一致的分级校验报告。
            review_report: 与正式规划及门禁目标集一致的专家评审报告。
            correction_receipt: 正式规划来自人工修正时的可选审计回执。

        Returns:
            不可变发布归档的内容清单。

        Raises:
            ProductionClosureStorageError: 发布绑定不一致或同 ID 文件被篡改。
        """
        try:
            manifest = _make_release_manifest(
                report,
                official_plan,
                validation,
                review_report,
                correction_receipt,
            )
            archive = ProductionImmutableRelease(
                manifest=manifest,
                report=report,
                official_plan=official_plan,
                validation=validation,
                review_report=review_report,
                correction_receipt=correction_receipt,
            )
        except (ValidationError, ValueError) as error:
            raise ProductionClosureStorageError(f"不可变制片发布审计链无效：{error}") from error
        self._write_release(self.releases_dir / "immutable" / manifest.release_id, archive)
        return manifest

    def load_immutable_release(self, release_id: str) -> ProductionImmutableRelease:
        """按内容 ID 读取并重验一个不可变制片发布归档。

        Args:
            release_id: 六十四位小写 SHA-256 发布 ID。

        Returns:
            完成全部指纹交叉校验的不可变发布快照。

        Raises:
            ProductionClosureStorageError: ID 非法或归档缺失、损坏、被篡改。
        """
        if len(release_id) != 64 or any(char not in "0123456789abcdef" for char in release_id):
            raise ProductionClosureStorageError("制片发布 ID 必须是六十四位小写 SHA-256。")
        directory = self.releases_dir / "immutable" / release_id
        manifest = self._read_required(
            directory / "manifest.json",
            ProductionReleaseManifest,
            "不可变制片发布清单",
        )
        if manifest.release_id != release_id:
            raise ProductionClosureStorageError("制片发布目录与清单 ID 不一致。")
        return self._load_release(directory, manifest)

    def _load_release(
        self,
        directory: Path,
        manifest: ProductionReleaseManifest,
    ) -> ProductionImmutableRelease:
        """加载并交叉校验已通过目录与清单 ID 检查的发布归档。"""
        receipt_path = directory / "correction_receipt.json"
        try:
            return ProductionImmutableRelease(
                manifest=manifest,
                report=self._read_required(
                    directory / "report.json", ProductionReleaseReport, "制片发布报告"
                ),
                official_plan=self._read_required(
                    directory / "official_plan.json", ProductionPlan, "发布正式规划"
                ),
                validation=self._read_required(
                    directory / "validation.json",
                    ProductionPlanningValidationReport,
                    "发布规划校验报告",
                ),
                review_report=self._read_required(
                    directory / "review_report.json",
                    ProductionReviewReport,
                    "发布专家评审报告",
                ),
                correction_receipt=(
                    self._read_required(
                        receipt_path, ProductionCorrectionReceipt, "发布制片修正回执"
                    )
                    if receipt_path.is_file()
                    else None
                ),
            )
        except ValidationError as error:
            raise ProductionClosureStorageError(f"不可变制片发布指纹无效：{error}") from error

    def _write_release(self, directory: Path, archive: ProductionImmutableRelease) -> None:
        """写完不可变发布内容后最后写入归档清单。"""
        items: list[tuple[str, BaseModel]] = [
            ("report.json", archive.report),
            ("official_plan.json", archive.official_plan),
            ("validation.json", archive.validation),
            ("review_report.json", archive.review_report),
        ]
        if archive.correction_receipt is not None:
            items.append(("correction_receipt.json", archive.correction_receipt))
        items.append(("manifest.json", archive.manifest))
        for name, model in items:
            self._write_immutable(directory / name, model)


def _make_release_manifest(
    report: ProductionReleaseReport,
    plan: ProductionPlan,
    validation: ProductionPlanningValidationReport,
    review: ProductionReviewReport,
    receipt: ProductionCorrectionReceipt | None,
) -> ProductionReleaseManifest:
    """根据完整发布快照构造可自校验的内容清单。"""
    values = {
        "schema_version": "1.0",
        "profile": report.profile,
        "report_fingerprint": content_fingerprint(report),
        "official_plan_fingerprint": content_fingerprint(plan),
        "validation_fingerprint": content_fingerprint(validation),
        "review_report_fingerprint": content_fingerprint(review),
        "correction_receipt_fingerprint": content_fingerprint(receipt) if receipt else None,
    }
    return ProductionReleaseManifest(release_id=content_fingerprint(values), **values)
