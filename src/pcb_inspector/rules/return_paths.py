"""Heuristic rule verifying ground plane continuity and reference return paths."""

from __future__ import annotations

import logging
from typing import Any

from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union
from shapely.validation import make_valid

from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.core.models import Coordinate, Finding, FindingCategory, Severity
from pcb_inspector.kicad.pcb_model import PcbBoard, TrackSegment, net_tokens
from pcb_inspector.rules.base import BaseRule
from pcb_inspector.rules.board_loader import resolve_board

logger = logging.getLogger(__name__)


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

    #: Segments shorter than this are too short to matter as antennas.
    MIN_TRACK_LENGTH_MM = 5.0

    #: Fraction of a segment that must lie over ground copper to count as
    #: referenced. Below 1.0 to tolerate the plane's own clearance cutouts at
    #: the segment's endpoints, where it meets pads.
    MIN_REFERENCED_FRACTION = 0.9

    #: Net name tokens whose return path is not provided by a plane below them.
    _SKIP_TOKENS = frozenset({"GND", "AGND", "DGND", "VCC", "VDD"})

    @staticmethod
    def _reference_layers(track_layer: str, zone_layers: set[str]) -> set[str]:
        """Layers that can act as a return reference for a track on ``track_layer``.

        A zone on the track's own layer is coplanar copper, not a reference
        plane beneath it; counting it made an F.Cu ground pour "reference" the
        F.Cu signals routed beside it.
        """
        return {layer for layer in zone_layers if layer != track_layer}

    def evaluate(self, context: Any, config: InspectorConfig) -> list[Finding]:
        findings: list[Finding] = []

        board = resolve_board(context)
        if board is None:
            return findings

        min_length = self.param(config, "min_track_length_mm", self.MIN_TRACK_LENGTH_MM)
        min_fraction = self.param(
            config, "min_referenced_fraction", self.MIN_REFERENCED_FRACTION
        )

        gnd_zones = [
            z for z in board.zones if "GND" in net_tokens(z.net_name) and z.copper_polygons
        ]

        if not gnd_zones and len(board.tracks) > 5:
            findings.append(self._no_plane_finding(board))
            return findings

        # Ground copper, unioned per layer. A zone contributes several filled
        # islands, and two zones on one layer may overlap.
        planes_by_layer: dict[str, Any] = {}
        for gz in gnd_zones:
            for points in gz.copper_polygons:
                poly = self._to_polygon(points)
                if poly is None:
                    continue
                existing = planes_by_layer.get(gz.layer)
                planes_by_layer[gz.layer] = (
                    unary_union([existing, poly]) if existing is not None else poly
                )

        if not planes_by_layer:
            return findings

        zone_layers = set(planes_by_layer)
        unreferenced: list[TrackSegment] = []

        for t in board.tracks:
            if t.length < min_length or net_tokens(t.net_name) & self._SKIP_TOKENS:
                continue

            candidates = self._reference_layers(t.layer, zone_layers)
            if not candidates:
                unreferenced.append(t)
                continue

            line = LineString([(t.start_x, t.start_y), (t.end_x, t.end_y)])
            # The whole segment is measured, not its midpoint: a long trace can
            # leave the plane at both ends while its centre still sits over it.
            covered = max(
                line.intersection(planes_by_layer[layer]).length for layer in candidates
            )
            if covered < line.length * min_fraction:
                unreferenced.append(t)

        if unreferenced:
            findings.append(self._unreferenced_finding(unreferenced, min_fraction))

        return findings

    @staticmethod
    def _to_polygon(points: list[tuple[float, float]]) -> Polygon | Any | None:
        """Build a valid shapely polygon from zone vertices, repairing if needed.

        Self-intersecting pours are common in real layouts. Dropping them, as
        the previous implementation did, silently removed a real ground plane
        from consideration and produced false 'unreferenced' findings.
        """
        if len(points) < 3:
            return None
        try:
            poly = Polygon(points)
            if poly.is_empty:
                return None
            if not poly.is_valid:
                poly = make_valid(poly)
            return None if poly.is_empty else poly
        except Exception as err:  # pragma: no cover - shapely edge cases
            logger.debug("Skipping unusable ground zone polygon: %s", err)
            return None

    def _no_plane_finding(self, board: PcbBoard) -> Finding:
        return Finding(
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

    def _unreferenced_finding(
        self, unreferenced: list[TrackSegment], min_fraction: float
    ) -> Finding:
        worst = max(unreferenced, key=lambda t: t.length)
        affected_length = sum(t.length for t in unreferenced)
        nets = sorted({t.net_name for t in unreferenced if t.net_name})

        return Finding(
            id=f"GND-UNREFERENCED-TRACKS-{worst.net_name or 'UNNAMED'}",
            title=(
                f"Signal tracks without ground reference plane "
                f"({len(unreferenced)} segments)"
            ),
            severity=Severity.WARNING,
            category=FindingCategory.SIGNAL_INTEGRITY,
            description=(
                f"Detected {len(unreferenced)} signal track segments (such as net "
                f"'{worst.net_name}') with less than {min_fraction:.0%} of their length over "
                f"ground copper on an adjacent layer. {affected_length:.1f} mm of routing is "
                f"affected."
            ),
            rule_id=self.rule_id,
            nets=nets[:10],
            coordinates=[
                Coordinate(
                    x=(t.start_x + t.end_x) / 2,
                    y=(t.start_y + t.end_y) / 2,
                    layer=t.layer,
                )
                for t in sorted(unreferenced, key=lambda t: -t.length)[:10]
            ],
            rationale=(
                "Signals routed outside ground planes lack a low-impedance return path, "
                "causing EMI and reflections."
            ),
            recommendation="Expand the ground copper zone to encompass all signal routing areas.",
            raw_data={
                "unreferenced_count": len(unreferenced),
                "affected_length_mm": round(affected_length, 3),
                "min_referenced_fraction": min_fraction,
            },
        )
