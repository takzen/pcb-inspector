"""Main CLI entrypoint for pcb-inspector."""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    reconfig_out = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfig_out):
        try:
            reconfig_out(encoding="utf-8", errors="replace")
        except Exception:
            pass
    reconfig_err = getattr(sys.stderr, "reconfigure", None)
    if callable(reconfig_err):
        try:
            reconfig_err(encoding="utf-8", errors="replace")
        except Exception:
            pass

import typer
from rich.console import Console
from rich.table import Table

from pcb_inspector import __version__
from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.core.models import AuditResult, Severity
from pcb_inspector.kicad.cli_wrapper import KiCadCli
from pcb_inspector.reporters.json_reporter import JsonReporter
from pcb_inspector.reporters.markdown_reporter import MarkdownReporter
from pcb_inspector.reporters.terminal_reporter import TerminalReporter
from pcb_inspector.rules.registry import default_registry

app = typer.Typer(
    name="pcb-inspector",
    help="pcb-inspector: Automated Multimodal Design Reviewer & Linter for KiCad Projects",
    add_completion=False,
    rich_markup_mode="rich",
)
console = Console(highlight=False)


@app.command()
def version() -> None:
    """Show version and detected KiCad CLI environment."""
    console.print(f"[bold cyan]pcb-inspector[/bold cyan] version: [green]v{__version__}[/green]")
    kicad_path = KiCadCli.find_executable()
    if kicad_path:
        console.print(f"KiCad CLI: [green]{kicad_path}[/green]")
        try:
            cli = KiCadCli(kicad_path)
            ver = cli.get_version()
            console.print(f"KiCad Version: [cyan]{ver}[/cyan]")
        except Exception as err:
            console.print(f"[yellow]Could not query KiCad version: {err}[/yellow]")
    else:
        console.print("[yellow]KiCad CLI: Not detected in PATH or default paths.[/yellow]")


@app.command()
def rules() -> None:
    """List all registered inspection rules."""
    all_rules = default_registry.list_rules()
    if not all_rules:
        console.print("[yellow]No custom heuristic rules currently registered.[/yellow]")
        return

    table = Table(title="Registered Inspection Rules", header_style="bold magenta")
    table.add_column("Rule ID", justify="left", style="cyan")
    table.add_column("Name", justify="left")
    table.add_column("Category", justify="left", style="green")
    table.add_column("Default Severity", justify="center")

    for r in all_rules:
        table.add_row(r.rule_id, r.name, r.category.value, f"{r.default_severity.badge_emoji} {r.default_severity.value}")

    console.print(table)


@app.command()
def check(
    project_path: Path = typer.Argument(
        ...,
        help="Path to KiCad project file (.kicad_pro), schematic (.kicad_sch), or PCB (.kicad_pcb)",
        exists=True,
        readable=True,
    ),
    output: Path | None = typer.Option(
        None, "--output", "-o", help="File path to save the generated report"
    ),
    report_format: str = typer.Option(
        "markdown", "--format", "-f", help="Report format: markdown, json, or both"
    ),
    fail_on: str = typer.Option(
        "CRITICAL",
        "--fail-on",
        help="Severity threshold causing non-zero exit code: CRITICAL, WARNING, SUGGESTION",
    ),
    config_file: Path | None = typer.Option(
        None, "--config", "-c", help="Path to custom configuration YAML file"
    ),
) -> None:
    """Run verification pipeline on a KiCad project."""
    start_time = time.perf_counter()

    # Parse fail_on threshold
    try:
        threshold = Severity(fail_on.upper())
    except ValueError:
        console.print(f"[bold red]Invalid --fail-on value '{fail_on}'. Choose CRITICAL, WARNING, or SUGGESTION.[/bold red]")
        raise typer.Exit(code=1) from None

    # Load configuration
    cfg = InspectorConfig.load(config_file)
    cfg.fail_on = threshold

    console.print(f"[bold]Starting inspection of:[/bold] [cyan]{project_path}[/cyan]")

    # Run evaluations via registry
    findings = default_registry.evaluate_all(context=project_path, config=cfg)

    duration = time.perf_counter() - start_time
    result = AuditResult.create(
        project_path=str(project_path),
        findings=findings,
        tool_version=__version__,
        duration_seconds=duration,
        fail_on=threshold,
    )

    # Print terminal output
    terminal_reporter = TerminalReporter(console=console)
    terminal_reporter.print_result(result)

    # Save reports if requested
    if output:
        fmt = report_format.lower()
        if fmt in ("json", "both"):
            json_path = output if fmt == "json" else output.with_suffix(".json")
            JsonReporter().write_to_file(result, json_path)
            console.print(f"Saved JSON report to: [green]{json_path}[/green]")

        if fmt in ("markdown", "md", "both"):
            md_path = output if fmt in ("markdown", "md") else output.with_suffix(".md")
            MarkdownReporter().write_to_file(result, md_path)
            console.print(f"Saved Markdown report to: [green]{md_path}[/green]")

    # Exit code based on pass/fail
    if not result.summary.passed:
        raise typer.Exit(code=1)


def main() -> None:
    """Entrypoint function."""
    app()


if __name__ == "__main__":
    main()
