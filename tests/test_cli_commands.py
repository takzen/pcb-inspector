"""Tests for additional CLI commands (drc, analyze, mcp) and file watcher."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pcb_inspector.cli.main import app
from pcb_inspector.cli.watcher import get_watch_files, watch_and_run
from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.core.models import FindingCategory
from pcb_inspector.rules.registry import default_registry

runner = CliRunner()


def test_cli_drc_command(tmp_path: Path) -> None:
    board = tmp_path / "test.kicad_pcb"
    board.write_text('(kicad_pcb (version 20240108))', encoding="utf-8")
    out_md = tmp_path / "drc_report.md"

    result = runner.invoke(app, ["drc", str(board), "-o", str(out_md), "-f", "markdown"])
    assert result.exit_code in (0, 1)
    assert out_md.exists()
    content = out_md.read_text(encoding="utf-8")
    assert "PCB Inspection Report" in content


def test_cli_drc_invalid_fail_on(tmp_path: Path) -> None:
    board = tmp_path / "test.kicad_pcb"
    board.write_text('(kicad_pcb (version 20240108))', encoding="utf-8")

    result = runner.invoke(app, ["drc", str(board), "--fail-on", "INVALID_SEVERITY"])
    assert result.exit_code == 1
    assert "Invalid --fail-on value" in result.stdout


def test_cli_analyze_command(tmp_path: Path) -> None:
    board = tmp_path / "test.kicad_pcb"
    board.write_text(
        """(kicad_pcb (version 20240108)
  (footprint "Package_SO:SOIC-8" (layer "F.Cu") (at 10 10)
    (property "Reference" "U1")
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 1 "+3V3") (pinfunction "VDD"))
  )
)""",
        encoding="utf-8",
    )
    out_json = tmp_path / "heuristics.json"

    result = runner.invoke(app, ["analyze", str(board), "-o", str(out_json), "-f", "json"])
    assert result.exit_code in (0, 1)
    assert out_json.exists()


def test_cli_mcp_invalid_transport() -> None:
    result = runner.invoke(app, ["mcp", "--transport", "unknown_transport"])
    assert result.exit_code == 1
    assert "Invalid transport" in result.stdout


def test_watcher_get_watch_files(tmp_path: Path) -> None:
    pcb = tmp_path / "board.kicad_pcb"
    pcb.write_text('(kicad_pcb (version 20240108))', encoding="utf-8")
    cfg = tmp_path / ".pcb-inspector.yaml"
    cfg.write_text("fail_on: CRITICAL", encoding="utf-8")
    ignored = tmp_path / "notes.txt"
    ignored.write_text("not watched", encoding="utf-8")

    files = get_watch_files(tmp_path)
    assert pcb in files
    assert cfg in files
    assert ignored not in files


def test_watcher_watch_and_run(tmp_path: Path) -> None:
    pcb = tmp_path / "board.kicad_pcb"
    pcb.write_text('(kicad_pcb (version 20240108))', encoding="utf-8")

    run_counts = [0]

    def on_change() -> bool:
        run_counts[0] += 1
        return True

    # Test initial run + max_iterations termination
    watch_and_run(pcb, on_change, poll_interval=0.05, max_iterations=1)
    assert run_counts[0] == 1


def test_registry_evaluate_filtered(tmp_path: Path) -> None:
    board = tmp_path / "board.kicad_pcb"
    board.write_text('(kicad_pcb (version 20240108))', encoding="utf-8")

    cfg = InspectorConfig()
    # Evaluate DRC only
    drc_findings = default_registry.evaluate_filtered(
        context=board,
        config=cfg,
        categories={FindingCategory.DRC_ERC},
    )
    for f in drc_findings:
        assert f.category == FindingCategory.DRC_ERC

    # Evaluate Heuristics only
    heur_rules = default_registry.get_rules_by_category([FindingCategory.DECOUPLING])
    assert len(heur_rules) >= 1
    assert any(r.rule_id == "HEUR-DEC-001" for r in heur_rules)


# --------------------------------------------------------------------------
# P2-6: the audit commands share one implementation
# --------------------------------------------------------------------------

AUDIT_COMMANDS = ["check", "drc", "analyze", "vision"]


@pytest.mark.parametrize("command", AUDIT_COMMANDS)
def test_every_audit_command_offers_the_shared_options(command: str) -> None:
    """The commands drifted apart while each carried its own copy.

    `vision` in particular exited non-zero without offering --fail-on, the flag
    that decides when it should.
    """
    result = runner.invoke(app, [command, "--help"])
    assert result.exit_code == 0
    for flag in ("--output", "--format", "--fail-on", "--config", "--watch"):
        assert flag in result.stdout, f"{command} is missing {flag}"


@pytest.mark.parametrize("command", AUDIT_COMMANDS)
def test_invalid_fail_on_is_rejected(command: str, tmp_path: Path) -> None:
    board = tmp_path / "b.kicad_pcb"
    board.write_text("(kicad_pcb (version 20240108))", encoding="utf-8")

    result = runner.invoke(app, [command, str(board), "--fail-on", "NONSENSE"])
    assert result.exit_code == 1
    assert "Invalid --fail-on" in result.stdout


def test_vision_fail_on_controls_the_exit_code() -> None:
    """The mock client reports warnings but no criticals."""
    board = (
        Path(__file__).parent / "golden_samples" / "clean_board" / "clean_board.kicad_pcb"
    )

    passing = runner.invoke(app, ["vision", str(board), "-m", "mock", "--fail-on", "CRITICAL"])
    assert passing.exit_code == 0

    failing = runner.invoke(app, ["vision", str(board), "-m", "mock", "--fail-on", "WARNING"])
    assert failing.exit_code == 1


def test_vision_runs_through_the_registry_and_records_its_layer() -> None:
    """Going through the registry is what gives vision layer status and
    turns a crashing rule into a finding rather than silence."""
    board = (
        Path(__file__).parent / "golden_samples" / "clean_board" / "clean_board.kicad_pcb"
    )
    out_dir = Path(tempfile.mkdtemp())
    report = out_dir / "r"

    result = runner.invoke(
        app, ["vision", str(board), "-m", "mock", "-o", str(report), "-f", "json"]
    )
    assert result.exit_code == 0

    payload = json.loads(report.read_text(encoding="utf-8"))
    layers = payload["metadata"]["layers"]
    assert layers["layer3_vision"] == "executed"
    assert layers["layer1_drc_erc"] == "disabled"


# --------------------------------------------------------------------------
# P3-1 / P3-2: formats and config errors are reported, not swallowed
# --------------------------------------------------------------------------

CLEAN = Path(__file__).parent / "golden_samples" / "clean_board" / "clean_board.kicad_pcb"


def test_unknown_report_format_is_rejected(tmp_path: Path) -> None:
    """`-f pdf` used to be accepted and write nothing."""
    result = runner.invoke(app, ["analyze", str(CLEAN), "-o", str(tmp_path / "r"), "-f", "pdf"])
    assert result.exit_code == 2
    assert list(tmp_path.iterdir()) == []


def test_format_without_output_writes_to_output_dir(tmp_path: Path, monkeypatch) -> None:
    """`-f html` alone used to write nothing and say nothing."""
    cfg = tmp_path / "c.yaml"
    out_dir = tmp_path / "reports"
    cfg.write_text(f"output_dir: '{out_dir.as_posix()}'\n", encoding="utf-8")

    result = runner.invoke(app, ["analyze", str(CLEAN), "-f", "html", "-c", str(cfg)])

    assert result.exit_code == 0, result.stdout
    written = list(out_dir.iterdir())
    assert [p.name for p in written] == ["clean_board_report"]
    assert "<html" in written[0].read_text(encoding="utf-8").lower()


def test_no_format_and_no_output_writes_nothing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["analyze", str(CLEAN)])
    assert result.exit_code == 0
    assert not (tmp_path / "reports").exists()


def test_format_is_case_insensitive(tmp_path: Path) -> None:
    out = tmp_path / "r.json"
    result = runner.invoke(app, ["analyze", str(CLEAN), "-o", str(out), "-f", "JSON"])
    assert result.exit_code == 0
    assert json.loads(out.read_text(encoding="utf-8"))["summary"]


def test_missing_config_file_is_a_usage_error(tmp_path: Path) -> None:
    result = runner.invoke(app, ["analyze", str(CLEAN), "-c", str(tmp_path / "nope.yaml")])
    assert result.exit_code == 2


def test_broken_config_file_exits_2_not_1(tmp_path: Path) -> None:
    """A broken config means nothing was checked; that must not read as a failed audit."""
    cfg = tmp_path / "c.yaml"
    cfg.write_text("- not\n- a mapping\n", encoding="utf-8")
    result = runner.invoke(app, ["analyze", str(CLEAN), "-c", str(cfg)])
    assert result.exit_code == 2
    assert "Configuration error" in result.stdout


@pytest.mark.parametrize("command", ["check", "drc", "analyze"])
def test_target_without_a_design_is_a_usage_error(tmp_path: Path, command: str) -> None:
    """An empty directory used to report PASSED with a 100/100 health score."""
    (tmp_path / "README.md").write_text("no board here\n", encoding="utf-8")
    result = runner.invoke(app, [command, str(tmp_path)])
    assert result.exit_code == 2
    assert "No KiCad board or schematic found" in result.stdout


def test_schematic_only_directory_is_audited(tmp_path: Path) -> None:
    (tmp_path / "power.kicad_sch").write_text("(kicad_sch (version 20231120))", encoding="utf-8")
    result = runner.invoke(app, ["analyze", str(tmp_path)])
    assert "No KiCad board or schematic found" not in result.stdout
    assert result.exit_code == 0


def test_console_entry_point_reads_dotenv_without_overriding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A key kept in .env reaches the providers; one already exported wins."""
    import importlib

    # The package re-exports the function main, which shadows the module name.
    cli_main = importlib.import_module("pcb_inspector.cli.main")

    (tmp_path / ".env").write_text(
        "GOOGLE_API_KEY=from-dotenv\nOPENAI_API_KEY=from-dotenv\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "app", lambda: None)

    import os
    from unittest import mock

    # load_dotenv writes os.environ directly; restore it whole afterwards.
    with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "exported"}):
        cli_main.main()

        assert os.environ["GOOGLE_API_KEY"] == "from-dotenv"
        assert os.environ["OPENAI_API_KEY"] == "exported"
    assert "GOOGLE_API_KEY" not in os.environ


def test_app_itself_never_reads_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests invoke the app directly; a developer's real key must not leak in."""
    import os

    (tmp_path / ".env").write_text("GOOGLE_API_KEY=real-key\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    runner.invoke(app, ["rules"])

    assert "GOOGLE_API_KEY" not in os.environ
