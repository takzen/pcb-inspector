"""Tests for net class settings, the layer stack, and multi-layer zones.

All three were found to matter while auditing KiCad 10's CM5_MINIMA_3 demo,
a six-layer Raspberry Pi CM5 carrier:

* Net classes live in the .kicad_pro project file from KiCad 6 onward, not in
  the board. Without them, nine power nets routed at exactly the 0.15mm their
  Default class specifies were reported as undersized.
* Copper layers are listed in physical stack order, but their ordinals are not
  in that order, so adjacency cannot be derived from the numbers.
* A zone may span several layers via (layers ...) rather than (layer ...).
  Reading only the singular form hid that board's inner ground planes and made
  138 correctly referenced segments look unreferenced.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.core.models import Severity
from pcb_inspector.kicad.net_classes import (
    NetClass,
    NetClassSettings,
    load_net_classes,
    parse_legacy_net_classes,
    parse_project_net_classes,
)
from pcb_inspector.kicad.pcb_model import load_pcb_board
from pcb_inspector.kicad.sexpr_parser import parse_sexpr
from pcb_inspector.rules.return_paths import GroundPlaneIntegrityRule
from pcb_inspector.rules.trace_width import PowerTraceWidthRule


def _project(tmp_path: Path, stem: str, net_settings: dict) -> Path:
    path = tmp_path / f"{stem}.kicad_pro"
    path.write_text(json.dumps({"net_settings": net_settings}), encoding="utf-8")
    return path


def _board(tmp_path: Path, stem: str, body: str) -> Path:
    path = tmp_path / f"{stem}.kicad_pcb"
    path.write_text(f"(kicad_pcb (version 20240108)\n{body}\n)", encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# Pattern resolution
# --------------------------------------------------------------------------


@pytest.fixture
def cm5_like() -> NetClassSettings:
    """Patterns copied from KiCad 10's CM5_MINIMA_3 demo."""
    return parse_project_net_classes(
        {
            "net_settings": {
                "classes": [
                    {"name": "Default", "track_width": 0.15, "clearance": 0.13},
                    {"name": "100ohm", "track_width": 0.127, "diff_pair_width": 0.13},
                    {"name": "90ohm", "track_width": 0.147, "diff_pair_gap": 0.154},
                ],
                "netclass_patterns": [
                    {"netclass": "90ohm", "pattern": "/*USB_*"},
                    {"netclass": "100ohm", "pattern": "/*ETH_PI*"},
                    {"netclass": "100ohm", "pattern": "/*HDMI_P*.D*"},
                    {"netclass": "90ohm", "pattern": "/*PCIE*"},
                    {"netclass": "100ohm", "pattern": "/*HDMI_P*.CK*"},
                ],
            }
        }
    )


@pytest.mark.parametrize(
    ("net", "expected"),
    [
        ("/CM5/HDMI_PI.CK_P", "100ohm"),
        ("/CM5/HDMI_PI.D0_P", "100ohm"),
        ("/CM5/ETH_PI.TRD0_P", "100ohm"),
        ("/CM5/PCIE_PI.RX_N", "90ohm"),
        ("/USB_C.D_P", "90ohm"),
        # Unmatched nets fall through to Default.
        ("GND", "Default"),
        ("+3V3_PI", "Default"),
    ],
)
def test_pattern_resolution(cm5_like: NetClassSettings, net: str, expected: str) -> None:
    resolved = cm5_like.class_for(net)
    assert resolved is not None
    assert resolved.name == expected


def test_pattern_matching_is_case_sensitive() -> None:
    """fnmatch folds case on Windows; KiCad net names do not."""
    settings = parse_project_net_classes(
        {
            "net_settings": {
                "classes": [{"name": "RF", "track_width": 0.35}],
                "netclass_patterns": [{"netclass": "RF", "pattern": "ANT_*"}],
            }
        }
    )
    assert settings.class_for("ANT_IN") is not None
    assert settings.class_for("ant_in") is None


def test_explicit_assignment_outranks_pattern() -> None:
    settings = parse_project_net_classes(
        {
            "net_settings": {
                "classes": [
                    {"name": "Default", "track_width": 0.2},
                    {"name": "Power", "track_width": 0.6},
                ],
                "netclass_patterns": [{"netclass": "Default", "pattern": "*"}],
                "netclass_assignments": {"+12V": "Power"},
            }
        }
    )
    power = settings.class_for("+12V")
    assert power is not None and power.name == "Power"


def test_zero_widths_are_treated_as_unset() -> None:
    """KiCad writes 0 for inherit; a zero-width expectation is meaningless."""
    settings = parse_project_net_classes(
        {"net_settings": {"classes": [{"name": "Default", "track_width": 0}]}}
    )
    resolved = settings.class_for("ANY")
    assert resolved is not None
    assert resolved.track_width is None


