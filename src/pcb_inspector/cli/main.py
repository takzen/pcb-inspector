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

import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

import typer
from dotenv import find_dotenv, load_dotenv
from rich.console import Console
from rich.logging import RichHandler
from rich.markup import escape
from rich.table import Table

from pcb_inspector import __version__
from pcb_inspector.cli.watcher import watch_and_run
from pcb_inspector.core.aggregator import FindingAggregator
from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.core.exceptions import ConfigError
from pcb_inspector.core.layers import evaluate_layer_status
from pcb_inspector.core.models import AuditResult, Finding, FindingCategory, Severity
from pcb_inspector.kicad.cli_wrapper import KiCadCli
from pcb_inspector.reporters.html_reporter import HtmlReporter
from pcb_inspector.reporters.json_reporter import JsonReporter
from pcb_inspector.reporters.markdown_reporter import MarkdownReporter
from pcb_inspector.reporters.terminal_reporter import TerminalReporter
from pcb_inspector.rules.board_loader import find_design_file
from pcb_inspector.rules.registry import default_registry

app = typer.Typer(
    name="pcb-inspector",
    help="pcb-inspector: Automated Multimodal Design Reviewer & Linter for KiCad Projects",
    add_completion=False,
    rich_markup_mode="rich",
)
console = Console(highlight=False)

HEURISTIC_CATEGORIES = {
    FindingCategory.DECOUPLING,
    FindingCategory.POWER_DELIVERY,
    FindingCategory.SIGNAL_INTEGRITY,
    FindingCategory.THERMAL,
    FindingCategory.PLACEMENT,
    FindingCategory.SILKSCREEN,
    FindingCategory.MECHANICAL,
}


@app.callback()
def _configure(
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show diagnostic logs from rules and the KiCad CLI"
    ),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress non-error logs"),
) -> None:
    """Configure logging before any subcommand runs.

    Without this, every logger.warning/error in the rule engine is discarded and
    failures such as a missing kicad-cli stay invisible.
    """
    if quiet:
        level = logging.ERROR
    elif verbose:
        level = logging.DEBUG
    else:
        level = logging.WARNING

    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=verbose)],
        force=True,
    )


def _build_result(
    project_path: Path,
    findings: list[Finding],
    cfg: InspectorConfig,
    categories: set[FindingCategory] | None,
    duration: float,
    threshold: Severity,
) -> AuditResult:
    """Assemble the audit result, recording which verification layers actually ran."""
    return AuditResult.create(
        project_path=str(project_path),
        findings=findings,
        tool_version=__version__,
        duration_seconds=duration,
        fail_on=threshold,
        metadata={"layers": evaluate_layer_status(findings, cfg, categories)},
    )


# Option definitions shared by every audit command. Declaring them once keeps
# the commands from drifting apart, which is how `vision` ended up exiting
# non-zero without offering the --fail-on flag that decides when it should.
_PROJECT_ARG = typer.Argument(
    ...,
    help="Path to KiCad project file (.kicad_pro), schematic (.kicad_sch), or PCB (.kicad_pcb)",
    exists=True,
    readable=True,
)
_OUTPUT_OPT = typer.Option(
    None, "--output", "-o", help="File path to save the generated report"
)
_FORMAT_OPT = typer.Option(
    None,
    "--format",
    "-f",
    help="Report format. Without --output, reports go to output_dir from the config.",
    case_sensitive=False,
)
_FAIL_ON_OPT = typer.Option(
    "CRITICAL",
    "--fail-on",
    help="Severity threshold causing non-zero exit code: CRITICAL, WARNING, SUGGESTION",
)
_CONFIG_OPT = typer.Option(
    None,
    "--config",
    "-c",
    help="Path to custom configuration YAML file",
    exists=True,
    dir_okay=False,
    readable=True,
)
_REQUIRE_CLI_OPT = typer.Option(
    False,
    "--require-kicad-cli",
    help="Fail the run if kicad-cli is missing, instead of silently skipping Layer 1",
)
_WATCH_OPT = typer.Option(
    False, "--watch", "-w", help="Continuously monitor files and re-evaluate on change"
)


