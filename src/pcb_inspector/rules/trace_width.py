"""Heuristic rule checking trace widths on power distribution nets."""

from __future__ import annotations

from typing import Any

from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.core.models import Coordinate, Finding, FindingCategory, Severity
from pcb_inspector.kicad.pcb_model import TrackSegment, net_tokens
from pcb_inspector.rules.base import BaseRule
from pcb_inspector.rules.board_loader import resolve_board


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

    #: Matched against whole tokens of a net name. "+3V3" tokenizes to {3V3},
    #: "VDD_CORE" to {VDD, CORE}.
    POWER_NET_KEYWORDS = frozenset(
        {
            "VCC",
            "VDD",
            "VBUS",
            "VBAT",
            "VIN",
            "VOUT",
            "VSYS",
            "3V3",
            "5V",
            "12V",
            "24V",
            "1V8",
            "2V5",
        }
    )

    @classmethod
    def _is_power_net(cls, net_name: str) -> bool:
        tokens = net_tokens(net_name)
        if "GND" in tokens:
            # Ground is normally poured as a zone, not routed as a track.
            return False
        return bool(tokens & cls.POWER_NET_KEYWORDS)

    def evaluate(self, context: Any, config: InspectorConfig) -> list[Finding]:
        findings: list[Finding] = []

        board = resolve_board(context)
        if board is None:
            return findings

        min_width = self.param(
            config, "min_width_mm", config.min_power_trace_width_mm
        )

        # One finding per (net, layer), not per segment. KiCad splits a single
        # routed trace into one segment per bend, so per-segment findings let a
        # single undersized net emit dozens of warnings and drive the health
        # score to zero on its own.
        groups: dict[tuple[str, str], list[TrackSegment]] = {}
        for track in board.tracks:
            if not self._is_power_net(track.net_name):
                continue
            if track.width < (min_width - 1e-4):
                groups.setdefault((track.net_name, track.layer), []).append(track)

        for (net_name, layer), segments in sorted(groups.items()):
            narrowest = min(segments, key=lambda t: t.width)
            total_length = sum(t.length for t in segments)
            mid_x = (narrowest.start_x + narrowest.end_x) / 2
            mid_y = (narrowest.start_y + narrowest.end_y) / 2

            plural = "s" if len(segments) > 1 else ""
            findings.append(
                Finding(
                    id=f"PWR-WIDTH-{net_name}-{layer}",
                    title=(
                        f"Undersized power trace on net '{net_name}' "
                        f"({narrowest.width:.2f} mm, {len(segments)} segment{plural})"
                    ),
                    severity=Severity.WARNING,
                    category=FindingCategory.POWER_DELIVERY,
                    description=(
                        f"{len(segments)} track segment{plural} on power rail '{net_name}' ({layer}) "
                        f"fall below the recommended minimum width of {min_width:.2f} mm. "
                        f"The narrowest measures {narrowest.width:.2f} mm; "
                        f"{total_length:.1f} mm of routing is affected."
                    ),
                    rule_id=self.rule_id,
                    nets=[net_name],
                    coordinates=[
                        Coordinate(x=t.start_x, y=t.start_y, layer=t.layer) for t in segments[:10]
                    ],
                    rationale=(
                        "Narrow traces on power delivery networks introduce parasitic DC resistance and "
                        "inductive impedance, leading to voltage dips during transient load steps."
                    ),
                    recommendation=(
                        f"Increase trace width on net '{net_name}' near ({mid_x:.1f}, {mid_y:.1f}) "
                        f"to at least {min_width:.2f} mm (or route via copper pour polygon)."
                    ),
                    raw_data={
                        "measured_width_mm": narrowest.width,
                        "min_width_threshold_mm": min_width,
                        "layer": layer,
                        "segment_count": len(segments),
                        "affected_length_mm": round(total_length, 3),
                    },
                )
            )

        return findings