def test_no_net_settings_yields_no_classes() -> None:
    assert parse_project_net_classes({}).class_for("GND") is None


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def test_loads_net_classes_from_sibling_project(tmp_path: Path) -> None:
    _project(tmp_path, "b", {"classes": [{"name": "Default", "track_width": 0.25}]})
    board = _board(tmp_path, "b", '  (net 0 "") (net 1 "+5V")')
    parsed = load_pcb_board(board)

    resolved = parsed.net_class_for("+5V")
    assert resolved is not None
    assert resolved.track_width == pytest.approx(0.25)


def test_legacy_in_board_net_classes_still_work(tmp_path: Path) -> None:
    """KiCad 5 kept net classes inline, listing nets with add_net."""
    text = (
        '(kicad_pcb (version 20240108)\n'
        '  (net 0 "") (net 1 "+3.3V_PROBE")\n'
        '  (net_class "Power" "Power nets"\n'
        '    (clearance 0.25) (trace_width 0.6) (via_dia 0.8) (via_drill 0.4)\n'
        '    (add_net "+3.3V_PROBE"))\n'
        ')'
    )
    settings = parse_legacy_net_classes(parse_sexpr(text))
    resolved = settings.class_for("+3.3V_PROBE")
    assert resolved is not None
    assert resolved.name == "Power"
    assert resolved.track_width == pytest.approx(0.6)
    assert resolved.clearance == pytest.approx(0.25)


def test_unreadable_project_does_not_fail_the_load(tmp_path: Path) -> None:
    """Net classes enrich the audit; losing them must not abort it."""
    (tmp_path / "b.kicad_pro").write_text("{ not json", encoding="utf-8")
    board = _board(tmp_path, "b", '  (net 0 "")')
    assert load_net_classes(board).classes == {}
    assert load_pcb_board(board).net_classes.classes == {}


# --------------------------------------------------------------------------
# Layer stack
# --------------------------------------------------------------------------


def test_stack_order_comes_from_list_order_not_ordinals(tmp_path: Path) -> None:
    """A six-layer board reads F.Cu=0, In1=4, In2=6, In3=8, In4=10, B.Cu=2."""
    board = _board(
        tmp_path,
        "b",
        '  (layers\n'
        '    (0 "F.Cu" signal)\n'
        '    (4 "In1.Cu" signal)\n'
        '    (6 "In2.Cu" signal)\n'
        '    (8 "In3.Cu" signal)\n'
        '    (10 "In4.Cu" signal)\n'
        '    (2 "B.Cu" signal)\n'
        '    (25 "Edge.Cuts" user)\n'
        '  )',
    )
    parsed = load_pcb_board(board)

    assert parsed.copper_layers == ["F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu", "B.Cu"]
    assert parsed.adjacent_copper_layers("F.Cu") == ["In1.Cu"]
    assert parsed.adjacent_copper_layers("In2.Cu") == ["In1.Cu", "In3.Cu"]
    assert parsed.adjacent_copper_layers("B.Cu") == ["In4.Cu"]


def test_unknown_stack_falls_back_to_two_layers(tmp_path: Path) -> None:
    board = _board(tmp_path, "b", '  (net 0 "")')
    parsed = load_pcb_board(board)
    assert parsed.copper_layers == []
    assert parsed.adjacent_copper_layers("F.Cu") == ["B.Cu"]


# --------------------------------------------------------------------------
# Multi-layer zones
# --------------------------------------------------------------------------


def test_multi_layer_zone_expands_to_one_zone_per_layer(tmp_path: Path) -> None:
    board = _board(
        tmp_path,
        "b",
        '  (net 0 "") (net 1 "GND")\n'
        '  (zone (net 1) (net_name "GND") (layers "F.Cu" "In1.Cu" "B.Cu")\n'
        '    (polygon (pts (xy 0 0) (xy 50 0) (xy 50 50) (xy 0 50)))\n'
        '    (filled_polygon (layer "F.Cu") (pts (xy 0 0) (xy 10 0) (xy 10 10) (xy 0 10)))\n'
        '    (filled_polygon (layer "In1.Cu") (pts (xy 0 0) (xy 50 0) (xy 50 50) (xy 0 50)))\n'
        '  )',
    )
    parsed = load_pcb_board(board)

    by_layer = {z.layer: z for z in parsed.zones}
    assert set(by_layer) == {"F.Cu", "In1.Cu", "B.Cu"}
    # Poured copper follows each filled_polygon's own layer tag.
    assert len(by_layer["F.Cu"].filled_polygons) == 1
    assert len(by_layer["In1.Cu"].filled_polygons) == 1
    # A declared layer with no fill still exists, falling back to the outline.
    assert by_layer["B.Cu"].filled_polygons == []
    assert len(by_layer["B.Cu"].copper_polygons) == 1


