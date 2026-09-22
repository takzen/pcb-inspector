"""Heuristic rule verifying ground plane continuity and reference return paths."""

from __future__ import annotations

import logging
from typing import Any

import shapely
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

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
    def _reference_layers(
        board: PcbBoard, track_layer: str, zone_layers: set[str]
    ) -> set[str]:
        """Layers that can act as a return reference for a track on ``track_layer``.

        Return current follows the path of least inductance, which is the
        nearest plane directly above or below the trace. A ground pour four
        layers away is not that plane, and one on the trace's own layer is
        coplanar copper rather than a reference at all.

        The stack order comes from the board's layer table, read in file order:
        the ordinals are identifiers, not positions. On a six-layer board they
        read F.Cu=0, In1.Cu=4, In2.Cu=6, In3.Cu=8, In4.Cu=10, B.Cu=2.
        """
        adjacent = set(board.adjacent_copper_layers(track_layer))
        if adjacent:
            return adjacent & zone_layers
        # Unknown stack: fall back to any layer that is not the trace's own.
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

        # Prepared geometry answers covers() through a spatial index. Without
        # it every segment was intersected against the full plane outline, which
        # took 144s on a 26,650-track board while every other rule took under 1s.
        for plane in planes_by_layer.values():
            shapely.prepare(plane)

        zone_layers = set(planes_by_layer)
        unreferenced: list[TrackSegment] = []

        for t in board.tracks:
            if t.length < min_length or net_tokens(t.net_name) & self._SKIP_TOKENS:
                continue

            candidates = self._reference_layers(board, t.layer, zone_layers)
            if not candidates:
                unreferenced.append(t)
                continue

            line = LineString([(t.start_x, t.start_y), (t.end_x, t.end_y)])

            # Fast path: a segment lying wholly over one plane is referenced,
            # and on a well-routed board that is almost every segment. The
            # exact overlap is only measured for the rest, so results are
            # identical to measuring all of them.
            if any(planes_by_layer[layer].covers(line) for layer in candidates):
                continue

            # The whole segment is measured, not its midpoint: a long trace can
            # leave the plane at both ends while its centre still sits over it.
            covered = max(
                self._covered_length(line, planes_by_layer[layer]) for layer in candidates
            )
            if covered < line.length * min_fraction:
                unreferenced.append(t)

        if unreferenced:
            findings.append(self._unreferenced_finding(unreferenced, min_fraction))

        return findings

    @staticmethod
    def _covered_length(line: LineString, plane: Any) -> float:
        """Length of ``line`` lying over ``plane``.

        The plane is first clipped to the segment's bounding box. Inner planes
        on a dense board run to 126,000 vertices, and a general intersection
        against all of them cost 33ms per segment; clip_by_rect is a
        specialised clipper that makes the same measurement 10x faster.
        Verified against the direct intersection on 251 segments of a
        26,650-track board: largest difference 6e-14 mm, same decision on all.
        """
        x0, y0, x1, y1 = line.bounds
        pad = 0.01
        window = shapely.clip_by_rect(plane, x0 - pad, y0 - pad, x1 + pad, y1 + pad)
        if window.is_empty:
            return 0.0
        # Clipping may return an invalid polygon; repair rather than trust it.
        if not window.is_valid:
            window = shapely.make_valid(window, method="structure")
        return float(line.intersection(window).length)

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
                # KiCad encodes holes in a filled zone with keyhole cuts, a ring
                # that touches itself along each cut, which GEOS reports as
                # invalid. The "structure" repair reads rings as shells and holes,
                # which is exactly that intent. The default "linework" repair took
                # 3s per 123k-vertex plane (12s of a 15s rule run); "structure"
                # gives the same area to 2e-16 in a tenth of the time. buffer(0)
                # is faster still but keeps only one lobe of a self-crossing
                # outline, silently discarding real copper.
                poly = shapely.make_valid(poly, method="structure")
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
