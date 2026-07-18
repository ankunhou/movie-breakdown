"""制片本地闭环各命令返回的强类型结果。"""

from __future__ import annotations

from dataclasses import dataclass

from movie_breakdown.domain.production_correction import ProductionCorrectionReceipt
from movie_breakdown.domain.production_planning import ProductionPlan
from movie_breakdown.domain.production_planning_validation import (
    ProductionPlanningValidationReport,
)
from movie_breakdown.domain.production_release import ProductionReleaseReport
from movie_breakdown.domain.production_review import (
    ProductionReviewAnswers,
    ProductionReviewReport,
)


@dataclass(frozen=True, slots=True)
class ProductionPlanClosureResult:
    """本地规划命令生成的正式结果。

    Attributes:
        plan: 从当前基础拆解派生的规划。
        validation: 规划的三级准备度校验。
        exports: 用户可见导出文件名与绝对路径。
    """

    plan: ProductionPlan
    validation: ProductionPlanningValidationReport
    exports: dict[str, str]


@dataclass(frozen=True, slots=True)
class ProductionReviewClosureResult:
    """专家目标报告和可继续填写的答案文件。

    Attributes:
        report: 当前正式规划的全量强制评审报告。
        answers: 与报告指纹一致的完整答案模板或已导入答案。
        paths: 报告和答案文件的绝对路径。
    """

    report: ProductionReviewReport
    answers: ProductionReviewAnswers
    paths: dict[str, str]


@dataclass(frozen=True, slots=True)
class ProductionCorrectionClosureResult:
    """结构化修正预演或激活后的审计结果。

    Attributes:
        plan: 应用完整修正集后的规划。
        validation: 修正规划的三级准备度校验。
        receipt: 绑定旧值、答案和新规划的应用回执。
        generation_id: 正式激活的代际 ID；预演时为空。
        exports: 正式激活后更新的用户可见导出。
    """

    plan: ProductionPlan
    validation: ProductionPlanningValidationReport
    receipt: ProductionCorrectionReceipt
    generation_id: str | None
    exports: dict[str, str]


@dataclass(frozen=True, slots=True)
class ProductionFinalizeClosureResult:
    """封版门禁及可选不可变发布归档结果。

    Attributes:
        report: 评测版或专业版完整门禁报告。
        release_id: 通过门禁后生成的不可变发布 ID；阻断时为空。
        exports: 最新 JSON 和中文 Markdown 门禁文件。
    """

    report: ProductionReleaseReport
    release_id: str | None
    exports: dict[str, str]
