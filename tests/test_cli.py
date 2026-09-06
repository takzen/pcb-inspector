"""Unit tests for the CLI commands."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from pcb_inspector.cli.main import app

runner = CliRunner()


def test_cli_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "pcb-inspector" in result.stdout
    assert "check" in result.stdout
    assert "rules" in result.stdout
    assert "version" in result.stdout


def test_cli_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "v0.1.0" in result.stdout


def test_cli_rules() -> None:
    result = runner.invoke(app, ["rules"])
    assert result.exit_code == 0


def test_cli_check_with_empty_project(tmp_path: Path) -> None:
    dummy_pcb = tmp_path / "test.kicad_pcb"
    dummy_pcb.write_text('(kicad_pcb (version 20240108))', encoding="utf-8")

    out_md = tmp_path / "out.md"
    result = runner.invoke(app, ["check", str(dummy_pcb), "-o", str(out_md)])
    # An empty board with no edge cuts triggers DRC invalid_outline violations, so exit_code is 1
    assert result.exit_code in (0, 1)
    assert out_md.exists()
    assert "PCB Inspection Report" in out_md.read_text(encoding="utf-8")


def test_cli_check_html_format(tmp_path: Path) -> None:
    dummy_pcb = tmp_path / "test.kicad_pcb"
    dummy_pcb.write_text('(kicad_pcb (version 20240108))', encoding="utf-8")

    out_html = tmp_path / "out.html"
    result = runner.invoke(app, ["check", str(dummy_pcb), "-o", str(out_html), "-f", "html"])
    assert result.exit_code in (0, 1)
    assert out_html.exists()
    assert "<!DOCTYPE html>" in out_html.read_text(encoding="utf-8")

