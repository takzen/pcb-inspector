"""Heuristic rule checking trace widths on power distribution nets."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.core.models import Coordinate, Finding, FindingCategory, Severity
from pcb_inspector.kicad.pcb_model import PcbBoard, load_pcb_board
from pcb_inspector.rules.base import BaseRule


class PowerTraceWidthRule(BaseRule):
    """Verifies that high-current and power distribution tracks meet minimum width requirements."""

    rule_id = "HEUR-PWR-001"
    name = "Power Rail Minimum Trace Width"
    category = FindingCategory.POWER_DELIVERY
    default_severity = Severity.WARNING
    description = (
        "Ensures power supply nets maintain adequate copper trace width to prevent excessive "
        "IR voltage drop, parasitic trace resistance, and localized Joule heating."
    )

    POWER_NET_KEYWORDS = (
        "VCC",
        "VDD",
        "VBUS",
        "VBAT",
        "+3V3",
        "+5V",
        "+12V",
        "+24V",
        "3V3",
        "5V",
        "12V",
        "VIN",
        "VOUT",
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

        min_width = config.min_power_trace_width_mm

        # Check track segments
        for idx, track in enumerate(board.tracks, start=1):
            net_upper = track.net_name.upper()
            if not any(kw in net_upper for kw in self.POWER_NET_KEYWORDS):
                continue
            # Skip ground traces here as they are usually polygons/planes
            if "GND" in net_upper:
                continue

            if track.width < (min_width - 1e-4):
                mid_x = (track.start_x + track.end_x) / 2
                mid_y = (track.start_y + track.end_y) / 2

                findings.append(
                    Finding(
                        id=f"PWR-WIDTH-{track.net_name}-{idx:03d}",
                        title=f"Undersized power trace on net '{track.net_name}' ({track.width:.2f} mm)",
                        severity=Severity.WARNING,
                        category=FindingCategory.POWER_DELIVERY,
                        description=(
                            f"Track segment on power rail '{track.net_name}' ({track.layer}) has a width of "
                            f"{track.width:.2f} mm, below the recommended minimum of {min_width:.2f} mm."
                        ),
                        rule_id=self.rule_id,
                        nets=[track.net_name],
                        coordinates=[
                            Coordinate(x=track.start_x, y=track.start_y, layer=track.layer),
                            Coordinate(x=track.end_x, y=track.end_y, layer=track.layer),
                        ],
                        rationale=(
                            "Narrow traces on power delivery networks introduce parasitic DC resistance and "
                            "inductive impedance, leading to voltage dips during transient load steps."
                        ),
                        recommendation=(
                            f"Increase trace width on net '{track.net_name}' near ({mid_x:.1f}, {mid_y:.1f}) "
                            f"to at least {min_width:.2f} mm (or route via copper pour polygon)."
                        ),
                        raw_data={
                            "measured_width_mm": track.width,
                            "min_width_threshold_mm": min_width,
                            "layer": track.layer,
                        },
                    )
                )

        return findings