def _no_overrides(cfg: InspectorConfig) -> None:
    """Default config hook: leave the loaded configuration as it is."""


@dataclass(frozen=True)
class AuditRequest:
    """Everything one audit command varies, so the run itself can be shared."""

    project_path: Path
    #: Finding categories to evaluate; None runs every registered rule.
    categories: set[FindingCategory] | None
    banner: str
    output: Path | None
    report_format: ReportFormat | None
    threshold: Severity
    config_file: Path | None
    watch: bool
    #: Applied after the config file is loaded, so flags win over the file.
    overrides: Callable[[InspectorConfig], None] = _no_overrides


def _run_audit(request: AuditRequest) -> None:
    """Load config, evaluate, report, and set the exit code.

    Raises:
        typer.Exit: With code 1 when the run does not meet its threshold, and
            code 2 when the target holds no KiCad design or the config is invalid.
    """
    if find_design_file(request.project_path) is None:
        # With nothing to audit every rule returns no findings, which used to
        # print PASSED with a 100/100 health score.
        console.print(
            "[bold red]No KiCad board or schematic found at:[/bold red] "
            f"{escape(str(request.project_path))}"
        )
        raise typer.Exit(code=2)

    def run_once() -> bool:
        start_time = time.perf_counter()
        target = request.project_path
        project_dir = target if target.is_dir() else target.parent

        try:
            cfg = InspectorConfig.load(request.config_file, project_dir=project_dir)
        except ConfigError as err:
            # Exit 2, a usage error, distinct from exit 1 for a failed audit: a
            # broken config means the board was never checked at all.
            console.print(f"[bold red]Configuration error:[/bold red] {escape(str(err))}")
            raise typer.Exit(code=2) from None
        cfg.fail_on = request.threshold
        request.overrides(cfg)

        console.print(f"[bold]{request.banner}:[/bold] [cyan]{escape(str(target))}[/cyan]")
        raw_findings = default_registry.evaluate_filtered(
            context=target, config=cfg, categories=request.categories
        )
        findings = FindingAggregator(config=cfg).aggregate(raw_findings)

        result = _build_result(
            project_path=target,
            findings=findings,
            cfg=cfg,
            categories=request.categories,
            duration=time.perf_counter() - start_time,
            threshold=request.threshold,
        )
        output, fmt = _resolve_output(
            request.output, request.report_format, cfg.output_dir, target
        )
        return _save_and_display_result(result, output, fmt)

    if request.watch:
        console.print(
            f"[bold cyan]Entering watch mode for {escape(str(request.project_path))}... "
            f"(Ctrl+C to exit)[/bold cyan]"
        )
        watch_and_run(request.project_path, run_once)
        return

    if not run_once():
        raise typer.Exit(code=1)


class ReportFormat(str, Enum):
    """Report file formats accepted by --format.

    An Enum makes Typer reject anything else with a usage error. As a plain
    string, `-f pdf` was accepted, matched no branch, and wrote nothing.
    """

    markdown = "markdown"
    md = "md"
    json = "json"
    html = "html"
    both = "both"  # markdown + json
    all = "all"  # markdown + json + html


#: (format, reporter factory, file suffix) in the order files are written.
_WRITERS: tuple[tuple[str, Callable[[], Any], str], ...] = (
    ("json", JsonReporter, ".json"),
    ("markdown", MarkdownReporter, ".md"),
    ("html", HtmlReporter, ".html"),
)

_FORMATS_WRITTEN: dict[ReportFormat, frozenset[str]] = {
    ReportFormat.markdown: frozenset({"markdown"}),
    ReportFormat.md: frozenset({"markdown"}),
    ReportFormat.json: frozenset({"json"}),
    ReportFormat.html: frozenset({"html"}),
    ReportFormat.both: frozenset({"markdown", "json"}),
    ReportFormat.all: frozenset({"markdown", "json", "html"}),
}


def _resolve_output(
    output: Path | None,
    report_format: ReportFormat | None,
    output_dir: str,
    project_path: Path,
) -> tuple[Path | None, ReportFormat]:
    """Decide where reports go and in which format.

    `-f` without `-o` used to write nothing and say nothing. It now writes to
    the configured output_dir, which is also the first thing that setting has
    ever done: it was declared and documented but read by nothing.
    """
    fmt = report_format or ReportFormat.markdown
    if output is not None:
        return output, fmt
    if report_format is None:
        return None, fmt
    return Path(output_dir) / f"{project_path.stem}_report", fmt


