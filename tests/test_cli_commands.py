"""Tests for additional CLI commands (drc, analyze, mcp) and file watcher."""

from __future__ import annotations

from pathlib import Path

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
