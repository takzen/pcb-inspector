"""Heuristic rule checking trace widths on power distribution nets."""

from __future__ import annotations

from typing import Any

from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.core.models import Coordinate, Finding, FindingCategory, Severity
from pcb_inspector.kicad.pcb_model import PcbBoard, TrackSegment, is_supply_net, net_tokens
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

    @classmethod
    def _is_power_net(cls, net_name: str) -> bool:
        if "GND" in net_tokens(net_name):
            # Ground is normally poured as a zone, not routed as a track.
            return False
        # Shared with the decoupling rule. A fixed keyword list here missed
        # every rail written with a decimal point, such as +3.3V.
        return is_supply_net(net_name)

    def evaluate(self, context: Any, config: InspectorConfig) -> list[Finding]:
        findings: list[Finding] = []

        board = resolve_board(context)
        if board is None:
            return findings

        floor = self.param(config, "min_width_mm", config.min_power_trace_width_mm)

        # One finding per (net, layer), not per segment. KiCad splits a single
        # routed trace into one segment per bend, so per-segment findings let a
        # single undersized net emit dozens of warnings and drive the health
        # score to zero on its own.
        groups: dict[tuple[str, str], list[TrackSegment]] = {}
        for track in board.tracks:
            if not self._is_power_net(track.net_name):
                continue
            declared = self._declared_width(board, track.net_name)
            limit = max(floor, declared) if declared is not None else floor
            if track.width < (limit - 1e-4):
                groups.setdefault((track.net_name, track.layer), []).append(track)

        for (net_name, layer), segments in sorted(groups.items()):
            declared = self._declared_width(board, net_name)
            narrowest = min(segments, key=lambda t: t.width)
            findings.append(
                self._build_finding(board, net_name, layer, segments, narrowest, floor, declared)
            )

        return findings

    @staticmethod
    def _declared_width(board: PcbBoard, net_name: str) -> float | None:
        """Track width the net's own net class declares, if any."""
        net_class = board.net_class_for(net_name)
        return net_class.track_width if net_class else None

    def _build_finding(
        self,
        board: PcbBoard,
        net_name: str,
        layer: str,
        segments: list[TrackSegment],
        narrowest: TrackSegment,
        floor: float,
        declared: float | None,
    ) -> Finding:
        """Report a group of undersized segments, ranked by what it contradicts.

        Two different statements are possible and they carry different weight:

        * Narrower than the net's own net class — the layout contradicts the
          designer's declared intent. That is the strongest signal this rule has.
        * Meets its net class but sits below our configured floor — the designer
          chose this deliberately, so it is a suggestion, not a defect. KiCad
          treats net class widths as defaults rather than constraints, so a
          trace at its declared width is not a violation of anything.

        Before net classes were read, the second case was reported as a WARNING.
        On KiCad's own CM5 demo that flagged nine power nets routed at exactly
        the 0.15mm their Default class specifies.
        """
        total_length = sum(t.length for t in segments)
        mid_x = (narrowest.start_x + narrowest.end_x) / 2
        mid_y = (narrowest.start_y + narrowest.end_y) / 2
        plural = "s" if len(segments) > 1 else ""
        coordinates = [
            Coordinate(x=t.start_x, y=t.start_y, layer=t.layer) for t in segments[:10]
        ]

        contradicts_netclass = declared is not None and narrowest.width < (declared - 1e-4)

        if contradicts_netclass:
            assert declared is not None
            net_class = board.net_class_for(net_name)
            class_name = net_class.name if net_class else "its net class"
            severity = Severity.WARNING
            title = (
                f"Power trace on '{net_name}' is narrower than net class "
                f"'{class_name}' ({narrowest.width:.2f} mm vs {declared:.2f} mm)"
            )
            description = (
                f"{len(segments)} track segment{plural} on power rail '{net_name}' ({layer}) "
                f"are narrower than the {declared:.2f} mm declared by net class "
                f"'{class_name}'. The narrowest measures {narrowest.width:.2f} mm; "
                f"{total_length:.1f} mm of routing is affected."
            )
            recommendation = (
                f"Widen net '{net_name}' near ({mid_x:.1f}, {mid_y:.1f}) to the "
                f"{declared:.2f} mm its net class specifies."
            )
            target = declared
        elif declared is not None:
            severity = Severity.SUGGESTION
            title = (
                f"Power trace on '{net_name}' is thin for a power rail "
                f"({narrowest.width:.2f} mm)"
            )
            description = (
                f"{len(segments)} track segment{plural} on power rail '{net_name}' ({layer}) "
                f"measure {narrowest.width:.2f} mm, matching the net class but below the "
                f"configured {floor:.2f} mm guideline for power nets. "
                f"{total_length:.1f} mm of routing is affected."
            )
            recommendation = (
                f"Confirm {narrowest.width:.2f} mm carries the expected current for "
                f"'{net_name}', or raise the width in the net class."
            )
            target = floor
        else:
            severity = Severity.WARNING
            title = (
                f"Undersized power trace on net '{net_name}' "
                f"({narrowest.width:.2f} mm, {len(segments)} segment{plural})"
            )
            description = (
                f"{len(segments)} track segment{plural} on power rail '{net_name}' ({layer}) "
                f"fall below the recommended minimum width of {floor:.2f} mm. "
                f"The narrowest measures {narrowest.width:.2f} mm; "
                f"{total_length:.1f} mm of routing is affected."
            )
            recommendation = (
                f"Increase trace width on net '{net_name}' near ({mid_x:.1f}, {mid_y:.1f}) "
                f"to at least {floor:.2f} mm (or route via copper pour polygon)."
            )
            target = floor

        return Finding(
            id=f"PWR-WIDTH-{net_name}-{layer}",
            title=title,
            severity=severity,
            category=FindingCategory.POWER_DELIVERY,
            description=description,
            rule_id=self.rule_id,
            nets=[net_name],
            coordinates=coordinates,
            rationale=(
                "Narrow traces on power delivery networks introduce parasitic DC resistance and "
                "inductive impedance, leading to voltage dips during transient load steps."
            ),
            recommendation=recommendation,
            raw_data={
                "measured_width_mm": narrowest.width,
                "min_width_threshold_mm": target,
                "configured_floor_mm": floor,
                "net_class_width_mm": declared,
                "contradicts_net_class": contradicts_netclass,
                "layer": layer,
                "segment_count": len(segments),
                "affected_length_mm": round(total_length, 3),
            },
        )
