"""Regression tests for heuristic correctness.

These pin down the false positives and silently dropped findings that the
Layer-2 rules produced before the accuracy pass: connectors classified as ICs
and capacitors, SWD/PHY nets flagged as regulator switching nodes, USB D+/D-
pairs never checked, and repair actions attached to the wrong rule.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pcb_inspector.core.aggregator import FindingAggregator
from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.core.models import Finding, FindingCategory, Severity
from pcb_inspector.kicad.pcb_model import (
    Footprint,
    Pad,
    PcbBoard,
    TrackSegment,
    Via,
    Zone,
    net_tokens,
)
from pcb_inspector.rules.decoupling import DecouplingProximityRule
from pcb_inspector.rules.differential_pairs import (
    DifferentialPairSkewRule,
    split_diff_pair_suffix,
)
from pcb_inspector.rules.registry import RuleRegistry
from pcb_inspector.rules.return_paths import GroundPlaneIntegrityRule
from pcb_inspector.rules.switching_loops import SwitchingLoopGeometryRule
from pcb_inspector.rules.trace_width import PowerTraceWidthRule

CLEAN_BOARD = Path(__file__).parent / "golden_samples" / "clean_board" / "clean_board.kicad_pcb"


def _pad(net: str, x: float = 0.0, y: float = 0.0, num: str = "1") -> Pad:
    return Pad(number=num, at_x=x, at_y=y, net_name=net, layers=["F.Cu"])


# --------------------------------------------------------------------------
# P1-1: reference designator classification
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("refdes", "ic", "cap", "ind", "diode"),
    [
        ("U1", True, False, False, False),
        ("IC3", True, False, False, False),
        ("U12B", True, False, False, False),
        ("C5", False, True, False, False),
        ("L1", False, False, True, False),
        ("D3", False, False, False, True),
        # Connectors and LEDs must not be mistaken for any of the above.
        ("USB1", False, False, False, False),
        ("UART1", False, False, False, False),
        ("CN1", False, False, False, False),
        ("CONN2", False, False, False, False),
        ("LED1", False, False, False, False),
        ("DNP1", False, False, False, False),
    ],
)
def test_refdes_classification(
    refdes: str, ic: bool, cap: bool, ind: bool, diode: bool
) -> None:
    fp = Footprint(refdes=refdes)
    assert fp.is_ic is ic
    assert fp.is_capacitor is cap
    assert fp.is_inductor is ind
    assert fp.is_diode is diode


def test_connector_is_not_audited_for_decoupling() -> None:
    """USB1 used to be treated as an IC and demanded a bypass capacitor."""
    usb = Footprint(refdes="USB1", at_x=0, at_y=0, pads=[_pad("+5V")])
    board = PcbBoard(file_path="x", nets={1: "+5V"}, footprints={"USB1": usb})

    assert DecouplingProximityRule().evaluate(board, InspectorConfig()) == []


def test_connector_does_not_mask_a_missing_capacitor() -> None:
    """CN1 used to count as a capacitor, hiding a genuinely unbypassed rail."""
    ic = Footprint(refdes="U1", at_x=0, at_y=0, pads=[_pad("+3V3")])
    conn = Footprint(refdes="CN1", at_x=1, at_y=0, pads=[_pad("+3V3", x=1)])
    board = PcbBoard(file_path="x", nets={1: "+3V3"}, footprints={"U1": ic, "CN1": conn})

    findings = DecouplingProximityRule().evaluate(board, InspectorConfig())
    assert [f.id for f in findings] == ["DEC-MISSING-U1-1"]
    assert findings[0].severity is Severity.CRITICAL


# --------------------------------------------------------------------------
# P1-2: net-name case handling
# --------------------------------------------------------------------------


def test_decoupling_detects_violation_with_mixed_case_netnames() -> None:
    """A capacitor on '+3v3' against a pin on '+3V3' must still be measured."""
    ic = Footprint(refdes="U1", at_x=0, at_y=0, pads=[_pad("+3V3")])
    cap = Footprint(refdes="C1", at_x=50, at_y=50, pads=[_pad("+3v3", x=50, y=50)])
    board = PcbBoard(file_path="x", nets={1: "+3V3"}, footprints={"U1": ic, "C1": cap})

    findings = DecouplingProximityRule().evaluate(board, InspectorConfig())
    assert len(findings) == 1
    assert findings[0].severity is Severity.CRITICAL
    assert findings[0].id.startswith("DEC-DIST-")


# --------------------------------------------------------------------------
# P1-3: switching-node detection
# --------------------------------------------------------------------------


@pytest.mark.parametrize("net", ["SWCLK", "SWDIO", "PHY_TXD", "GRAPH_EN", "PWR_SWITCH_EN"])
def test_debug_and_phy_nets_are_not_switching_nodes(net: str) -> None:
    """Substring matching flagged every SWD and PHY net as a regulator node."""
    ic = Footprint(refdes="U1", at_x=0, at_y=0, pads=[_pad(net)])
    far = Footprint(refdes="U2", at_x=60, at_y=60, pads=[_pad(net, x=60, y=60)])
    third = Footprint(refdes="U3", at_x=0, at_y=60, pads=[_pad(net, x=0, y=60)])
    board = PcbBoard(
        file_path="x",
        nets={1: net},
        footprints={"U1": ic, "U2": far, "U3": third},
    )

    assert SwitchingLoopGeometryRule().evaluate(board, InspectorConfig()) == []


def test_named_switch_net_without_inductor_is_ignored() -> None:
    """Topology, not the name alone, confirms a converter switching node."""
    ic = Footprint(refdes="U1", at_x=0, at_y=0, pads=[_pad("SW")])
    res = Footprint(refdes="R1", at_x=60, at_y=60, pads=[_pad("SW", x=60, y=60)])
    res2 = Footprint(refdes="R2", at_x=0, at_y=60, pads=[_pad("SW", x=0, y=60)])
    board = PcbBoard(
        file_path="x", nets={1: "SW"}, footprints={"U1": ic, "R1": res, "R2": res2}
    )

    assert SwitchingLoopGeometryRule().evaluate(board, InspectorConfig()) == []


def test_real_switching_node_is_still_detected() -> None:
    ic = Footprint(refdes="U1", at_x=0, at_y=0, pads=[_pad("SW")])
    ind = Footprint(refdes="L1", at_x=60, at_y=60, pads=[_pad("SW", x=60, y=60)])
    diode = Footprint(refdes="D1", at_x=0, at_y=60, pads=[_pad("SW", x=0, y=60)])
    board = PcbBoard(
        file_path="x", nets={1: "SW"}, footprints={"U1": ic, "L1": ind, "D1": diode}
    )

    findings = SwitchingLoopGeometryRule().evaluate(board, InspectorConfig())
    assert len(findings) == 1
    assert findings[0].rule_id == "HEUR-DCDC-001"


def test_net_tokens_splits_on_delimiters() -> None:
    assert net_tokens("PWR_SW") == {"PWR", "SW"}
    assert net_tokens("+3V3") == {"3V3"}
    assert net_tokens("/power/VBUS") == {"POWER", "VBUS"}


# --------------------------------------------------------------------------
# P1-8: differential pair naming
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("net", "expected"),
    [
        ("USB_DP", ("USB", "P")),
        ("USB_DM", ("USB", "N")),
        ("CANH", ("CAN", "P")),
        ("CANL", ("CAN", "N")),
        ("D+", ("D", "P")),
        ("D-", ("D", "N")),
        ("CLKP", ("CLK", "P")),
        ("ETH_TX_P", ("ETH_TX", "P")),
        # Not differential halves.
        ("VIN", None),
        ("GND", None),
        ("+3V3", None),
        ("EN", None),
    ],
)
def test_diff_pair_suffix_split(net: str, expected: tuple[str, str] | None) -> None:
    assert split_diff_pair_suffix(net) == expected


def test_usb_dp_dm_pair_skew_is_detected() -> None:
    """The most common differential pair in consumer hardware was never checked."""
    board = PcbBoard(
        file_path="x",
        nets={1: "USB_DP", 2: "USB_DM"},
        tracks=[
            TrackSegment(
                start_x=0, start_y=0, end_x=30, end_y=0, width=0.25,
                layer="F.Cu", net_num=1, net_name="USB_DP",
            ),
            TrackSegment(
                start_x=0, start_y=1, end_x=20, end_y=1, width=0.25,
                layer="F.Cu", net_num=2, net_name="USB_DM",
            ),
        ],
    )

    findings = DifferentialPairSkewRule().evaluate(board, InspectorConfig())
    assert len(findings) == 1
    assert findings[0].raw_data["skew_mm"] == pytest.approx(10.0)


def test_diff_pair_length_includes_via_transitions() -> None:
    """A layer change adds real length; ignoring it understates skew."""
    board = PcbBoard(
        file_path="x",
        thickness=1.6,
        nets={1: "USB_P", 2: "USB_N"},
        tracks=[
            TrackSegment(
                start_x=0, start_y=0, end_x=20, end_y=0, width=0.25,
                layer="F.Cu", net_num=1, net_name="USB_P",
            ),
            TrackSegment(
                start_x=0, start_y=1, end_x=20, end_y=1, width=0.25,
                layer="F.Cu", net_num=2, net_name="USB_N",
            ),
        ],
        vias=[
            Via(x=10, y=0, size=0.8, drill=0.4, layers=("F.Cu", "B.Cu"),
                net_num=1, net_name="USB_P"),
        ],
    )

    findings = DifferentialPairSkewRule().evaluate(board, InspectorConfig())
    assert len(findings) == 1
    assert findings[0].raw_data["skew_mm"] == pytest.approx(1.6)


# --------------------------------------------------------------------------
# P1-6: one finding per net, not per segment
# --------------------------------------------------------------------------


def test_undersized_net_yields_single_grouped_finding() -> None:
    """A trace bent 40 times is one defect, not 40 warnings."""
    tracks = [
        TrackSegment(
            start_x=float(i), start_y=0, end_x=float(i + 1), end_y=0, width=0.2,
            layer="F.Cu", net_num=1, net_name="+5V",
        )
        for i in range(40)
    ]
    board = PcbBoard(file_path="x", nets={1: "+5V"}, tracks=tracks)

    findings = PowerTraceWidthRule().evaluate(board, InspectorConfig())
    assert len(findings) == 1
    assert findings[0].raw_data["segment_count"] == 40
    assert findings[0].raw_data["measured_width_mm"] == pytest.approx(0.2)


def test_trace_width_groups_per_layer() -> None:
    board = PcbBoard(
        file_path="x",
        nets={1: "+5V"},
        tracks=[
            TrackSegment(start_x=0, start_y=0, end_x=1, end_y=0, width=0.2,
                         layer="F.Cu", net_num=1, net_name="+5V"),
            TrackSegment(start_x=0, start_y=0, end_x=1, end_y=0, width=0.2,
                         layer="B.Cu", net_num=1, net_name="+5V"),
        ],
    )
    findings = PowerTraceWidthRule().evaluate(board, InspectorConfig())
    assert {f.raw_data["layer"] for f in findings} == {"F.Cu", "B.Cu"}


# --------------------------------------------------------------------------
# P1-9: ground reference
# --------------------------------------------------------------------------


def _board_with_plane(zone_layer: str, track_layer: str) -> PcbBoard:
    return PcbBoard(
        file_path="x",
        nets={1: "GND", 2: "SIG"},
        tracks=[
            TrackSegment(start_x=10, start_y=10, end_x=40, end_y=10, width=0.25,
                         layer=track_layer, net_num=2, net_name="SIG"),
        ],
        zones=[
            Zone(net_num=1, net_name="GND", layer=zone_layer,
                 points=[(0, 0), (100, 0), (100, 100), (0, 100)]),
        ],
    )


def test_plane_on_adjacent_layer_references_the_track() -> None:
    assert GroundPlaneIntegrityRule().evaluate(
        _board_with_plane("B.Cu", "F.Cu"), InspectorConfig()
    ) == []


def test_plane_on_the_same_layer_is_not_a_reference() -> None:
    """Coplanar ground copper is not a return plane beneath the trace."""
    findings = GroundPlaneIntegrityRule().evaluate(
        _board_with_plane("F.Cu", "F.Cu"), InspectorConfig()
    )
    assert len(findings) == 1
    assert findings[0].id.startswith("GND-UNREFERENCED-TRACKS")


def test_track_leaving_the_plane_is_caught_despite_centred_midpoint() -> None:
    """Midpoint-only testing passed traces that ran off the plane at both ends."""
    board = PcbBoard(
        file_path="x",
        nets={1: "GND", 2: "SIG"},
        tracks=[
            TrackSegment(start_x=-20, start_y=50, end_x=120, end_y=50, width=0.25,
                         layer="F.Cu", net_num=2, net_name="SIG"),
        ],
        zones=[
            Zone(net_num=1, net_name="GND", layer="B.Cu",
                 points=[(0, 0), (100, 0), (100, 100), (0, 100)]),
        ],
    )
    findings = GroundPlaneIntegrityRule().evaluate(board, InspectorConfig())
    assert len(findings) == 1


def test_filled_polygon_is_preferred_over_outline() -> None:
    """Real copper is the filled polygon; the outline may be far larger."""
    board = PcbBoard(
        file_path="x",
        nets={1: "GND", 2: "SIG"},
        tracks=[
            TrackSegment(start_x=60, start_y=60, end_x=90, end_y=60, width=0.25,
                         layer="F.Cu", net_num=2, net_name="SIG"),
        ],
        zones=[
            Zone(
                net_num=1, net_name="GND", layer="B.Cu",
                points=[(0, 0), (100, 0), (100, 100), (0, 100)],
                filled_polygons=[[(0, 0), (10, 0), (10, 10), (0, 10)]],
            ),
        ],
    )
    findings = GroundPlaneIntegrityRule().evaluate(board, InspectorConfig())
    assert len(findings) == 1, "track outside the poured copper must be flagged"


def test_self_intersecting_plane_is_repaired_not_discarded() -> None:
    """An invalid pour used to be dropped, inventing unreferenced findings.

    This bow-tie repairs into a left and a right triangle meeting at (50, 50);
    the track sits well inside the left one.
    """
    bowtie = [(0, 0), (100, 100), (100, 0), (0, 100)]
    board = PcbBoard(
        file_path="x",
        nets={1: "GND", 2: "SIG"},
        tracks=[
            TrackSegment(start_x=5, start_y=40, end_x=25, end_y=40, width=0.25,
                         layer="F.Cu", net_num=2, net_name="SIG"),
        ],
        zones=[Zone(net_num=1, net_name="GND", layer="B.Cu", points=bowtie)],
    )
    assert GroundPlaneIntegrityRule().evaluate(board, InspectorConfig()) == []


# --------------------------------------------------------------------------
# P1-4 / P1-5: actionable fixes
# --------------------------------------------------------------------------


def _finding(rule_id: str, category: FindingCategory, **kwargs: object) -> Finding:
    payload: dict[str, object] = {
        "id": f"{rule_id}-X",
        "title": "t",
        "severity": Severity.WARNING,
        "category": category,
        "description": "d",
        "rule_id": rule_id,
    }
    payload.update(kwargs)
    return Finding(**payload)  # type: ignore[arg-type]


def test_ground_plane_finding_gets_ground_plane_fix() -> None:
    """Was handed TUNE_DIFF_PAIR_SKEW because both rules are SIGNAL_INTEGRITY."""
    f = _finding("HEUR-GND-001", FindingCategory.SIGNAL_INTEGRITY, nets=["GND"])
    out = FindingAggregator().aggregate([f])
    assert out[0].actionable_fix is not None
    assert out[0].actionable_fix.action_type == "EXPAND_GROUND_PLANE"


def test_switching_loop_finding_gets_compact_loop_fix() -> None:
    """Was handed WIDEN_TRACE because both rules are POWER_DELIVERY."""
    f = _finding("HEUR-DCDC-001", FindingCategory.POWER_DELIVERY, nets=["SW"])
    out = FindingAggregator().aggregate([f])
    assert out[0].actionable_fix is not None
    assert out[0].actionable_fix.action_type == "COMPACT_SWITCHING_LOOP"


def test_missing_capacitor_gets_add_not_relocate() -> None:
    f = _finding("HEUR-DEC-001", FindingCategory.DECOUPLING, components=["U1"])
    out = FindingAggregator().aggregate([f])
    assert out[0].actionable_fix is not None
    assert out[0].actionable_fix.action_type == "ADD_DECOUPLING_CAPACITOR"


def test_actionable_fix_threshold_matches_the_rule_that_ran() -> None:
    """The fix quoted 0.50mm while the rule measured against 0.30mm."""
    cfg = InspectorConfig(min_power_trace_width_mm=0.8)
    f = _finding(
        "HEUR-PWR-001",
        FindingCategory.POWER_DELIVERY,
        nets=["+5V"],
        raw_data={"min_width_threshold_mm": 0.8},
    )
    out = FindingAggregator(config=cfg).aggregate([f])
    assert out[0].actionable_fix is not None
    assert out[0].actionable_fix.parameters["recommended_min_width_mm"] == pytest.approx(0.8)


def test_switching_loop_fix_quotes_the_rule_threshold() -> None:
    f = _finding(
        "HEUR-DCDC-001",
        FindingCategory.POWER_DELIVERY,
        nets=["SW"],
        raw_data={"max_recommended_area_mm2": 20.0},
    )
    out = FindingAggregator().aggregate([f])
    assert out[0].actionable_fix is not None
    assert out[0].actionable_fix.parameters["max_loop_area_mm2"] == pytest.approx(20.0)


# --------------------------------------------------------------------------
# P1-7: custom_rules and enable_heuristics actually take effect
# --------------------------------------------------------------------------


def test_custom_rules_can_disable_a_rule() -> None:
    reg = RuleRegistry()
    reg.register(GroundPlaneIntegrityRule())
    cfg = InspectorConfig(custom_rules={"HEUR-GND-001": {"enabled": False}})

    assert reg.evaluate_all(context=CLEAN_BOARD, config=cfg) == []


def test_enable_heuristics_false_skips_layer_2() -> None:
    reg = RuleRegistry()
    reg.register(PowerTraceWidthRule())
    reg.register(DecouplingProximityRule())

    assert reg.evaluate_all(
        context=CLEAN_BOARD, config=InspectorConfig(enable_heuristics=False)
    ) == []


def test_custom_rules_override_a_threshold() -> None:
    """The example config documents HEUR-DCDC-001.max_loop_area_mm2."""
    ic = Footprint(refdes="U1", at_x=0, at_y=0, pads=[_pad("SW")])
    ind = Footprint(refdes="L1", at_x=5, at_y=5, pads=[_pad("SW", x=5, y=5)])
    diode = Footprint(refdes="D1", at_x=0, at_y=5, pads=[_pad("SW", x=0, y=5)])
    board = PcbBoard(
        file_path="x", nets={1: "SW"}, footprints={"U1": ic, "L1": ind, "D1": diode}
    )

    # Hull area is 12.5 mm2: under the 30 mm2 default, over a 5 mm2 override.
    assert SwitchingLoopGeometryRule().evaluate(board, InspectorConfig()) == []

    cfg = InspectorConfig(custom_rules={"HEUR-DCDC-001": {"max_loop_area_mm2": 5.0}})
    findings = SwitchingLoopGeometryRule().evaluate(board, cfg)
    assert len(findings) == 1
    assert findings[0].raw_data["max_recommended_area_mm2"] == pytest.approx(5.0)


def test_invalid_custom_rule_value_falls_back_to_default() -> None:
    cfg = InspectorConfig(custom_rules={"HEUR-DCDC-001": {"max_loop_area_mm2": "not a number"}})
    rule = SwitchingLoopGeometryRule()
    assert rule.param(cfg, "max_loop_area_mm2", 30.0) == pytest.approx(30.0)


# --------------------------------------------------------------------------
# P2-3: correlation scales without changing its results
# --------------------------------------------------------------------------


def test_correlation_links_shared_component_across_categories() -> None:
    """Criterion A: same part, different engineering category."""
    a = _finding("HEUR-DEC-001", FindingCategory.DECOUPLING, id="A", components=["U1"])
    b = _finding("KICAD-DRC-ERC-001", FindingCategory.DRC_ERC, id="B", components=["U1"])
    out = {f.id: f.correlated_with for f in FindingAggregator().aggregate([a, b])}
    assert out["A"] == ["B"]
    assert out["B"] == ["A"]


def test_correlation_links_nearby_coordinates() -> None:
    """Criterion B: no shared part, but physically coincident."""
    from pcb_inspector.core.models import Coordinate

    a = _finding("R1", FindingCategory.VISION, id="A",
                 coordinates=[Coordinate(x=50.0, y=50.0)])
    b = _finding("R2", FindingCategory.VISION, id="B",
                 coordinates=[Coordinate(x=51.0, y=50.5)])
    out = {f.id: f.correlated_with for f in FindingAggregator(correlation_radius_mm=2.5).aggregate([a, b])}
    assert out["A"] == ["B"]


def test_correlation_respects_the_radius() -> None:
    from pcb_inspector.core.models import Coordinate

    a = _finding("R1", FindingCategory.VISION, id="A",
                 coordinates=[Coordinate(x=0.0, y=0.0)])
    b = _finding("R2", FindingCategory.VISION, id="B",
                 coordinates=[Coordinate(x=40.0, y=0.0)])
    out = FindingAggregator(correlation_radius_mm=2.5).aggregate([a, b])
    assert all(f.correlated_with == [] for f in out)


def test_correlation_stays_linear_enough_to_finish() -> None:
    """A dense board reaches thousands of findings; the old scan took ~19s at 4000."""
    import time

    from pcb_inspector.core.models import Coordinate

    findings = [
        _finding(
            f"RULE-{i % 7}",
            list(FindingCategory)[i % len(FindingCategory)],
            id=f"F-{i:05d}",
            components=[f"U{i % 300}"],
            nets=[f"NET{i % 400}"],
            coordinates=[Coordinate(x=float(i % 200), y=float((i * 7) % 200))],
        )
        for i in range(4000)
    ]
    start = time.perf_counter()
    FindingAggregator().aggregate(findings)
    assert time.perf_counter() - start < 5.0



# --------------------------------------------------------------------------
# Found on a real KiCad 10 board: an analog metal-detector probe with
# ADA4841-2 op-amps, a BIAS_1.65V mid-rail net and a +3.3V_PROBE_AN rail
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("net", "supply"),
    [
        ("+3.3V_PROBE_AN", True),
        ("+3.3V", True),
        ("+3V3", True),
        ("P3V3", True),
        ("USB_5V", True),
        ("+12V", True),
        ("-5V", True),
        ("VDD_CORE", True),
        ("AVCC", True),
        ("/VCC_PROBE", True),
        ("Net-(J1-VCC)", True),
        # "5V" used to match inside "1.65V".
        ("BIAS_1.65V", False),
        ("VREF_2V5", False),
        ("VDD_SENSE", False),
        # "VIN" used to match inside "DRIVING".
        ("LED_DRIVING", False),
        ("Net-(U5-OUT)", False),
        ("Net-(FB1-Pad1)", False),
        ("SWCLK", False),
        # Net names from KiCad's own demos, where the first token-based
        # version of this check got them wrong.
        ("+5VUSB", True),
        ("+5VBAT", True),
        ("/Battery_holder/VBAT+", True),
        ("/Debugger/USB.VBUS", True),
        ("/xilinx/+3,3V_OUT", True),
        ("VSYS", True),
        ("M2_3V3", True),
        ("/12Vext", True),
        ("/ampli_ht_vertical/Vpil_0_3,3V", False),
        ("unconnected-(BUS1--12V-Pad7)", False),
        ("/VCC_SENSE-ERROR*", False),
        ("Net-(U1-VBUS_SENSE)", False),
    ],
)
def test_supply_net_classification(net: str, supply: bool) -> None:
    from pcb_inspector.kicad.pcb_model import is_supply_net

    assert is_supply_net(net) is supply


def _opamp(refdes: str, x: float, pins: dict[str, tuple[str, str]]) -> Footprint:
    """An SO-8 op-amp whose pads carry (pin function, net)."""
    return Footprint(
        refdes=refdes,
        at_x=x,
        at_y=0,
        pads=[
            Pad(number=num, at_x=x, at_y=0, net_name=net, pin_function=fn, layers=["F.Cu"])
            for num, (fn, net) in pins.items()
        ],
    )


def test_opamp_bias_pins_are_not_supply_pins_but_its_supply_pin_is() -> None:
    """The bias buffer's output and inputs were flagged CRITICAL for missing
    decoupling, while the real supply pin on +3.3V_PROBE_AN was never checked."""
    u2 = _opamp(
        "U2",
        0,
        {
            "1": ("OUT_1", "BIAS_1.65V"),
            "2": ("-IN_1", "BIAS_1.65V"),
            "4": ("-VS", "GND_PROBE"),
            "5": ("+_IN_2", "BIAS_1.65V"),
            "8": ("+_VS", "+3.3V_PROBE_AN"),
        },
    )
    far_cap = Footprint(
        refdes="C4",
        at_x=10,
        at_y=0,
        pads=[_pad("+3.3V_PROBE_AN", x=10), _pad("GND_PROBE", x=11, num="2")],
    )
    board = PcbBoard(
        file_path="x",
        nets={1: "BIAS_1.65V", 2: "GND_PROBE", 3: "+3.3V_PROBE_AN"},
        footprints={"U2": u2, "C4": far_cap},
    )

    findings = DecouplingProximityRule().evaluate(board, InspectorConfig())
    assert [f.id for f in findings] == ["DEC-DIST-U2-C4-8"]
    assert findings[0].severity is Severity.WARNING


def test_power_trace_width_recognises_rails_written_with_a_decimal_point() -> None:
    track = TrackSegment(
        start_x=0, start_y=0, end_x=10, end_y=0, width=0.1,
        layer="F.Cu", net_num=1, net_name="+3.3V_PROBE",
    )
    board = PcbBoard(file_path="x", nets={1: "+3.3V_PROBE"}, tracks=[track])

    findings = PowerTraceWidthRule().evaluate(board, InspectorConfig())
    assert [f.nets for f in findings] == [["+3.3V_PROBE"]]


def _pair_board(p: str, n: str, len_p: float, len_n: float) -> PcbBoard:
    return PcbBoard(
        file_path="x",
        nets={1: p, 2: n},
        tracks=[
            TrackSegment(
                start_x=0, start_y=0, end_x=len_p, end_y=0, width=0.25,
                layer="F.Cu", net_num=1, net_name=p,
            ),
            TrackSegment(
                start_x=0, start_y=1, end_x=len_n, end_y=1, width=0.25,
                layer="F.Cu", net_num=2, net_name=n,
            ),
        ],
    )


@pytest.mark.parametrize(
    ("p", "n", "severity"),
    [
        # A coil signal read at kHz: 7 mm is picoseconds there.
        ("RX_S_P", "RX_S_N", Severity.SUGGESTION),
        ("CAN_H", "CAN_L", Severity.SUGGESTION),
        ("ADC_INP", "ADC_INN", Severity.SUGGESTION),
        ("USB_DP", "USB_DM", Severity.CRITICAL),
        ("D+", "D-", Severity.CRITICAL),
        # Hierarchical and hub-port spellings from KiCad's demos.
        ("/Debugger/D+", "/Debugger/D-", Severity.CRITICAL),
        ("/U2D+", "/U2D-", Severity.CRITICAL),
        ("/HDMI_D0_P", "/HDMI_D0_N", Severity.CRITICAL),
        ("PCIE_TX0_P", "PCIE_TX0_N", Severity.CRITICAL),
        ("LANE0_TXP", "LANE0_TXN", Severity.CRITICAL),
        ("REFCLK_P", "REFCLK_N", Severity.CRITICAL),
    ],
)
def test_skew_severity_follows_what_the_pair_is(p: str, n: str, severity: Severity) -> None:
    findings = DifferentialPairSkewRule().evaluate(_pair_board(p, n, 45.3, 52.4), InspectorConfig())
    assert len(findings) == 1
    assert findings[0].severity is severity
