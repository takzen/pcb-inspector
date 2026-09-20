"""Regression tests for curved copper tracks.

KiCad writes a curved trace as ``(arc ...)`` with a midpoint, not as
``(segment ...)``. The parser read only segments, so every net routed with
curves was measured short. That is precisely the routing style used for
length tuning on differential pairs, so the omission produced CRITICAL skew
findings on boards that were in fact correctly matched.

Found by running the audit against KiCad 10's own CM5_MINIMA_3 demo, where
11 of 13 reported pair-skew violations were caused by uncounted arc copper.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.kicad.pcb_model import TrackSegment, arc_length, load_pcb_board
from pcb_inspector.rules.differential_pairs import DifferentialPairSkewRule


def _write(tmp_path: Path, body: str) -> Path:
    board = tmp_path / "b.kicad_pcb"
    board.write_text(f"(kicad_pcb (version 20240108)\n{body}\n)", encoding="utf-8")
    return board


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------


def test_arc_length_quarter_circle() -> None:
    """Unit circle quarter arc from (1,0) to (0,1) through (cos45, sin45)."""
    r2 = math.sqrt(2) / 2
    length = arc_length((1.0, 0.0), (r2, r2), (0.0, 1.0))
    assert length == pytest.approx(math.pi / 2, abs=1e-9)


def test_arc_length_semicircle() -> None:
    length = arc_length((1.0, 0.0), (0.0, 1.0), (-1.0, 0.0))
    assert length == pytest.approx(math.pi, abs=1e-9)


def test_arc_length_major_arc_is_not_mistaken_for_minor() -> None:
    """The midpoint decides which way round the circle the arc runs."""
    minor = arc_length((1.0, 0.0), (math.sqrt(2) / 2, math.sqrt(2) / 2), (0.0, 1.0))
    major = arc_length((1.0, 0.0), (-1.0, 0.0), (0.0, 1.0))
    assert minor == pytest.approx(math.pi / 2, abs=1e-9)
    assert major == pytest.approx(3 * math.pi / 2, abs=1e-9)


def test_collinear_arc_falls_back_to_chord() -> None:
    """Degenerate input must not divide by zero."""
    assert arc_length((0.0, 0.0), (5.0, 0.0), (10.0, 0.0)) == pytest.approx(10.0)


def test_straight_segment_length_unchanged() -> None:
    t = TrackSegment(
        start_x=0, start_y=0, end_x=3, end_y=4, width=0.25,
        layer="F.Cu", net_num=1, net_name="SIG",
    )
    assert t.is_arc is False
    assert t.length == pytest.approx(5.0)


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def test_arc_tracks_are_parsed_as_copper(tmp_path: Path) -> None:
    board = _write(
        tmp_path,
        '  (net 0 "") (net 1 "SIG")\n'
        '  (segment (start 0 0) (end 10 0) (width 0.2) (layer "F.Cu") (net 1))\n'
        '  (arc (start 10 0) (mid 10.707107 0.292893) (end 11 1) '
        '(width 0.2) (layer "F.Cu") (net 1))',
    )
    parsed = load_pcb_board(board)

    assert len(parsed.tracks) == 2
    straight, arc = parsed.tracks[0], parsed.tracks[1]
    assert straight.is_arc is False
    assert arc.is_arc is True
    assert arc.net_name == "SIG"
    assert arc.layer == "F.Cu"
    assert arc.width == pytest.approx(0.2)
    # Quarter circle of radius 1.
    assert arc.length == pytest.approx(math.pi / 2, abs=1e-3)


def test_arc_without_mid_is_still_usable(tmp_path: Path) -> None:
    """A malformed arc degrades to its chord rather than breaking the parse."""
    board = _write(
        tmp_path,
        '  (net 0 "") (net 1 "SIG")\n'
        '  (arc (start 0 0) (end 3 4) (width 0.2) (layer "F.Cu") (net 1))',
    )
    parsed = load_pcb_board(board)
    assert len(parsed.tracks) == 1
    assert parsed.tracks[0].length == pytest.approx(5.0)


# --------------------------------------------------------------------------
# The defect this caused
# --------------------------------------------------------------------------


def test_length_tuned_pair_with_arcs_is_not_flagged(tmp_path: Path) -> None:
    """A pair matched by curving one leg must not read as skewed.

    _N runs 10mm straight then a quarter arc of radius 1 (1.5708mm).
    _P runs 11.5708mm straight. Total lengths match to within 1um, but with
    arcs uncounted _N measured 1.5708mm short — a CRITICAL false positive.
    """
    quarter = math.pi / 2
    board = _write(
        tmp_path,
        '  (net 0 "") (net 1 "USB_P") (net 2 "USB_N")\n'
        f'  (segment (start 0 0) (end {10 + quarter} 0) (width 0.2) (layer "F.Cu") (net 1))\n'
        '  (segment (start 0 5) (end 10 5) (width 0.2) (layer "F.Cu") (net 2))\n'
        '  (arc (start 10 5) (mid 10.707107 5.292893) (end 11 6) '
        '(width 0.2) (layer "F.Cu") (net 2))',
    )
    parsed = load_pcb_board(board)

    len_p = sum(t.length for t in parsed.get_tracks_by_net("USB_P"))
    len_n = sum(t.length for t in parsed.get_tracks_by_net("USB_N"))
    assert abs(len_p - len_n) < 1e-3

    assert DifferentialPairSkewRule().evaluate(parsed, InspectorConfig()) == []


def test_undersized_arc_track_is_still_checked(tmp_path: Path) -> None:
    """Arcs were invisible to every rule, not just the skew check."""
    from pcb_inspector.rules.trace_width import PowerTraceWidthRule

    board = _write(
        tmp_path,
        '  (net 0 "") (net 1 "+5V")\n'
        '  (arc (start 0 0) (mid 0.707107 0.292893) (end 1 1) '
        '(width 0.1) (layer "F.Cu") (net 1))',
    )
    parsed = load_pcb_board(board)

    findings = PowerTraceWidthRule().evaluate(parsed, InspectorConfig())
    assert len(findings) == 1
    assert findings[0].raw_data["measured_width_mm"] == pytest.approx(0.1)
