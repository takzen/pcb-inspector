"""Regression tests for the KiCad 10 board file format.

Up to KiCad 9 a board declares a net table, (net 5 "GND"), and every pad,
track, via and zone refers to it by number. KiCad 10 (format 20260206, written
by 10.0.6) dropped the table and names nets inline: (net "GND").

The parser read only numbers. A board saved by KiCad 10 therefore parsed with
no nets at all, and four of the five Layer 2 rules silently reported nothing:
the deliberately flawed golden sample went from six findings to one.

The fixtures under golden_samples/kicad10_format/ are the existing golden
boards upgraded with `kicad-cli pcb upgrade` from KiCad 10.0.6, so these tests
need no KiCad installation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.core.layers import HEURISTIC_CATEGORIES
from pcb_inspector.kicad.pcb_model import _NetTable, load_pcb_board
from pcb_inspector.kicad.sexpr_parser import parse_sexpr
from pcb_inspector.rules import board_loader
from pcb_inspector.rules.registry import init_default_registry

SAMPLES = Path(__file__).parent / "golden_samples"


def _findings(path: Path) -> list[tuple[str, str]]:
    board_loader.clear_cache()
    found = init_default_registry().evaluate_filtered(
        context=path, config=InspectorConfig(), categories=HEURISTIC_CATEGORIES
    )
    return sorted((f.id, f.severity.value) for f in found)


# --------------------------------------------------------------------------
# Same board, both formats, same audit
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["flawed_board", "mixed_signal_board"])
def test_kicad10_board_audits_identically_to_its_kicad9_original(name: str) -> None:
    original = SAMPLES / name / f"{name}.kicad_pcb"
    upgraded = SAMPLES / "kicad10_format" / f"{name}.kicad_pcb"
    assert "(version 20260206)" in upgraded.read_text(encoding="utf-8")

    assert _findings(upgraded) == _findings(original)


def test_kicad10_board_has_its_nets() -> None:
    board = load_pcb_board(SAMPLES / "kicad10_format" / "flawed_board.kicad_pcb")

    assert {"GND", "+3V3", "USB_P", "USB_N", "SW", "+12V"} <= set(board.nets.values())
    assert all(t.net_name for t in board.tracks)
    assert all(p.net_name for fp in board.footprints.values() for p in fp.pads)


def test_flawed_board_is_not_blind_in_kicad10_format() -> None:
    """The regression itself: every rule that fired before must fire now."""
    board_loader.clear_cache()
    found = init_default_registry().evaluate_filtered(
        context=SAMPLES / "kicad10_format" / "flawed_board.kicad_pcb",
        config=InspectorConfig(),
        categories=HEURISTIC_CATEGORIES,
    )
    rules = {f.rule_id for f in found}
    assert rules == {
        "HEUR-DEC-001",
        "HEUR-PWR-001",
        "HEUR-DIFF-001",
        "HEUR-DCDC-001",
        "HEUR-GND-001",
    }


# --------------------------------------------------------------------------
# Net reference resolution
# --------------------------------------------------------------------------


def _table(text: str) -> _NetTable:
    return _NetTable(parse_sexpr(text))


def test_numbered_format_resolves_through_the_table() -> None:
    table = _table('(kicad_pcb (net 0 "") (net 5 "GND"))')
    assert table.inline_names is False
    assert table.resolve(["net", "5"]) == (5, "GND")


def test_inline_format_resolves_names_and_numbers_them() -> None:
    table = _table("(kicad_pcb (version 20260206))")
    assert table.inline_names is True

    gnd = table.resolve(["net", "GND"])
    assert gnd[1] == "GND"
    assert table.resolve(["net", "GND"]) == gnd  # stable number for the same name
    assert table.by_num[gnd[0]] == "GND"


def test_a_net_named_with_digits_is_a_name_in_the_inline_format() -> None:
    """The tokenizer strips quotes, so (net "1") looks like (net 1)."""
    table = _table("(kicad_pcb (version 20260206))")
    num, name = table.resolve(["net", "1"])
    assert name == "1"


def test_number_missing_from_table_falls_back_to_inline_name() -> None:
    table = _table('(kicad_pcb (net 1 "GND"))')
    assert table.resolve(["net", "9", "VBUS"]) == (9, "VBUS")


def test_absent_net_node_is_unconnected() -> None:
    table = _table("(kicad_pcb)")
    assert table.resolve(None) == (0, "")
    assert table.resolve(["net"]) == (0, "")


def test_zone_net_given_by_name_in_a_numbered_file(tmp_path: Path) -> None:
    """The fix that predates the audit: some zones name their net even in a
    file that otherwise uses numbers. It had no test."""
    board = tmp_path / "b.kicad_pcb"
    board.write_text(
        '(kicad_pcb (version 20240108)\n'
        '  (net 0 "") (net 1 "GND")\n'
        '  (zone (net "GND") (layer "B.Cu")\n'
        '    (polygon (pts (xy 0 0) (xy 10 0) (xy 10 10) (xy 0 10))))\n'
        ")",
        encoding="utf-8",
    )
    zone = load_pcb_board(board).zones[0]
    assert (zone.net_num, zone.net_name) == (1, "GND")


def test_zone_net_name_node_is_honoured(tmp_path: Path) -> None:
    board = tmp_path / "b.kicad_pcb"
    board.write_text(
        '(kicad_pcb (version 20240108)\n'
        '  (net 0 "")\n'
        '  (zone (net 0) (net_name "GND") (layer "B.Cu")\n'
        '    (polygon (pts (xy 0 0) (xy 10 0) (xy 10 10) (xy 0 10))))\n'
        ")",
        encoding="utf-8",
    )
    assert load_pcb_board(board).zones[0].net_name == "GND"