def test_inner_plane_from_multi_layer_zone_references_tracks(tmp_path: Path) -> None:
    """The bug this caused: an inner GND plane declared with (layers ...) was
    invisible, so every trace above it read as unreferenced."""
    board = _board(
        tmp_path,
        "b",
        '  (layers (0 "F.Cu" signal) (4 "In1.Cu" signal) (2 "B.Cu" signal))\n'
        '  (net 0 "") (net 1 "GND") (net 2 "SIG")\n'
        '  (segment (start 5 5) (end 40 5) (width 0.2) (layer "F.Cu") (net 2))\n'
        '  (zone (net 1) (net_name "GND") (layers "In1.Cu" "B.Cu")\n'
        '    (polygon (pts (xy 0 0) (xy 50 0) (xy 50 50) (xy 0 50)))\n'
        '    (filled_polygon (layer "In1.Cu") (pts (xy 0 0) (xy 50 0) (xy 50 50) (xy 0 50)))\n'
        '    (filled_polygon (layer "B.Cu") (pts (xy 0 0) (xy 50 0) (xy 50 50) (xy 0 50)))\n'
        '  )',
    )
    parsed = load_pcb_board(board)
    assert GroundPlaneIntegrityRule().evaluate(parsed, InspectorConfig()) == []


def test_plane_two_layers_away_is_not_a_reference(tmp_path: Path) -> None:
    """Return current takes the nearest plane, not one across the stack."""
    board = _board(
        tmp_path,
        "b",
        '  (layers (0 "F.Cu" signal) (4 "In1.Cu" signal) (6 "In2.Cu" signal) (2 "B.Cu" signal))\n'
        '  (net 0 "") (net 1 "GND") (net 2 "SIG")\n'
        '  (segment (start 5 5) (end 40 5) (width 0.2) (layer "F.Cu") (net 2))\n'
        '  (zone (net 1) (net_name "GND") (layer "In2.Cu")\n'
        '    (polygon (pts (xy 0 0) (xy 50 0) (xy 50 50) (xy 0 50)))\n'
        '  )',
    )
    parsed = load_pcb_board(board)
    findings = GroundPlaneIntegrityRule().evaluate(parsed, InspectorConfig())
    assert len(findings) == 1


# --------------------------------------------------------------------------
# Net classes change what the trace width rule reports
# --------------------------------------------------------------------------


def test_trace_matching_its_net_class_is_a_suggestion_not_a_warning(tmp_path: Path) -> None:
    """KiCad treats net class widths as defaults, not constraints.

    A trace routed at exactly its declared width is a deliberate choice, so it
    cannot be a defect, even when below our generic power-rail guideline.
    """
    _project(tmp_path, "b", {"classes": [{"name": "Default", "track_width": 0.15}]})
    board = _board(
        tmp_path,
        "b",
        '  (net 0 "") (net 1 "+3V3")\n'
        '  (segment (start 0 0) (end 20 0) (width 0.15) (layer "F.Cu") (net 1))',
    )
    parsed = load_pcb_board(board)

    findings = PowerTraceWidthRule().evaluate(
        parsed, InspectorConfig(min_power_trace_width_mm=0.3)
    )
    assert len(findings) == 1
    assert findings[0].severity is Severity.SUGGESTION
    assert findings[0].raw_data["contradicts_net_class"] is False
    assert findings[0].raw_data["net_class_width_mm"] == pytest.approx(0.15)


def test_trace_narrower_than_its_net_class_is_a_warning(tmp_path: Path) -> None:
    """Contradicting the designer's own declaration is the strongest signal."""
    _project(tmp_path, "b", {"classes": [{"name": "Default", "track_width": 0.6}]})
    board = _board(
        tmp_path,
        "b",
        '  (net 0 "") (net 1 "+12V")\n'
        '  (segment (start 0 0) (end 20 0) (width 0.4) (layer "F.Cu") (net 1))',
    )
    parsed = load_pcb_board(board)

    findings = PowerTraceWidthRule().evaluate(
        parsed, InspectorConfig(min_power_trace_width_mm=0.3)
    )
    assert len(findings) == 1
    assert findings[0].severity is Severity.WARNING
    assert findings[0].raw_data["contradicts_net_class"] is True
    assert "net class" in findings[0].title.lower()
    # 0.4mm clears the 0.3mm floor; only the net class makes this a finding.
    assert findings[0].raw_data["min_width_threshold_mm"] == pytest.approx(0.6)


def test_without_net_classes_the_configured_floor_still_warns(tmp_path: Path) -> None:
    board = _board(
        tmp_path,
        "b",
        '  (net 0 "") (net 1 "+5V")\n'
        '  (segment (start 0 0) (end 20 0) (width 0.15) (layer "F.Cu") (net 1))',
    )
    parsed = load_pcb_board(board)

    findings = PowerTraceWidthRule().evaluate(
        parsed, InspectorConfig(min_power_trace_width_mm=0.3)
    )
    assert len(findings) == 1
    assert findings[0].severity is Severity.WARNING
    assert findings[0].raw_data["net_class_width_mm"] is None


