"""把制片封版门禁渲染为稳定 JSON 与中文 Markdown。"""

from __future__ import annotations

import json

from movie_breakdown.domain.production_release import ProductionReleaseReport


def render_production_release_json(report: ProductionReleaseReport) -> str:
    """渲染完整机器可读制片封版报告。

    Args:
        report: 已执行全部检查的制片封版门禁报告。

    Returns:
        末尾带换行的 UTF-8 JSON 文本。
    """
    payload = report.model_dump(mode="json", exclude_computed_fields=True)
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def render_production_release_markdown(report: ProductionReleaseReport) -> str:
    """渲染供专家签阅的中文制片封版摘要。

    Args:
        report: 已执行全部检查的制片封版门禁报告。

    Returns:
        显示等级、结论、逐项门禁和限制的 Markdown 文本。
    """
    lines = [
        "# 制片规划封版门禁",
        "",
        f"- 请求等级：`{report.profile.value}`",
        f"- 当前状态：`{report.state.value}`",
        f"- 是否可发布：{'是' if report.releasable else '否'}",
        f"- 规划指纹：`{report.plan_fingerprint}`",
        f"- 评审目标集指纹：`{report.review_target_set_fingerprint}`",
        "",
        "## 门禁检查",
        "",
        "| 检查 | 结论 | 说明 | 引用 |",
        "|---|---|---|---|",
    ]
    for check in report.checks:
        references = "、".join(check.references) or "—"
        lines.append(
            f"| {check.name} (`{check.code.value}`) | "
            f"{'通过' if check.passed else '阻断'} | {check.message} | {references} |"
        )
    lines.extend(["", "## 限制与边界", ""])
    lines.extend(f"- {item}" for item in report.limitations)
    if not report.limitations:
        lines.append("- 无额外限制。")
    lines.append("")
    return "\n".join(lines)