def _save_and_display_result(
    result: AuditResult,
    output: Path | None,
    report_format: ReportFormat | str,
) -> bool:
    """Render terminal summary and write output files if requested."""
    terminal_reporter = TerminalReporter(console=console)
    terminal_reporter.print_result(result)

    if output:
        fmt = ReportFormat(report_format)
        wanted = _FORMATS_WRITTEN[fmt]
        single = len(wanted) == 1
        for name, reporter, suffix in _WRITERS:
            if name not in wanted:
                continue
            # A single format writes exactly the path given; several share its stem.
            path = output if single else output.with_suffix(suffix)
            path.parent.mkdir(parents=True, exist_ok=True)
            reporter().write_to_file(result, path)
            console.print(f"Saved {name.upper() if name != 'markdown' else 'Markdown'} report to: [green]{escape(str(path))}[/green]")

    return result.summary.passed


def _parse_severity_threshold(fail_on: str) -> Severity:
    """Parse string threshold into Severity enum, exiting on error."""
    try:
        return Severity(fail_on.upper())
    except ValueError:
        console.print(
            f"[bold red]Invalid --fail-on value '{escape(fail_on)}'. Choose CRITICAL, WARNING, or SUGGESTION.[/bold red]"
        )
        raise typer.Exit(code=1) from None


@app.command()
def version() -> None:
    """Show version and detected KiCad CLI environment."""
    console.print(f"[bold cyan]pcb-inspector[/bold cyan] version: [green]v{__version__}[/green]")
    kicad_path = KiCadCli.find_executable()
    if kicad_path:
        console.print(f"KiCad CLI: [green]{escape(str(kicad_path))}[/green]")
        try:
            cli = KiCadCli(kicad_path)
            ver = cli.get_version()
            console.print(f"KiCad Version: [cyan]{escape(str(ver))}[/cyan]")
        except Exception as err:
            console.print(f"[yellow]Could not query KiCad version: {escape(str(err))}[/yellow]")
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
        table.add_row(
            r.rule_id,
            r.name,
            r.category.value,
            f"{r.default_severity.badge_emoji} {r.default_severity.value}",
        )

    console.print(table)


@app.command()
def check(
    project_path: Path = _PROJECT_ARG,
    output: Path | None = _OUTPUT_OPT,
    report_format: ReportFormat | None = _FORMAT_OPT,
    fail_on: str = _FAIL_ON_OPT,
    config_file: Path | None = _CONFIG_OPT,
    enable_vision: bool | None = typer.Option(
        None,
        "--vision/--no-vision",
        help="Enable or disable Layer 3 multimodal vision AI inspection",
    ),
    vision_model: str | None = typer.Option(
        None,
        "--vision-model",
        help="Vision LLM model identifier (gemini-3.8-flash, fable-5, gpt-6-astra, mock)",
    ),
    require_kicad_cli: bool = _REQUIRE_CLI_OPT,
    watch: bool = _WATCH_OPT,
) -> None:
    """Run comprehensive 3-layer verification pipeline on a KiCad project."""

    def overrides(cfg: InspectorConfig) -> None:
        if require_kicad_cli:
            cfg.require_kicad_cli = True
        if enable_vision is not None:
            cfg.enable_vision = enable_vision
        if vision_model is not None:
            cfg.vision_model = vision_model

    _run_audit(
        AuditRequest(
            project_path=project_path,
            categories=None,
            banner="Starting comprehensive inspection",
            output=output,
            report_format=report_format,
            threshold=_parse_severity_threshold(fail_on),
            config_file=config_file,
            watch=watch,
            overrides=overrides,
        )
    )


