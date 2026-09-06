"""Markdown report generator for human engineers and pull request reviews."""

from __future__ import annotations

from pcb_inspector.core.models import AuditResult, Severity
from pcb_inspector.reporters.base import BaseReporter


class MarkdownReporter(BaseReporter):
    """Produces formatted GitHub Flavored Markdown reports."""

    def render(self, result: AuditResult) -> str:
        s = result.summary
        status_text = "🟢 **PASSED**" if s.passed else "🔴 **FAILED**"

        lines: list[str] = [
            f"# 🔬 PCB Inspection Report — {status_text}",
            "",
            f"- **Project:** `{result.project_path}`",
            f"- **Generated:** `{result.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}`",
            f"- **Tool Version:** `v{result.tool_version}`",
            f"- **Execution Time:** `{s.duration_seconds:.2f}s`",
            "",
            "## 📊 Summary",
            "",
            "| Metric | Count |",
            "| :--- | :--- |",
            f"| 🔴 Critical | {s.critical_count} |",
            f"| 🟠 Warning | {s.warning_count} |",
            f"| 🟡 Suggestion | {s.suggestion_count} |",
            f"| 🟢 Pass Checks | {s.pass_count} |",
            f"| **Total Findings** | **{s.total_findings}** |",
            "",
        ]

        if not result.findings:
            lines.extend(
                [
                    "> [!NOTE]",
                    "> No rule violations or anomalies were detected. The board passed all checks.",
                    "",
                ]
            )
            return "\n".join(lines)

        lines.extend(["## 📋 Findings Details", ""])

        # Group findings by severity
        for target_sev in [Severity.CRITICAL, Severity.WARNING, Severity.SUGGESTION, Severity.PASS]:
            group = [f for f in result.findings if f.severity == target_sev]
            if not group:
                continue

            lines.append(f"### {target_sev.badge_emoji} {target_sev.value} ({len(group)})")
            lines.append("")

            for idx, item in enumerate(group, start=1):
                components_str = ", ".join(f"`{c}`" for c in item.components) or "N/A"
                nets_str = ", ".join(f"`{n}`" for n in item.nets) or "N/A"
                coords_str = ", ".join(str(c) for c in item.coordinates) or "N/A"

                lines.extend(
                    [
                        f"#### {idx}. [{item.rule_id}] {item.title}",
                        f"- **ID:** `{item.id}`",
                        f"- **Category:** `{item.category.value}`",
                        f"- **Components:** {components_str}",
                        f"- **Nets:** {nets_str}",
                        f"- **Coordinates:** {coords_str}",
                        "",
                        "**Description:**  ",
                        f"{item.description}",
                        "",
                    ]
                )

                if item.rationale:
                    lines.extend(
                        [
                            "> [!NOTE]",
                            f"> **Engineering Rationale:** {item.rationale}",
                            "",
                        ]
                    )

                if item.recommendation:
                    lines.extend(
                        [
                            "> [!TIP]",
                            f"> **Actionable Recommendation:** {item.recommendation}",
                            "",
                        ]
                    )

                if item.visual_snapshot:
                    lines.extend([f"![Snapshot]({item.visual_snapshot})", ""])

                lines.append("---")
                lines.append("")

        return "\n".join(lines)
