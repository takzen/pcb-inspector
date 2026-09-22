"""Smoke tests over the demo boards that ship with KiCad.

The project's own golden samples are synthetic and minimal. Auditing KiCad's
demos found three parser defects none of them could reveal, because none
contained an arc, a multi-layer zone, or a rotated footprint:

* curved tracks written as (arc ...) were ignored, producing nine false
  CRITICAL pair-skew findings on CM5_MINIMA_3;
* zones declared with plural (layers ...) hid that board's inner ground planes;
* pad coordinates were rotated the wrong way, by up to 22.9mm.

It also found a 144s ground-reference check on the 26,650-track vme-wren board.

These tests pin those fixes to real files. They are skipped when KiCad is not
installed. The quick set runs by default; the full sweep over every demo is
opt-in, via PCB_INSPECTOR_FULL_DEMOS=1, because two of the boards are 60-70 MB.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.core.layers import HEURISTIC_CATEGORIES
from pcb_inspector.kicad.pcb_model import PcbBoard, load_pcb_board
from pcb_inspector.rules import board_loader
from pcb_inspector.rules.differential_pairs import DifferentialPairSkewRule
from pcb_inspector.rules.registry import init_default_registry
from pcb_inspector.rules.return_paths import GroundPlaneIntegrityRule


def _find_demos() -> Path | None:
    override = os.environ.get("PCB_INSPECTOR_KICAD_DEMOS")
    candidates = [Path(override)] if override else []
    if sys.platform == "win32":
        for version in ("10.0", "9.0", "8.0"):
            candidates.append(Path(rf"C:\Program Files\KiCad\{version}\share\kicad\demos"))
    elif sys.platform == "darwin":
        candidates.append(
            Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/demos")
        )
    else:
        candidates += [Path("/usr/share/kicad/demos"), Path("/usr/local/share/kicad/demos")]
    return next((c for c in candidates if c.is_dir()), None)


DEMOS = _find_demos()

pytestmark = pytest.mark.skipif(DEMOS is None, reason="KiCad demo boards not installed")

#: Small enough to run on every test invocation, and between them covering
#: arcs, multi-layer zones, net classes, rotated and flipped footprints.
QUICK_BOARDS = [
    "cm5_minima/CM5_MINIMA_3.kicad_pcb",
    "stickhub/StickHub.kicad_pcb",
    "royalblue54L_feather/RoyalBlue54L-Feather.kicad_pcb",
    "interf_u/interf_u.kicad_pcb",
    "pic_programmer/pic_programmer.kicad_pcb",
]


def _demo(rel: str) -> Path:
    assert DEMOS is not None
    path = DEMOS / rel
    if not path.is_file():
        pytest.skip(f"{rel} not present in this KiCad install")
    return path


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    board_loader.clear_cache()


def _audit(path: Path) -> list:  # type: ignore[type-arg]
    return init_default_registry().evaluate_filtered(
        context=path, config=InspectorConfig(), categories=HEURISTIC_CATEGORIES
    )


@pytest.mark.parametrize("rel", QUICK_BOARDS)
def test_demo_board_parses_and_audits_cleanly(rel: str) -> None:
    """No parser exception, and no rule that failed to evaluate."""
    path = _demo(rel)
    board = load_pcb_board(path)
    assert board.footprints

    failed = [f.id for f in _audit(path) if f.id.startswith("RULE-EXEC-FAILED")]
    assert failed == []


# --------------------------------------------------------------------------
# CM5_MINIMA_3: the board that exposed the parser defects
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def cm5() -> PcbBoard:
    return load_pcb_board(_demo("cm5_minima/CM5_MINIMA_3.kicad_pcb"))


def test_cm5_arcs_are_counted(cm5: PcbBoard) -> None:
    arcs = [t for t in cm5.tracks if t.is_arc]
    assert len(arcs) == 200
    assert len(cm5.tracks) == 2084


def test_cm5_matched_hdmi_pairs_are_not_reported_skewed(cm5: PcbBoard) -> None:
    """Their length tuning is drawn with arcs; ignoring arcs flagged all four."""
    findings = DifferentialPairSkewRule().evaluate(cm5, InspectorConfig())
    flagged = {f.raw_data["base_name"] for f in findings}
    assert not any("HDMI" in base for base in flagged), flagged
    assert not any("PCIE_PI.CLK" in base for base in flagged), flagged


def test_cm5_inner_planes_from_multi_layer_zones_are_present(cm5: PcbBoard) -> None:
    layers = {z.layer for z in cm5.zones}
    assert {"In1.Cu", "In4.Cu"} <= layers


def test_cm5_stack_order_is_physical(cm5: PcbBoard) -> None:
    assert cm5.copper_layers == ["F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu", "B.Cu"]


def test_cm5_ground_reference_is_nearly_complete(cm5: PcbBoard) -> None:
    """138 segments read as unreferenced while the inner planes were hidden."""
    findings = GroundPlaneIntegrityRule().evaluate(cm5, InspectorConfig())
    unreferenced = findings[0].raw_data["unreferenced_count"] if findings else 0
    assert unreferenced <= 1


def test_cm5_net_classes_resolve_from_the_project_file(cm5: PcbBoard) -> None:
    hdmi = cm5.net_class_for("/CM5/HDMI_PI.CK_P")
    assert hdmi is not None and hdmi.name == "100ohm"
    default = cm5.net_class_for("+3V3_PI")
    assert default is not None and default.track_width == pytest.approx(0.15)


@pytest.mark.parametrize(
    ("pad_index", "expected"),
    [
        # From pcbnew's PAD::GetPosition on KiCad 10.0.6. J1 sits at rotation
        # -90, where the counter-clockwise transform erred by up to 22.9mm.
        (0, (94.046, 32.65)),
        (1, (94.046, 39.65)),
        (2, (98.212, 40.74)),
    ],
)
def test_cm5_rotated_pads_match_pcbnew(
    cm5: PcbBoard, pad_index: int, expected: tuple[float, float]
) -> None:
    pad = cm5.footprints["J1"].pads[pad_index]
    assert (pad.at_x, pad.at_y) == pytest.approx(expected, abs=1e-3)


# --------------------------------------------------------------------------
# Performance on the largest demo
# --------------------------------------------------------------------------


def test_ground_rule_is_fast_on_a_dense_board() -> None:
    """The ground check took 144s here; every other rule under 1s."""
    path = _demo("vme-wren/vme-wren.kicad_pcb")
    if not os.environ.get("PCB_INSPECTOR_FULL_DEMOS"):
        pytest.skip("set PCB_INSPECTOR_FULL_DEMOS=1 to parse the 72 MB vme-wren board")
    board = load_pcb_board(path)

    start = time.perf_counter()
    GroundPlaneIntegrityRule().evaluate(board, InspectorConfig())
    assert time.perf_counter() - start < 15.0


# --------------------------------------------------------------------------
# Full sweep, opt-in
# --------------------------------------------------------------------------


def _all_demo_boards() -> list[str]:
    if DEMOS is None:
        return []
    return sorted(str(p.relative_to(DEMOS)).replace("\\", "/") for p in DEMOS.rglob("*.kicad_pcb"))


@pytest.mark.skipif(
    not os.environ.get("PCB_INSPECTOR_FULL_DEMOS"),
    reason="set PCB_INSPECTOR_FULL_DEMOS=1 to audit every KiCad demo board",
)
@pytest.mark.parametrize("rel", _all_demo_boards())
def test_every_demo_board_audits_cleanly(rel: str) -> None:
    path = _demo(rel)
    failed = [f.id for f in _audit(path) if f.id.startswith("RULE-EXEC-FAILED")]
    assert failed == []