def test_net_class_model_defaults_are_none() -> None:
    """A missing key must not read as zero."""
    nc = NetClass(name="X")
    assert nc.track_width is None
    assert nc.diff_pair_gap is None


# --------------------------------------------------------------------------
# P2-7: pinfunction, the designer's own pin names
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pin_function", "net", "power", "ground"),
    [
        # Declared supply pins, whatever the net ended up being called.
        ("VDD", "Net-(U5-OUT)", True, False),
        ("VDDA", "X", True, False),
        ("AVCC", "X", True, False),
        ("VCCIO", "X", True, False),
        ("VBUS", "X", True, False),
        ("3.3V", "X", True, False),
        # Real placeholder seen on KiCad 10's CM5 demo.
        ("+5v_(Input)", "X", True, False),
        # Declared ground pins.
        ("GND", "X", False, True),
        ("VSS", "X", False, True),
        ("AGND", "X", False, True),
        ("VSSA", "X", False, True),
        # Placeholders carry nothing, so the net name decides.
        ("NC", "SIGNAL_A", False, False),
        ("Pin_4", "SIGNAL_A", False, False),
        ("1", "SIGNAL_A", False, False),
        ("SHIELD", "SIGNAL_A", False, False),
        ("", "+3V3", True, False),
        ("", "GND", False, True),
        ("", "Net-(J1-VCC)", True, False),
        # A signal pin must not become power because of its net name.
        ("SW", "SW_NODE", False, False),
        ("SDA", "I2C_SDA", False, False),
    ],
)
def test_pin_function_classification(
    pin_function: str, net: str, power: bool, ground: bool
) -> None:
    from pcb_inspector.kicad.pcb_model import Pad

    pad = Pad(number="1", net_name=net, pin_function=pin_function)
    assert pad.is_power is power
    assert pad.is_ground is ground


def test_pin_function_is_parsed(tmp_path: Path) -> None:
    board = _board(
        tmp_path,
        "b",
        '  (net 0 "") (net 1 "Net-(U1-Pad1)")\n'
        '  (footprint "F" (layer "F.Cu") (at 0 0 0)\n'
        '    (property "Reference" "U1")\n'
        '    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") '
        '(net 1 "Net-(U1-Pad1)") (pinfunction "VDD"))\n'
        '  )',
    )
    parsed = load_pcb_board(board)
    pad = parsed.footprints["U1"].pads[0]

    assert pad.pin_function == "VDD"
    # The net name alone gives no hint; the pin function does.
    assert pad.is_power is True


def test_decoupling_found_via_pin_function_despite_opaque_net_name(tmp_path: Path) -> None:
    """Auto-generated net names carry no hint, but the pin name does."""
    from pcb_inspector.rules.decoupling import DecouplingProximityRule

    board = _board(
        tmp_path,
        "b",
        '  (net 0 "") (net 1 "Net-(U1-Pad7)")\n'
        '  (footprint "F" (layer "F.Cu") (at 0 0 0)\n'
        '    (property "Reference" "U1")\n'
        '    (pad "7" smd rect (at 0 0) (size 1 1) (layers "F.Cu") '
        '(net 1 "Net-(U1-Pad7)") (pinfunction "VDD"))\n'
        '  )',
    )
    parsed = load_pcb_board(board)

    findings = DecouplingProximityRule().evaluate(parsed, InspectorConfig())
    assert [f.id for f in findings] == ["DEC-MISSING-U1-7"]


def test_declared_sw_pin_confirms_switching_node(tmp_path: Path) -> None:
    """A regulator without a discrete inductor footprint is still a regulator."""
    from pcb_inspector.rules.switching_loops import SwitchingLoopGeometryRule

    pads = "\n".join(
        f'    (pad "{n}" smd rect (at {x} {y}) (size 1 1) (layers "F.Cu") '
        f'(net 1 "SW") (pinfunction "{fn}"))'
        for n, x, y, fn in (("1", 0, 0, "SW"), ("2", 60, 0, "SW"), ("3", 0, 60, "SW"))
    )
    board = _board(
        tmp_path,
        "b",
        '  (net 0 "") (net 1 "SW")\n'
        f'  (footprint "F" (layer "F.Cu") (at 0 0 0)\n'
        '    (property "Reference" "U1")\n'
        f'{pads}\n'
        '  )',
    )
    parsed = load_pcb_board(board)

    findings = SwitchingLoopGeometryRule().evaluate(parsed, InspectorConfig())
    assert len(findings) == 1
    assert findings[0].rule_id == "HEUR-DCDC-001"