@app.command()
def drc(
    project_path: Path = _PROJECT_ARG,
    output: Path | None = _OUTPUT_OPT,
    report_format: ReportFormat | None = _FORMAT_OPT,
    fail_on: str = _FAIL_ON_OPT,
    config_file: Path | None = _CONFIG_OPT,
    require_kicad_cli: bool = _REQUIRE_CLI_OPT,
    watch: bool = _WATCH_OPT,
) -> None:
    """Run Layer 1 deterministic DRC/ERC verification using native kicad-cli."""

    def overrides(cfg: InspectorConfig) -> None:
        if require_kicad_cli:
            cfg.require_kicad_cli = True

    _run_audit(
        AuditRequest(
            project_path=project_path,
            categories={FindingCategory.DRC_ERC},
            banner="Running KiCad DRC/ERC verification",
            output=output,
            report_format=report_format,
            threshold=_parse_severity_threshold(fail_on),
            config_file=config_file,
            watch=watch,
            overrides=overrides,
        )
    )


@app.command()
def analyze(
    project_path: Path = _PROJECT_ARG,
    output: Path | None = _OUTPUT_OPT,
    report_format: ReportFormat | None = _FORMAT_OPT,
    fail_on: str = _FAIL_ON_OPT,
    config_file: Path | None = _CONFIG_OPT,
    watch: bool = _WATCH_OPT,
) -> None:
    """Run Layer 2 spatial, geometric, and physical heuristics verification."""
    _run_audit(
        AuditRequest(
            project_path=project_path,
            categories=set(HEURISTIC_CATEGORIES),
            banner="Running spatial & physical heuristics",
            output=output,
            report_format=report_format,
            threshold=_parse_severity_threshold(fail_on),
            config_file=config_file,
            watch=watch,
        )
    )


@app.command()
def vision(
    project_path: Path = _PROJECT_ARG,
    output: Path | None = _OUTPUT_OPT,
    report_format: ReportFormat | None = _FORMAT_OPT,
    vision_model: str = typer.Option(
        "gemini-3.8-flash",
        "--model",
        "-m",
        help="Vision model to use: gemini-3.8-flash, fable-5, gpt-6-astra, mock",
    ),
    fail_on: str = _FAIL_ON_OPT,
    config_file: Path | None = _CONFIG_OPT,
    watch: bool = _WATCH_OPT,
) -> None:
    """Run dedicated Layer 3 Multimodal Visual Review on a PCB layout."""

    def overrides(cfg: InspectorConfig) -> None:
        cfg.enable_vision = True
        cfg.vision_model = vision_model

    _run_audit(
        AuditRequest(
            project_path=project_path,
            categories={FindingCategory.VISION},
            banner=f"Starting Multimodal Vision Review ([cyan]{vision_model}[/cyan])",
            output=output,
            report_format=report_format,
            threshold=_parse_severity_threshold(fail_on),
            config_file=config_file,
            watch=watch,
            overrides=overrides,
        )
    )


@app.command()
def mcp(
    transport: str = typer.Option(
        "stdio",
        "--transport",
        "-t",
        help="Transport protocol: 'stdio' or 'sse'",
    ),
) -> None:
    """Launch native Model Context Protocol (MCP) server for autonomous AI coding agents."""
    from pcb_inspector.mcp.server import create_mcp_server

    if transport not in ("stdio", "sse", "streamable-http"):
        console.print(
            f"[bold red]Invalid transport '{escape(transport)}'. Choose 'stdio', 'sse', or 'streamable-http'.[/bold red]"
        )
        raise typer.Exit(code=1)

    server = create_mcp_server()
    if transport == "stdio":
        server.run(transport="stdio")
    elif transport == "sse":
        console.print("[bold cyan]Starting pcb-inspector MCP server (sse)...[/bold cyan]")
        server.run(transport="sse")
    else:
        console.print("[bold cyan]Starting pcb-inspector MCP server (streamable-http)...[/bold cyan]")
        server.run(transport="streamable-http")


def main() -> None:
    """Console entry point.

    Reads a .env file from the working directory or the nearest parent that
    has one, so an API key kept there reaches the vision providers. Variables
    already set in the environment win. This happens here and not in the
    Typer callback so that tests, which invoke ``app`` directly, never pick up
    a developer's real keys and make paid requests.
    """
    env_file = find_dotenv(usecwd=True)
    if env_file:
        load_dotenv(env_file, override=False)
    app()


if __name__ == "__main__":
    main()
