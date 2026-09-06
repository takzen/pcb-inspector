"""Integration tests for vision CLI commands."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from pcb_inspector.cli.main import app

runner = CliRunner()

SAMPLE_PCB = """(kicad_pcb (version 20240108) (generator pcbnew)
  (general (thickness 1.6))
  (footprint "Package_QFP:LQFP-48" (layer "F.Cu") (at 50 50 0)
    (property "Reference" "U1")
    (pad "1" smd rect (at -3.5 0) (size 0.3 1.2) (layers "F.Cu") (net 1 "VCC"))
  )
)"""


def test_cli_check_with_vision(tmp_path: Path) -> None:
    pcb_file = tmp_path / "board.kicad_pcb"
    pcb_file.write_text(SAMPLE_PCB, encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "check",
            str(pcb_file),
            "--vision",
            "--vision-model",
            "mock",
        ],
    )
    assert "VISION-AI-001" in result.output


def test_cli_vision_command(tmp_path: Path) -> None:
    pcb_file = tmp_path / "board.kicad_pcb"
    pcb_file.write_text(SAMPLE_PCB, encoding="utf-8")

    report_path = tmp_path / "report.md"
    result = runner.invoke(
        app,
        [
            "vision",
            str(pcb_file),
            "--model",
            "mock",
            "-o",
            str(report_path),
        ],
    )
    assert "Starting Multimodal Vision Review" in result.output
    assert report_path.exists()
    assert "VISION-AI-001" in report_path.read_text(encoding="utf-8")

