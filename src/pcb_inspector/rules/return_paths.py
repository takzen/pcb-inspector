"""Heuristic rule verifying ground plane continuity and reference return paths."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shapely.geometry import Point, Polygon

from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.core.models import Coordinate, Finding, FindingCategory, Severity
from pcb_inspector.kicad.pcb_model import PcbBoard, load_pcb_board
from pcb_inspector.rules.base import BaseRule


class GroundPlaneIntegrityRule(BaseRule):
    """Verifies that high-speed and critical signal tracks maintain a continuous ground return plane."""

    rule_id = "HEUR-GND-001"
    name = "Ground Return Plane Continuity"
    category = FindingCategory.SIGNAL_INTEGRITY
    default_severity = Severity.WARNING
    description = (
        "Ensures signal tracks travel over unbroken ground copper planes to prevent return current "
        "discontinuities, large return loop inductances, and radiated EMI."
    )

    def evaluate(self, context: Any, config: InspectorConfig) -> list[Finding]:
        findings: list[Finding] = []

        board: PcbBoard
        if isinstance(context, PcbBoard):
            board = context
        elif isinstance(context, (str, Path)):
            p = Path(context)
            if p.suffix != ".kicad_pcb":
                pcb_candidate = p.with_suffix(".kicad_pcb") if p.is_file() else (p / f"{p.stem}.kicad_pcb")
                if not pcb_candidate.exists():
                    return findings
                p = pcb_candidate
            try:
                board = load_pcb_board(p)
            except Exception:
                return findings
        else:
            return findings

        # Check for presence of ground zones
        gnd_zones = [z for z in board.zones if "GND" in z.net_name.upper() and len(z.points) >= 3]

        if not gnd_zones and len(board.tracks) > 5:
            # Entire board is routed without any ground pour/plane!
            findings.append(
                Finding(
                    id="GND-NO-COPPER-PLANE",
                    title="No ground plane / copper pour detected on board",
                    severity=Severity.WARNING,
                    category=FindingCategory.SIGNAL_INTEGRITY,
                    description=(
                        f"The PCB layout contains {len(board.tracks)} tracks but has no filled ground "
                        f"copper zones (GND plane) on any layer."
                    ),
                    rule_id=self.rule_id,
                    nets=["GND"],
                    coordinates=[],
                    rationale=(
                        "Without a continuous ground reference plane, high-frequency return currents "
                        "must travel through meandering ground traces, drastically increasing loop inductance."
                    ),
                    recommendation=(
                        "Add a filled copper zone on at least one layer (preferably B.Cu or internal plane) "
                        "assigned to net 'GND'."
                    ),
                )
            )
            return findings

        # If ground plane exists, build polygon geometries
        gnd_polygons = []
        for gz in gnd_zones:
            try:
                poly = Polygon(gz.points)
                if poly.is_valid and not poly.is_empty:
                    gnd_polygons.append(poly)
            except Exception:
                continue

        if not gnd_polygons:
            return findings

        # Check for tracks completely outside any ground plane
        unreferenced_count = 0
        sample_coord = None
        sample_track = None

        for t in board.tracks:
            # Skip short segments and power/gnd tracks
            if t.length < 5.0 or "GND" in t.net_name.upper() or "VCC" in t.net_name.upper():
                continue

            mid_pt = Point((t.start_x + t.end_x) / 2, (t.start_y + t.end_y) / 2)
            has_ref = any(poly.contains(mid_pt) for poly in gnd_polygons)
            if not has_ref:
                unreferenced_count += 1
                if sample_coord is None:
                    sample_coord = Coordinate(x=mid_pt.x, y=mid_pt.y, layer=t.layer)
                    sample_track = t

        if unreferenced_count > 0 and sample_coord and sample_track:
            findings.append(
                Finding(
                    id=f"GND-UNREFERENCED-TRACKS-{sample_track.net_name}",
                    title=f"Signal tracks without ground reference plane ({unreferenced_count} segments)",
                    severity=Severity.WARNING,
                    category=FindingCategory.SIGNAL_INTEGRITY,
                    description=(
                        f"Detected {unreferenced_count} signal track segments (such as net '{sample_track.net_name}') "
                        f"routing outside the boundaries of any filled ground plane."
                    ),
                    rule_id=self.rule_id,
                    nets=[sample_track.net_name],
                    coordinates=[sample_coord],
                    rationale="Signals routed outside ground planes lack a low-impedance return path, causing EMI and reflections.",
                    recommendation="Expand the ground copper zone to encompass all signal routing areas.",
                    raw_data={"unreferenced_count": unreferenced_count},
                )
            )

        return findings
