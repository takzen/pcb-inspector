"""Unit tests for PcbBoard model and parser."""

from __future__ import annotations

from pathlib import Path

from pcb_inspector.kicad.pcb_model import PcbBoard, load_pcb_board

SAMPLE_PCB = """(kicad_pcb (version 20240108) (generator pcbnew)
  (general (thickness 1.6))
  (net 0 "")
  (net 1 "GND")
  (net 2 "+3V3")
  (net 3 "USB_P")
  (net 4 "USB_N")
  (footprint "Package_QFP:LQFP-48" (layer "F.Cu") (at 50 50 0)
    (property "Reference" "U1")
    (pad "1" smd rect (at -3.5 0) (size 0.3 1.2) (layers "F.Cu") (net 2 "+3V3"))
    (pad "2" smd rect (at 3.5 0) (size 0.3 1.2) (layers "F.Cu") (net 1 "GND"))
  )
  (footprint "Capacitor_SMD:C_0402" (layer "F.Cu") (at 52 50 0)
    (property "Reference" "C1")
    (pad "1" smd rect (at -0.5 0) (size 0.6 0.5) (layers "F.Cu") (net 2 "+3V3"))
    (pad "2" smd rect (at 0.5 0) (size 0.6 0.5) (layers "F.Cu") (net 1 "GND"))
  )
  (segment (start 46.5 50) (end 51.5 50) (width 0.2) (layer "F.Cu") (net 2))
  (via (at 55 55) (size 0.8) (drill 0.4) (layers "F.Cu" "B.Cu") (net 1))
  (zone (net 1) (net_name "GND") (layer "B.Cu")
    (polygon (pts (xy 0 0) (xy 100 0) (xy 100 100) (xy 0 100)))
  )
)"""


def test_load_pcb_board(tmp_path: Path) -> None:
    pcb_file = tmp_path / "board.kicad_pcb"
    pcb_file.write_text(SAMPLE_PCB, encoding="utf-8")

    board = load_pcb_board(pcb_file)
    assert isinstance(board, PcbBoard)
    assert board.version == "20240108"
    assert board.thickness == 1.6

    # Footprints
    assert "U1" in board.footprints
    assert "C1" in board.footprints
    u1 = board.footprints["U1"]
    assert u1.is_ic is True
    assert len(u1.pads) == 2

    # Global pad coordinate calculation
    pad1 = u1.get_pad("1")
    assert pad1 is not None
    assert pad1.net_name == "+3V3"
    assert pad1.at_x == 46.5  # 50 - 3.5
    assert pad1.at_y == 50.0

    c1 = board.footprints["C1"]
    assert c1.is_capacitor is True

    # Tracks, Vias, Zones
    assert len(board.tracks) == 1
    assert board.tracks[0].net_num == 2
    assert board.tracks[0].width == 0.2

    assert len(board.vias) == 1
    assert board.vias[0].drill == 0.4

    assert len(board.zones) == 1
    assert board.zones[0].net_name == "GND"
    assert len(board.zones[0].points) == 4
