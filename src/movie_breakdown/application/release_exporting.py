"""叙事稳定版发布门禁报告的持久化与 Markdown 展示。"""

from __future__ import annotations

from movie_breakdown.domain.release import ReleaseGateReport
from movie_breakdown.infrastructure.storage import ProjectStore


class ReleaseGateExporter:
    """将发布门禁决策保存为机器可读产物和中文报告。

    导出器只负责展示和持久化既有决策，不重新执行门禁评估，也不维护可变状态。
    """

    def export(
        self,
        store: ProjectStore,
        report: ReleaseGateReport,
    ) -> dict[str, str]:
        """原子写入发布门禁 JSON 产物和 Markdown 报告。

        Args:
            store: 当前剧本分析项目的持久化仓库。
            report: 已完成确定性评估的发布门禁报告。

        Returns:
            产物类型到已写入文件绝对路径的映射。
        """
        artifact_path = store.artifact_path("release_gate")
        store.write_model(artifact_path, report)
        markdown_path = store.write_export("release-gate.md", self.render_markdown(report))
        return {
            "artifact": str(artifact_path),
            "markdown": str(markdown_path),
        }

    def render_markdown(self, report: ReleaseGateReport) -> str:
        """把发布决策和全部检查渲染为中文 Markdown。

        Args:
            report: 待展示的发布门禁报告。

        Returns:
            以换行符结尾的 Markdown 文本。
        """
        decision = "稳定，可封版" if report.stable else "阻断，不可封版"
        passed_count = sum(check.passed for check in report.checks)
        lines = [
            "# 叙事稳定版发布门禁",
            "",
            f"> 总体结论：**{decision}**",
            "",
            f"- 分析指纹：`{report.analysis_fingerprint}`",
            f"- 检查结果：{passed_count}/{len(report.checks)} 项通过",
            "",
            "## 门禁检查",
            "",
            "| 序号 | 检查 | 代码 | 状态 | 消息 | 引用 |",
            "| ---: | --- | --- | --- | --- | --- |",
        ]
        for index, check in enumerate(report.checks, start=1):
            status = "通过" if check.passed else "阻断"
            references = "、".join(check.references) or "—"
            lines.append(
                f"| {index} | {_escape_table(check.name)} | `{check.code.value}` | {status} | "
                f"{_escape_table(check.message)} | {_escape_table(references)} |"
            )
        lines.append("")
        return "\n".join(lines)


def _escape_table(value: str) -> str:
    """转义 Markdown 表格单元格中的竖线与换行。"""
    return value.replace("|", "\\|").replace("\r", " ").replace("\n", " ")
