"""把本地制片规划与分级校验渲染为可审计的内存导出文件。"""

from __future__ import annotations

import json

from movie_breakdown.application.production_planning_export_csv import (
    render_occurrences_csv,
    render_resources_csv,
    render_shooting_units_csv,
)
from movie_breakdown.application.production_planning_export_markdown import (
    render_planning_markdown,
)
from movie_breakdown.application.production_planning_export_semantic_csv import (
    render_issues_csv,
    render_quantities_csv,
    render_safety_csv,
)
from movie_breakdown.domain.production_planning import ProductionPlan
from movie_breakdown.domain.production_planning_validation import (
    ProductionPlanningValidationReport,
)
from movie_breakdown.infrastructure.fingerprint import content_fingerprint


class InvalidProductionPlanningExportError(ValueError):
    """表示规划与校验报告不属于同一内容版本。"""


class ProductionPlanningExportService:
    """把规划及其准备度结论渲染为固定文件名的 UTF-8 文本映射。"""

    def render_contents(
        self,
        plan: ProductionPlan,
        validation: ProductionPlanningValidationReport,
    ) -> dict[str, str]:
        """生成完整规划 JSON、中文报告和六类 CSV。

        Args:
            plan: 待导出的当前制片规划。
            validation: 针对同一规划计算的分级校验报告。

        Returns:
            固定文件名到确定性 UTF-8 文本内容的映射。

        Raises:
            InvalidProductionPlanningExportError: 校验报告不属于当前规划版本。
        """
        current_fingerprint = content_fingerprint(plan)
        if validation.plan_fingerprint != current_fingerprint:
            raise InvalidProductionPlanningExportError(
                "规划内容已变化，必须重新校验后才能导出准备度结论。"
            )
        return {
            "planning.json": _render_json(plan, validation),
            "planning-report.md": render_planning_markdown(plan, validation),
            "shooting_units.csv": render_shooting_units_csv(plan),
            "resources.csv": render_resources_csv(plan),
            "occurrences.csv": render_occurrences_csv(plan),
            "quantities.csv": render_quantities_csv(plan),
            "safety.csv": render_safety_csv(plan),
            "issues.csv": render_issues_csv(validation),
        }


def _render_json(
    plan: ProductionPlan,
    validation: ProductionPlanningValidationReport,
) -> str:
    """把规划和准备度报告写入同一个稳定 JSON 信封。"""
    payload = {
        "schema_version": "1.0",
        "plan": plan.model_dump(mode="json", exclude_computed_fields=True),
        "validation": validation.model_dump(mode="json", exclude_computed_fields=True),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
