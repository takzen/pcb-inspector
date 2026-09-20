"""Heuristic rule analyzing DC/DC converter high di/dt switching loop geometry."""

from __future__ import annotations

import logging
from typing import Any

from shapely.geometry import MultiPoint
from shapely.ops import unary_union

from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.core.models import Coordinate, Finding, FindingCategory, Severity
from pcb_inspector.kicad.pcb_model import Footprint, net_tokens
from pcb_inspector.rules.base import BaseRule
from pcb_inspector.rules.board_loader import resolve_board

logger = logging.getLogger(__name__)


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

    #: Matched against whole tokens of a net name, never as substrings.
    #: Substring matching flagged SWCLK, SWDIO ("SW"), PHY_TXD and GRAPH_EN
    #: ("PH") as regulator switching nodes, so every STM32 or Ethernet board
    #: produced false EMI warnings.
    SW_NODE_KEYWORDS = frozenset({"SW", "LX", "PH", "PHASE", "SWITCH", "VSW", "SWN"})
    MAX_RECOMMENDED_LOOP_AREA_MM2 = 30.0  # mm²

    @classmethod
    def _is_switch_node_name(cls, net_name: str) -> bool:
        """True if the net name designates a regulator switching node."""
        return bool(net_tokens(net_name) & cls.SW_NODE_KEYWORDS)

    @staticmethod
    def _is_switch_node_topology(attached: list[Footprint]) -> bool:
        """True if the parts on this net form a converter switching node.

        The switching node of a buck/boost regulator always ties the converter
        (or its FET) to the energy-storage inductor, usually with a
        freewheeling diode. Requiring that shape rejects same-named debug and
        PHY nets, which connect an IC to passives or a connector instead.
        """
        has_inductor = any(fp.is_inductor for fp in attached)
        has_active = any(fp.is_ic or fp.is_diode for fp in attached)
        return has_inductor and has_active

    def evaluate(self, context: Any, config: InspectorConfig) -> list[Finding]:
        findings: list[Finding] = []

        board = resolve_board(context)
        if board is None:
            return findings

        max_area = self.param(
            config, "max_loop_area_mm2", self.MAX_RECOMMENDED_LOOP_AREA_MM2
        )

        # Scan for switching nets
        for net_name in board.nets.values():
            if not net_name or not self._is_switch_node_name(net_name):
                continue

            # Find all pads and tracks on this switching net
            sw_pads = []
            related_components = set()
            attached: list[Footprint] = []
            for fp, pad in board.get_pads_on_net(net_name):
                sw_pads.append((pad.at_x, pad.at_y))
                related_components.add(fp.refdes)
                attached.append(fp)

            # A name alone is not evidence. A real regulator switching node
            # joins the converter to its inductor (or freewheeling diode); a
            # debug or PHY net that happens to tokenize as "SW" does not.
            if not self._is_switch_node_topology(attached):
                logger.debug(
                    "Net '%s' matches a switching-node name but not the topology; skipping.",
                    net_name,
                )
                continue

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
            if area_mm2 > max_area:
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
                            f"{max_area:.1f} mm²)."
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
                            "max_recommended_area_mm2": max_area,
                        },
                    )
                )

        return findings
