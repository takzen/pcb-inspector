"""Heuristic rule verifying decoupling capacitor proximity to IC power pins."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.core.models import Coordinate, Finding, FindingCategory, Severity
from pcb_inspector.kicad.pcb_model import PcbBoard, load_pcb_board
from pcb_inspector.rules.base import BaseRule


class DecouplingProximityRule(BaseRule):
    """Verifies that each IC power pin has a decoupling capacitor in close spatial proximity."""

    rule_id = "HEUR-DEC-001"
    name = "Decoupling Capacitor Proximity"
    category = FindingCategory.DECOUPLING
    default_severity = Severity.WARNING
    description = (
        "Ensures high-frequency bypass/decoupling capacitors are placed within the maximum "
        "allowable distance from IC power supply pins to minimize parasitic trace inductance."
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

        max_dist = config.max_decoupling_distance_mm
        critical_threshold = max_dist * 3.5  # e.g., > 12.25 mm is considered severe

        # Scan ICs
        for refdes, fp in board.footprints.items():
            if not fp.is_ic:
                continue

            # Group power pads
            power_pads = [p for p in fp.pads if p.is_power]
            if not power_pads:
                continue

            for p_pad in power_pads:
                net_name = p_pad.net_name
                if not net_name:
                    continue

                # Find all capacitors connected to this power net
                candidate_caps = board.get_capacitors_on_net(net_name)
                if not candidate_caps:
                    # No capacitor on this power rail at all!
                    findings.append(
                        Finding(
                            id=f"DEC-MISSING-{refdes}-{p_pad.number}",
                            title=f"Missing decoupling capacitor for {refdes} pin {p_pad.number}",
                            severity=Severity.CRITICAL,
                            category=FindingCategory.DECOUPLING,
                            description=(
                                f"IC {refdes} power pin {p_pad.number} ({net_name}) has no bypass "
                                f"capacitor connected to rail '{net_name}' on the entire board."
                            ),
                            rule_id=self.rule_id,
                            components=[refdes],
                            nets=[net_name],
                            coordinates=[Coordinate(x=p_pad.at_x, y=p_pad.at_y)],
                            rationale="Without decoupling capacitors, switching noise and voltage dips will cause logic instability or resets.",
                            recommendation=f"Add a 100nF ceramic capacitor (C_0402 or C_0603) placed < {max_dist:.1f}mm from pin {p_pad.number}.",
                        )
                    )
                    continue

                # Find nearest capacitor to this specific power pin
                best_cap = None
                best_dist = float("inf")
                best_cap_coord = (0.0, 0.0)

                for cap in candidate_caps:
                    # Find the power pad on the capacitor
                    for c_pad in cap.pads:
                        if c_pad.net_name == net_name:
                            dist = math.hypot(p_pad.at_x - c_pad.at_x, p_pad.at_y - c_pad.at_y)
                            if dist < best_dist:
                                best_dist = dist
                                best_cap = cap
                                best_cap_coord = (c_pad.at_x, c_pad.at_y)

                if best_dist > max_dist and best_cap is not None:
                    sev = Severity.CRITICAL if best_dist >= critical_threshold else Severity.WARNING
                    findings.append(
                        Finding(
                            id=f"DEC-DIST-{refdes}-{best_cap.refdes}-{p_pad.number}",
                            title=f"Excessive decoupling distance: {best_cap.refdes} to {refdes} pin {p_pad.number}",
                            severity=sev,
                            category=FindingCategory.DECOUPLING,
                            description=(
                                f"Bypass capacitor {best_cap.refdes} is located {best_dist:.2f} mm away from "
                                f"{refdes} power pin {p_pad.number} (configured limit: {max_dist:.1f} mm)."
                            ),
                            rule_id=self.rule_id,
                            components=[refdes, best_cap.refdes],
                            nets=[net_name],
                            coordinates=[
                                Coordinate(x=p_pad.at_x, y=p_pad.at_y),
                                Coordinate(x=best_cap_coord[0], y=best_cap_coord[1]),
                            ],
                            rationale=(
                                f"Parasitic trace loop inductance increases by ~1 nH per mm of trace length. "
                                f"At {best_dist:.1f} mm, high-frequency transients cannot be shunted effectively."
                            ),
                            recommendation=(
                                f"Relocate capacitor {best_cap.refdes} closer to {refdes} pin {p_pad.number} "
                                f"within {max_dist:.1f} mm on layer {p_pad.layers[0] if p_pad.layers else 'F.Cu'}."
                            ),
                            raw_data={
                                "measured_distance_mm": round(best_dist, 3),
                                "max_threshold_mm": max_dist,
                            },
                        )
                    )

        return findings
