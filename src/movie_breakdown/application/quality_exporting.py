"""叙事语义质量报告和可填写人工评测模板的持久化。"""

from __future__ import annotations

from movie_breakdown.application.quality import NarrativeQualityService
from movie_breakdown.domain.quality import (
    ReviewResponse,
    ReviewTarget,
    ReviewVerdict,
    SemanticQualityReport,
)
from movie_breakdown.infrastructure.storage import ProjectStore


class SemanticQualityExporter:
    """把严格语义质量报告导出为 JSON、Markdown 和人工答案模板。"""

    def export(
        self,
        store: ProjectStore,
        report: SemanticQualityReport,
    ) -> dict[str, str]:
        """原子保存报告并保护已经存在的人工答案模板。

        Args:
            store: 当前分析项目的持久化仓库。
            report: 待保存的自动信号与人工抽检报告。

        Returns:
            产物类型到绝对文件路径的映射。
        """
        artifact_path = store.artifact_path("semantic_quality")
        store.write_model(artifact_path, report)
        markdown_path = store.write_export("semantic-quality.md", self.render_markdown(report))
        template_name = f"human-review-{report.analysis_fingerprint[:12]}.json"
        template_path = store.exports_dir / template_name
        if not template_path.exists():
            answers = NarrativeQualityService().answers_template(report)
            store.write_model(template_path, answers)
        return {
            "artifact": str(artifact_path),
            "markdown": str(markdown_path),
            "answers_template": str(template_path),
        }

    def render_markdown(self, report: SemanticQualityReport) -> str:
        """渲染明确区分代理信号与人工判断的中文报告。

        Args:
            report: 已生成的语义质量报告。

        Returns:
            以换行结尾的 Markdown 文本。
        """
        summary = report.human_summary
        lines = [
            "# 叙事语义质量评测",
            "",
            "> 结构校验通过不等于叙事判断正确；以下自动信号只是风险代理，不是正确率。",
            "",
            f"- 分析指纹：`{report.analysis_fingerprint}`",
            f"- 评分标准版本：`{report.rubric_version}`",
            f"- 人工覆盖：{summary.reviewed_count}/{summary.target_count} ({summary.coverage:.1%})",
        ]
        reviewer = report.human_review.reviewer.strip()
        if reviewer:
            lines.append(f"- 评审人：{reviewer}")
        lines.extend(
            [
                "",
                "## 自动风险信号",
                "",
                "| 信号 | 状态 | 结果 | 说明 |",
                "| --- | --- | ---: | --- |",
            ]
        )
        for signal in report.automatic_signals:
            result = (
                "不适用" if signal.value is None else f"{signal.numerator}/{signal.denominator}"
            )
            lines.append(f"| {signal.name} | {signal.status.value} | {result} | {signal.message} |")
            if signal.references:
                references = "、".join(signal.references[:20])
                suffix = "……" if len(signal.references) > 20 else ""
                lines.append(f"<!-- {signal.code} 待关注：{references}{suffix} -->")
        lines.extend(
            [
                "",
                "## 人工评测方法",
                "",
                "每个目标按适用维度给 1–5 分，并选择 supported、partially_supported、",
                "unsupported 或 uncertain。未填写保持 unreviewed，不参与均分。",
                "",
                "- source_fidelity：是否忠于剧本原文",
                "- evidence_sufficiency：证据是否足以支撑结论",
                "- causal_coherence：因果解释是否成立",
                "- structural_plausibility：幕、节拍或转折点解释是否合理",
                "- character_arc_coherence：人物欲望、动机与变化是否连贯",
                "- character_portrait_coherence：人物小传的性格、动机与整体形象是否连贯",
                "- theme_plausibility：主题或母题解读是否有文本依据",
                "- uncertainty_calibration：是否区分剧本呈现、角色转述、分析推断与未知",
                "",
                "## 抽检目标",
                "",
            ]
        )
        responses = {item.target_id: item for item in report.human_review.responses}
        for index, target in enumerate(report.human_review.targets, start=1):
            response = responses[target.id]
            lines.extend(self._render_target(index, target, response))
        lines.extend(["## 局限", ""])
        lines.extend(f"- {item}" for item in report.limitations)
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_target(
        index: int,
        target: ReviewTarget,
        response: ReviewResponse,
    ) -> list[str]:
        """渲染单个目标的结论、证据与已填写的人工答案。

        Args:
            index: 目标在报告中的一基序号。
            target: 待评审的叙事结论及其证据上下文。
            response: 与目标对应的当前人工答案。

        Returns:
            该目标对应的 Markdown 行。
        """
        scene_ids = "、".join(target.scene_ids) or "未提供"
        risks = "；".join(target.risk_reasons) or "未发现自动风险"
        lines = [
            f"### {index}. {target.title}",
            "",
            f"- ID：`{target.id}`",
            f"- 抽样原因：{target.selection_reason}；风险分：{target.risk_score}",
            f"- 风险说明：{risks}",
            f"- 涉及场景：{scene_ids}",
            f"- 待判断结论：{target.claim}",
            f"- 当前结论：{response.verdict.value}",
            "",
        ]
        if target.evidence:
            lines.append("证据：")
            lines.append("")
            for evidence in target.evidence[:8]:
                excerpt = evidence.excerpt.replace("\n", " ")
                span = evidence.source_span
                lines.append(f"- `{evidence.scene_id}:{span.line_start}-{span.line_end}` {excerpt}")
            lines.append("")
        for context in target.contexts[:3]:
            snippet = context.text[:1000].rstrip()
            suffix = "\n……" if len(context.text) > 1000 else ""
            lines.extend(
                [
                    f"<details><summary>{context.scene_id} · {context.heading}</summary>",
                    "",
                    "```text",
                    f"{snippet}{suffix}",
                    "```",
                    "",
                    "</details>",
                    "",
                ]
            )
        if response.verdict != ReviewVerdict.UNREVIEWED:
            ratings = [
                item for item in response.ratings if item.score is not None or item.comment.strip()
            ]
            if ratings:
                lines.extend(["人工评分：", ""])
                for rating in ratings:
                    score = f"：{rating.score}/5" if rating.score is not None else ""
                    lines.append(f"- `{rating.dimension.value}`{score}")
                    comment = rating.comment.strip()
                    if comment:
                        lines.append(f"  - 评论：{comment}")
                lines.append("")
            notes = response.notes.strip()
            if notes:
                lines.extend([f"备注：{notes}", ""])
            correction = (response.proposed_correction or "").strip()
            if correction:
                lines.extend([f"建议修正：{correction}", ""])
        return lines
