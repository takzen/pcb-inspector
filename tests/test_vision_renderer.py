"""Unit tests for BoardRenderer."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from pcb_inspector.kicad.cli_wrapper import KiCadCli
from pcb_inspector.vision.renderer import BoardRenderer

SAMPLE_PCB = """(kicad_pcb (version 20240108) (generator pcbnew)
  (general (thickness 1.6))
  (footprint "Package_QFP:LQFP-48" (layer "F.Cu") (at 50 50 0)
    (property "Reference" "U1")
    (pad "1" smd rect (at -3.5 0) (size 0.3 1.2) (layers "F.Cu") (net 1 "VCC"))
    (pad "2" smd rect (at 3.5 0) (size 0.3 1.2) (layers "F.Cu") (net 2 "GND"))
  )
  (footprint "Capacitor_SMD:C_0402" (layer "F.Cu") (at 52 50 0)
    (property "Reference" "C1")
    (pad "1" smd rect (at -0.5 0) (size 0.6 0.5) (layers "F.Cu") (net 1 "VCC"))
  )
  (segment (start 46.5 50) (end 51.5 50) (width 0.2) (layer "F.Cu") (net 1))
  (via (at 55 55) (size 0.8) (drill 0.4) (layers "F.Cu" "B.Cu") (net 2))
  (zone (net 2) (net_name "GND") (layer "B.Cu")
    (polygon (pts (xy 0 0) (xy 100 0) (xy 100 100) (xy 0 100)))
  )
)"""


def test_renderer_programmatic_fallback(tmp_path: Path) -> None:
    pcb_file = tmp_path / "test.kicad_pcb"
    pcb_file.write_text(SAMPLE_PCB, encoding="utf-8")

    # Force fallback by passing auto_detect=False
    renderer = BoardRenderer(kicad_cli=None, auto_detect=False)
    out_dir = tmp_path / "renders"
    renders = renderer.render_layers(pcb_file, output_dir=out_dir)

    assert "top" in renders
    assert "bottom" in renders

    top_svg = renders["top"]
    bot_svg = renders["bottom"]

    assert top_svg.exists()
    assert bot_svg.exists()

    top_content = top_svg.read_text(encoding="utf-8")
    assert "<svg" in top_content
    assert "U1" in top_content
    assert "C1" in top_content
    assert "<rect" in top_content
    assert "<line" in top_content


def test_renderer_with_cli_mock(tmp_path: Path) -> None:
    pcb_file = tmp_path / "test.kicad_pcb"
    pcb_file.write_text(SAMPLE_PCB, encoding="utf-8")

    mock_cli = MagicMock(spec=KiCadCli)
    mock_cli.is_available.return_value = True

    renderer = BoardRenderer(kicad_cli=mock_cli)
    out_dir = tmp_path / "renders"

    # Mock _render_with_cli to create the expected file
    def fake_render(p: Path, out: Path, side: str = "top") -> None:
        out.write_text("<svg>mocked</svg>", encoding="utf-8")

    renderer._render_with_cli = fake_render  # type: ignore

    renders = renderer.render_layers(pcb_file, output_dir=out_dir)
    assert renders["top"].exists()
    assert renders["bottom"].exists()
    assert renders["top"].read_text(encoding="utf-8") == "<svg>mocked</svg>"
