"""从已验证产物生成稳定 JSON 和中文 Markdown 报告。"""

from __future__ import annotations

import json
from typing import Literal

from movie_breakdown.application.biography_exporting import render_biography_markdown
from movie_breakdown.application.dossier_exporting import render_dossier_markdown
from movie_breakdown.domain.export import NarrativeBreakdown
from movie_breakdown.infrastructure.fingerprint import content_fingerprint
from movie_breakdown.infrastructure.storage import ProjectStore

ExportFormat = Literal["markdown", "json", "all"]
_EXPORT_FILENAMES = {"json": "breakdown.json", "markdown": "report.md"}


class InvalidExportError(ValueError):
    """校验未通过或导出格式无效。"""


class ExportService:
    """将领域产物渲染为面向用户的文件。"""

    def export(
        self,
        store: ProjectStore,
        breakdown: NarrativeBreakdown,
        export_format: ExportFormat = "all",
    ) -> dict[str, str]:
        """写出指定格式的叙事结构拆解结果。

        Args:
            store: 目标项目存储。
            breakdown: 已聚合的完整叙事拆解产物。
            export_format: `markdown`、`json` 或同时输出的 `all`。

        Returns:
            格式名称到已写入绝对路径的映射。

        Raises:
            InvalidExportError: 校验未通过或格式不受支持。
        """
        contents = self.render_contents(breakdown, export_format)
        return {
            kind: str(store.write_export(_EXPORT_FILENAMES[kind], content))
            for kind, content in contents.items()
        }

    def render_contents(
        self,
        breakdown: NarrativeBreakdown,
        export_format: ExportFormat = "all",
    ) -> dict[str, str]:
        """渲染指定格式且尚未写入磁盘的完整导出内容。

        Args:
            breakdown: 已聚合的完整叙事拆解产物。
            export_format: `markdown`、`json` 或同时输出的 `all`。

        Returns:
            格式名称到确定性 UTF-8 文本内容的映射。

        Raises:
            InvalidExportError: 校验未通过或格式不受支持。
        """
        if not breakdown.validation.valid:
            raise InvalidExportError("本地一致性校验未通过，不能导出正式报告。")
        if export_format not in {"markdown", "json", "all"}:
            raise InvalidExportError(f"不支持的导出格式：{export_format}")
        contents: dict[str, str] = {}
        if export_format in {"json", "all"}:
            payload = breakdown.model_dump(mode="json", exclude_computed_fields=True)
            contents["json"] = (
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            )
        if export_format in {"markdown", "all"}:
            contents["markdown"] = self.render_markdown(breakdown)
        return contents

    def render_markdown(self, breakdown: NarrativeBreakdown) -> str:
        """把叙事拆解聚合渲染为中文 Markdown。

        Args:
            breakdown: 已验证的完整叙事拆解产物。

        Returns:
            以换行符结尾的 Markdown 报告。
        """
        structure = breakdown.structure
        lines = [
            f"# {breakdown.screenplay.title}：叙事结构拆解",
            "",
            "## 故事概览",
            "",
            f"**一句话梗概：** {structure.logline}",
            "",
            structure.synopsis,
            "",
            "## 三幕结构",
            "",
        ]
        for act in structure.acts:
            scene_refs = "、".join(act.scene_ids)
            lines.extend(
                [
                    f"### 第 {act.act} 幕：{act.title}",
                    "",
                    act.summary,
                    "",
                    f"- 场景：{scene_refs}",
                    f"- 转折：{act.turning_point or '剧本未明确'}",
                    "",
                ]
            )
        lines.extend(render_dossier_markdown(breakdown))
        lines.extend(render_biography_markdown(breakdown))
        receipt = breakdown.correction_receipt
        if receipt is not None:
            lines.extend(
                [
                    "## 人工修正回执",
                    "",
                    f"- 评审人：{receipt.reviewer}",
                    f"- 已应用修正：{receipt.applied_count} 条",
                    f"- 修正 ID：{'、'.join(receipt.applied_correction_ids)}",
                    f"- 基础分析指纹：`{receipt.base_analysis_fingerprint}`",
                    f"- 修正集指纹：`{receipt.correction_set_fingerprint}`",
                    f"- 修正内容指纹：`{receipt.corrected_analysis_fingerprint}`",
                    "",
                ]
            )
        lines.extend(["## 情节线与节拍", ""])
        for thread in structure.plot_threads:
            lines.append(f"- **{thread.name}**（{thread.kind}，{thread.status}）：{thread.summary}")
        lines.extend(["", "### 关键节拍", ""])
        for beat in structure.beats:
            scene_ids = "、".join(beat.scene_ids)
            lines.append(f"- 第 {beat.act} 幕 · **{beat.name}**（{scene_ids}）：{beat.summary}")
        lines.extend(["", "## 主题与母题", ""])
        lines.append(f"- 主题：{'；'.join(structure.themes) or '未识别'}")
        lines.append(f"- 母题：{'；'.join(structure.motifs) or '未识别'}")
        lines.extend(["", "## 场景索引", "", "| 场景 | 标题 | 摘要 |", "|---|---|---|"])
        analyses = {item.scene_id: item for item in breakdown.scene_analyses}
        for scene in breakdown.screenplay.scenes:
            summary = analyses.get(scene.id)
            lines.append(
                f"| {scene.id} | {_escape_table(scene.heading)} | "
                f"{_escape_table(summary.summary if summary else '未分析')} |"
            )
        lines.extend(
            [
                "",
                "---",
                "",
                f"产物指纹：`{content_fingerprint(breakdown)}`",
                "",
            ]
        )
        return "\n".join(lines)


def _escape_table(value: str) -> str:
    """转义 Markdown 表格中的换行和竖线。"""
    return value.replace("|", "\\|").replace("\n", " ")
