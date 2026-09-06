"""Heuristic rule analyzing DC/DC converter high di/dt switching loop geometry."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shapely.geometry import MultiPoint
from shapely.ops import unary_union

from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.core.models import Coordinate, Finding, FindingCategory, Severity
from pcb_inspector.kicad.pcb_model import PcbBoard, load_pcb_board
from pcb_inspector.rules.base import BaseRule


class SwitchingLoopGeometryRule(BaseRule):
    """Detects high di/dt switching nodes (SW / LX / PH) and checks loop footprint compactness."""

    rule_id = "HEUR-DCDC-001"
    name = "DC/DC Converter Switching Loop Area"
    category = FindingCategory.POWER_DELIVERY
    default_severity = Severity.WARNING
    description = (
        "Analyzes switching nodes (SW, LX, PH) connecting buck/boost regulators, power inductors, "
        "and freewheeling diodes to ensure the critical high-frequency current loop is minimized."
    )

    SW_NODE_KEYWORDS = ("SW", "LX", "PH", "SWITCH", "BUCK_SW", "BOOST_SW")
    MAX_RECOMMENDED_LOOP_AREA_MM2 = 30.0  # mm²

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

        # Scan for switching nets
        for net_name in board.nets.values():
            net_upper = net_name.upper()
            if not any(sw in net_upper for sw in self.SW_NODE_KEYWORDS):
                continue

            # Find all pads and tracks on this switching net
            sw_pads = []
            related_components = set()
            for fp in board.footprints.values():
                for pad in fp.pads:
                    if pad.net_name == net_name:
                        sw_pads.append((pad.at_x, pad.at_y))
                        related_components.add(fp.refdes)

            tracks = board.get_tracks_by_net(net_name)
            for t in tracks:
                sw_pads.append((t.start_x, t.start_y))
                sw_pads.append((t.end_x, t.end_y))

            if len(sw_pads) < 3:
                continue

            # Calculate convex hull area of the switching node geometry
            points_geom = MultiPoint(sw_pads)
            hull = unary_union(points_geom).convex_hull
            area_mm2 = hull.area

            # If area is excessively large, flag as EMI loop hazard
            if area_mm2 > self.MAX_RECOMMENDED_LOOP_AREA_MM2:
                centroid = hull.centroid
                rep_coord = Coordinate(x=centroid.x, y=centroid.y)

                findings.append(
                    Finding(
                        id=f"DCDC-LOOP-AREA-{net_name}",
                        title=f"Excessive switching loop area on net '{net_name}' ({area_mm2:.1f} mm²)",
                        severity=Severity.WARNING,
                        category=FindingCategory.POWER_DELIVERY,
                        description=(
                            f"Switching node net '{net_name}' spanning components {sorted(related_components)} "
                            f"occupies an estimated loop area of {area_mm2:.1f} mm² (recommended: < "
                            f"{self.MAX_RECOMMENDED_LOOP_AREA_MM2:.1f} mm²)."
                        ),
                        rule_id=self.rule_id,
                        components=sorted(related_components),
                        nets=[net_name],
                        coordinates=[rep_coord],
                        rationale=(
                            "The switching node carries high di/dt pulsed currents. A large loop area acts "
                            "as an efficient loop antenna, radiating severe H-field EMI and causing ringing."
                        ),
                        recommendation=(
                            f"Tighten the placement between IC, inductor, and diode/capacitors. Keep the "
                            f"'{net_name}' copper pour as compact and direct as possible directly over GND plane."
                        ),
                        raw_data={
                            "net_name": net_name,
                            "measured_area_mm2": round(area_mm2, 2),
                            "max_recommended_area_mm2": self.MAX_RECOMMENDED_LOOP_AREA_MM2,
                        },
                    )
                )

        return findings
