"""把已验证制片拆解导出为 JSON、Markdown 和双 CSV。"""

from __future__ import annotations

import csv
import io
import json
from typing import Literal

from movie_breakdown.application.production_markdown import render_production_markdown
from movie_breakdown.domain.production_catalog import ProductionBreakdown
from movie_breakdown.infrastructure.production_storage import ProductionStore

ProductionExportFormat = Literal["markdown", "json", "csv", "all"]
_EXPORT_FILENAMES = {
    "json": "breakdown.json",
    "markdown": "report.md",
    "scenes_csv": "scenes.csv",
    "catalog_csv": "catalog.csv",
}


class InvalidProductionExportError(ValueError):
    """表示制片校验未通过或用户请求了无效格式。"""


class ProductionExportService:
    """把制片领域产物渲染到独立 `production/exports` 目录。"""

    def export(
        self,
        store: ProductionStore,
        breakdown: ProductionBreakdown,
        export_format: ProductionExportFormat = "all",
    ) -> dict[str, str]:
        """写出指定格式的制片元素拆解结果。

        Args:
            store: 独立制片存储。
            breakdown: 已聚合并通过校验的完整制片拆解。
            export_format: `markdown`、`json`、`csv` 或 `all`。

        Returns:
            格式名称到已写入绝对路径的映射。

        Raises:
            InvalidProductionExportError: 校验未通过或格式不受支持。
        """
        contents = self.render_contents(breakdown, export_format)
        return {
            kind: str(store.write_export(_EXPORT_FILENAMES[kind], content))
            for kind, content in contents.items()
        }

    def render_contents(
        self,
        breakdown: ProductionBreakdown,
        export_format: ProductionExportFormat = "all",
    ) -> dict[str, str]:
        """渲染指定格式且尚未写入磁盘的完整制片导出内容。

        Args:
            breakdown: 已聚合并通过校验的完整制片拆解。
            export_format: `markdown`、`json`、`csv` 或 `all`。

        Returns:
            格式名称到确定性 UTF-8 文本内容的映射。

        Raises:
            InvalidProductionExportError: 校验未通过或格式不受支持。
        """
        if not breakdown.validation.valid:
            raise InvalidProductionExportError("制片一致性校验未通过，不能导出正式报告。")
        if export_format not in {"markdown", "json", "csv", "all"}:
            raise InvalidProductionExportError(f"不支持的制片导出格式：{export_format}")
        contents: dict[str, str] = {}
        if export_format in {"json", "all"}:
            payload = breakdown.model_dump(mode="json", exclude_computed_fields=True)
            contents["json"] = (
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            )
        if export_format in {"markdown", "all"}:
            contents["markdown"] = render_production_markdown(breakdown)
        if export_format in {"csv", "all"}:
            contents["scenes_csv"] = self.render_scenes_csv(breakdown)
            contents["catalog_csv"] = self.render_catalog_csv(breakdown)
        return contents

    def render_scenes_csv(self, breakdown: ProductionBreakdown) -> str:
        """渲染逐场设置、规模和复杂度 CSV。

        Args:
            breakdown: 已验证的完整制片拆解。

        Returns:
            以换行符结尾的 UTF-8 CSV 内容。
        """
        rows = [
            [
                "场景ID",
                "标题",
                "地点",
                "子地点",
                "内外景",
                "时段",
                "演员数",
                "群演组数",
                "元素数",
                "复杂度分数",
                "复杂度等级",
                "待确认数",
            ]
        ]
        rows.extend(
            [
                scene.scene_id,
                scene.setting.raw_heading,
                scene.setting.location_name,
                scene.setting.sub_location or "",
                scene.setting.interior_exterior.value,
                scene.setting.time_of_day.value,
                len(scene.cast),
                len(scene.background),
                len(scene.elements),
                scene.complexity.score,
                scene.complexity.level.value,
                len(scene.uncertainties),
            ]
            for scene in breakdown.scenes
        )
        return _csv(rows)

    def render_catalog_csv(self, breakdown: ProductionBreakdown) -> str:
        """渲染地点、演员、群演和制片元素统一目录 CSV。

        Args:
            breakdown: 已验证的完整制片拆解。

        Returns:
            以换行符结尾的 UTF-8 CSV 内容。
        """
        rows: list[list[object]] = [
            ["目录类别", "元素类别", "ID", "名称", "场景", "数量下界", "数量上界", "单位"]
        ]
        for item in breakdown.catalog.locations:
            rows.append(["地点", "", item.id, item.name, "、".join(item.scene_ids), "", "", ""])
        for item in breakdown.catalog.cast:
            rows.append(["演员", "", item.id, item.name, "、".join(item.scene_ids), "", "", ""])
        for item in breakdown.catalog.background:
            rows.append(_quantity_row("群演", "", item))
        for item in breakdown.catalog.elements:
            rows.append(_quantity_row("制片元素", item.kind.value, item))
        return _csv(rows)


def _quantity_row(category: str, kind: str, item) -> list[object]:
    quantity = item.peak_quantity
    return [
        category,
        kind,
        item.id,
        item.name,
        "、".join(item.scene_ids),
        quantity.minimum if quantity.minimum is not None else "",
        quantity.maximum if quantity.maximum is not None else "",
        quantity.unit,
    ]


def _csv(rows: list[list[object]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerows(rows)
    return output.getvalue()
