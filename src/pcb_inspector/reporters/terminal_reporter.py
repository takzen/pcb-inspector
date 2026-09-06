"""Terminal rich console reporter for interactive CLI output."""

from __future__ import annotations

import sys

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    reconfig = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfig):
        try:
            reconfig(encoding="utf-8", errors="replace")
        except Exception:
            pass

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from pcb_inspector.core.models import AuditResult, Severity


class TerminalReporter:
    """Prints rich formatted tables and summaries to terminal stdout."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    def print_result(self, result: AuditResult) -> None:
        s = result.summary

        # Banner
        status_color = "bold green" if s.passed else "bold red"
        status_text = "PASSED" if s.passed else "FAILED"
        panel_content = (
            f"Project: [cyan]{result.project_path}[/cyan]\n"
            f"pcb-inspector v{result.tool_version} | Status: [{status_color}]{status_text}[/{status_color}]\n"
            f"Execution time: [yellow]{s.duration_seconds:.2f}s[/yellow]"
        )
        self.console.print(Panel(panel_content, title="🔬 PCB Inspector Audit Report", expand=False))

        # Metrics Table
        summary_table = Table(title="Summary Metrics", header_style="bold magenta")
        summary_table.add_column("Severity", justify="left")
        summary_table.add_column("Count", justify="right")

        summary_table.add_row(f"{Severity.CRITICAL.badge_emoji} Critical", str(s.critical_count))
        summary_table.add_row(f"{Severity.WARNING.badge_emoji} Warning", str(s.warning_count))
        summary_table.add_row(f"{Severity.SUGGESTION.badge_emoji} Suggestion", str(s.suggestion_count))
        summary_table.add_row(f"{Severity.PASS.badge_emoji} Pass", str(s.pass_count))
        summary_table.add_row("Total Findings", f"[bold]{s.total_findings}[/bold]")
        self.console.print(summary_table)

        if not result.findings:
            self.console.print("\n[bold green]✔ All checks passed without issues![/bold green]\n")
            return

        # Findings table
        findings_table = Table(title="Findings Details", header_style="bold cyan")
        findings_table.add_column("Severity", justify="center", width=10)
        findings_table.add_column("Rule ID", justify="left", width=14)
        findings_table.add_column("Components", justify="left", width=14)
        findings_table.add_column("Description", justify="left")

        for f in result.findings:
            sev_style = "red" if f.severity == Severity.CRITICAL else ("yellow" if f.severity == Severity.WARNING else "white")
            comp_str = ", ".join(f.components) if f.components else "-"
            findings_table.add_row(
                f"[{sev_style}]{f.severity.badge_emoji} {f.severity.value}[/{sev_style}]",
                f.rule_id,
                comp_str,
                f.title,
            )

        self.console.print(findings_table)
        self.console.print("")
