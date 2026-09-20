"""Regression tests for absolute pad placement.

Pad coordinates were rotated counter-clockwise. KiCad's stored orientation is
counter-clockwise as seen on screen, but its Y axis points down, so applying
it to the stored numbers must be clockwise. Every rotated footprint therefore
had its pads mirrored about the footprint origin, by up to 22.9mm on a real
board -- which silently corrupts decoupling distances, switching loop areas,
and the coordinates printed in every finding.

Expected values come from pcbnew's own PAD::GetPosition on KiCad 10.0.6,
against the CM5_MINIMA_3 demo. The clockwise form reproduces all 633 pads of
that board exactly; the counter-clockwise form reproduced 498.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.kicad.pcb_model import load_pcb_board
from pcb_inspector.rules.decoupling import DecouplingProximityRule


def _board_with_pad(
    tmp_path: Path,
    *,
    at_x: float,
    at_y: float,
    rot: float,
    local_x: float,
    local_y: float,
    layer: str = "F.Cu",
) -> tuple[float, float]:
    """Parse a one-pad board and return that pad's absolute position."""
    board = tmp_path / "b.kicad_pcb"
    board.write_text(
        "(kicad_pcb (version 20240108)\n"
        '  (net 0 "") (net 1 "SIG")\n'
        f'  (footprint "F" (layer "{layer}") (at {at_x} {at_y} {rot})\n'
        '    (property "Reference" "U1")\n'
        f'    (pad "1" smd rect (at {local_x} {local_y}) (size 1 1) '
        f'(layers "{layer}") (net 1 "SIG"))\n'
        "  )\n)",
        encoding="utf-8",
    )
    pad = load_pcb_board(board).footprints["U1"].pads[0]
    return pad.at_x, pad.at_y


def test_unrotated_pad_is_a_plain_offset(tmp_path: Path) -> None:
    x, y = _board_with_pad(tmp_path, at_x=10, at_y=20, rot=0, local_x=3, local_y=-4)
    assert (x, y) == pytest.approx((13.0, 16.0))


@pytest.mark.parametrize(
    ("rot", "expected"),
    [
        # A pad 1mm along +X, rotated about the footprint origin at (0, 0).
        # Clockwise in file coordinates, which is counter-clockwise on screen.
        (0, (1.0, 0.0)),
        (90, (0.0, -1.0)),
        (180, (-1.0, 0.0)),
        (270, (0.0, 1.0)),
        (-90, (0.0, 1.0)),
    ],
)
def test_rotation_direction(
    tmp_path: Path, rot: float, expected: tuple[float, float]
) -> None:
    x, y = _board_with_pad(tmp_path, at_x=0, at_y=0, rot=rot, local_x=1, local_y=0)
    assert (x, y) == pytest.approx(expected, abs=1e-9)


def test_rotation_preserves_distance_from_origin(tmp_path: Path) -> None:
    for rot in (0, 17, 45, 90, 133, 180, 271, -33):
        x, y = _board_with_pad(tmp_path, at_x=0, at_y=0, rot=rot, local_x=3, local_y=4)
        assert math.hypot(x, y) == pytest.approx(5.0, abs=1e-9)


@pytest.mark.parametrize(
    ("ref", "at_x", "at_y", "rot", "local_x", "local_y", "expected", "layer"),
    [
        # J1, rotated -90 on the front. The counter-clockwise form placed this
        # pad at (79.588, 29.520): 22.862mm away.
        ("J1", 88.9, 36.15, -90.0, 4.59, -9.312, (98.212, 40.74), "F.Cu"),
        ("J1", 88.9, 36.15, -90.0, -3.5, -5.146, (94.046, 32.65), "F.Cu"),
        ("J1", 88.9, 36.15, -90.0, 3.5, -5.146, (94.046, 39.65), "F.Cu"),
        # U401 sits on the back. KiCad already stores its offsets flipped, so
        # no extra mirroring may be applied.
        ("U401", 101.55, 38.19798, 0.0, -0.385, 1.0, (101.165, 39.198), "B.Cu"),
        ("U401", 101.55, 38.19798, 0.0, -0.385, 0.0, (101.165, 38.198), "B.Cu"),
    ],
)
def test_matches_pcbnew_on_real_footprints(
    tmp_path: Path,
    ref: str,
    at_x: float,
    at_y: float,
    rot: float,
    local_x: float,
    local_y: float,
    expected: tuple[float, float],
    layer: str,
) -> None:
    """Values taken from pcbnew's PAD::GetPosition on KiCad 10's CM5 demo."""
    x, y = _board_with_pad(
        tmp_path, at_x=at_x, at_y=at_y, rot=rot,
        local_x=local_x, local_y=local_y, layer=layer,
    )
    assert (x, y) == pytest.approx(expected, abs=1e-3)


def test_decoupling_distance_uses_corrected_geometry(tmp_path: Path) -> None:
    """The defect this caused: a capacitor placed correctly reads as far away.

    U1 is rotated 90 degrees with its power pin 2mm along local +X, which lands
    2mm above the footprint origin. C1 sits right there. Under the old
    counter-clockwise rotation the pin was computed 2mm below instead, putting
    the capacitor 4mm away and over the 3.5mm limit.
    """
    board = tmp_path / "b.kicad_pcb"
    board.write_text(
        "(kicad_pcb (version 20240108)\n"
        '  (net 0 "") (net 1 "+3V3")\n'
        '  (footprint "IC" (layer "F.Cu") (at 50 50 90)\n'
        '    (property "Reference" "U1")\n'
        '    (pad "1" smd rect (at 2 0) (size 1 1) (layers "F.Cu") '
        '(net 1 "+3V3") (pinfunction "VDD"))\n'
        "  )\n"
        '  (footprint "C" (layer "F.Cu") (at 50 48 0)\n'
        '    (property "Reference" "C1")\n'
        '    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 1 "+3V3"))\n'
        "  )\n)",
        encoding="utf-8",
    )
    parsed = load_pcb_board(board)

    pin = parsed.footprints["U1"].pads[0]
    assert (pin.at_x, pin.at_y) == pytest.approx((50.0, 48.0), abs=1e-9)

    assert DecouplingProximityRule().evaluate(parsed, InspectorConfig()) == []
