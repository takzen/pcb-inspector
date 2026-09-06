"""Findings aggregator: deduplication, cross-layer correlation, and actionable fix generation."""

from __future__ import annotations

import math

from pcb_inspector.core.models import (
    ActionableFix,
    Coordinate,
    Finding,
    FindingCategory,
    Severity,
)


def _distance_coords(c1: Coordinate, c2: Coordinate) -> float:
    """Euclidean distance between two coordinates in mm."""
    return math.hypot(c1.x - c2.x, c1.y - c2.y)


class FindingAggregator:
    """Consolidates findings from DRC/ERC, Heuristics, and Vision AI."""

    def __init__(self, correlation_radius_mm: float = 2.5) -> None:
        self.correlation_radius_mm = correlation_radius_mm

    def aggregate(self, findings: list[Finding]) -> list[Finding]:
        """Process findings: detect cross-layer correlations, attach actionable fixes, and sort."""
        if not findings:
            return []

        # 1. Cross-layer Correlation Detection
        self._correlate_findings(findings)

        # 2. Actionable Fix Generation
        for f in findings:
            if f.actionable_fix is None:
                f.actionable_fix = self._synthesize_actionable_fix(f)

        # 3. Canonical Sorting (Critical first, then Warning, Suggestion, Pass)
        severity_order = {
            Severity.CRITICAL: 0,
            Severity.WARNING: 1,
            Severity.SUGGESTION: 2,
            Severity.PASS: 3,
        }

        def sort_key(f: Finding) -> tuple[int, str, str]:
            first_comp = f.components[0] if f.components else ""
            return (severity_order.get(f.severity, 99), first_comp, f.id)

        return sorted(findings, key=sort_key)

    def _correlate_findings(self, findings: list[Finding]) -> None:
        """Find related issues across layers and link them via correlated_with."""
        n = len(findings)
        for i in range(n):
            for j in range(i + 1, n):
                f1 = findings[i]
                f2 = findings[j]

                # Don't correlate identical findings or findings from the exact same rule ID on the exact same component
                if f1.id == f2.id:
                    continue

                is_correlated = False

                # Criterion A: Shared component RefDes
                shared_components = set(f1.components) & set(f2.components)
                if shared_components:
                    # If they also share a net or are physically close
                    shared_nets = set(f1.nets) & set(f2.nets)
                    if shared_nets or f1.category != f2.category:
                        is_correlated = True

                # Criterion B: Physical spatial overlap within correlation radius
                if not is_correlated and f1.coordinates and f2.coordinates:
                    for c1 in f1.coordinates:
                        for c2 in f2.coordinates:
                            if _distance_coords(c1, c2) <= self.correlation_radius_mm:
                                is_correlated = True
                                break
                        if is_correlated:
                            break

                if is_correlated:
                    if f2.id not in f1.correlated_with:
                        f1.correlated_with.append(f2.id)
                    if f1.id not in f2.correlated_with:
                        f2.correlated_with.append(f1.id)

    def _synthesize_actionable_fix(self, f: Finding) -> ActionableFix | None:
        """Derive concrete agent-executable repair action from a finding."""
        rule = f.rule_id.upper()
        cat = f.category

        # Decoupling Capacitor Proximity
        if cat == FindingCategory.DECOUPLING or "DEC" in rule:
            target_comp = f.components[1] if len(f.components) > 1 else (f.components[0] if f.components else None)
            target_ic = f.components[0] if len(f.components) > 1 else "IC"
            target_coord = f.coordinates[0] if f.coordinates else None
            return ActionableFix(
                action_type="RELOCATE_COMPONENT",
                component=target_comp,
                net=f.nets[0] if f.nets else None,
                target_coordinates=target_coord,
                parameters={"target_ic": target_ic, "max_distance_mm": 3.5},
                description=f"Relocate capacitor {target_comp or ''} within 3.5mm of {target_ic} power pins.",
            )

        # Power Rail Trace Width
        if cat == FindingCategory.POWER_DELIVERY or "PWR" in rule:
            net_name = f.nets[0] if f.nets else None
            return ActionableFix(
                action_type="WIDEN_TRACE",
                net=net_name,
                parameters={"recommended_min_width_mm": 0.50},
                description=f"Widen power track for net '{net_name or 'power'}' to at least 0.50mm to reduce IR drop.",
            )

        # Differential Pair Length Mismatch
        if cat == FindingCategory.SIGNAL_INTEGRITY or "DIFF" in rule:
            return ActionableFix(
                action_type="TUNE_DIFF_PAIR_SKEW",
                net=f.nets[0] if f.nets else None,
                parameters={"max_skew_mm": 0.15},
                description="Add serpentine meandering or length-tuning loops to match differential pair trace lengths.",
            )

        # DC/DC Switching Loop
        if "DCDC" in rule or "SWITCH" in f.title.upper():
            return ActionableFix(
                action_type="COMPACT_SWITCHING_LOOP",
                parameters={"max_loop_area_mm2": 50.0},
                description="Reposition switching inductor, diode/FET, and input filter capacitors into a compact polygon.",
            )

        # Ground Plane Return Path
        if "GND" in rule or "RETURN" in f.title.upper():
            return ActionableFix(
                action_type="EXPAND_GROUND_PLANE",
                net="GND",
                description="Ensure continuous copper ground pour exists directly underneath high-speed signal tracks.",
            )

        # Silkscreen & Pin 1
        if cat == FindingCategory.SILKSCREEN or "SILK" in rule or "PIN 1" in f.title.upper():
            comp = f.components[0] if f.components else None
            return ActionableFix(
                action_type="ADD_PIN1_MARKER",
                component=comp,
                description=f"Add silkscreen Pin 1 dot or bevel indicator adjacent to pin 1 on component {comp or ''}.",
            )

        # DRC & ERC issues
        if cat == FindingCategory.DRC_ERC:
            title_lower = f.title.lower()
            desc_lower = f.description.lower()
            if "clearance" in title_lower or "clearance" in desc_lower:
                return ActionableFix(
                    action_type="ADJUST_CLEARANCE",
                    component=f.components[0] if f.components else None,
                    net=f.nets[0] if f.nets else None,
                    description="Reroute trace or move component pad to meet electrical clearance requirement.",
                )
            if "short" in title_lower or "short" in desc_lower:
                return ActionableFix(
                    action_type="REMOVE_SHORT_CIRCUIT",
                    net=f.nets[0] if f.nets else None,
                    description="Separate colliding copper nets to eliminate direct electrical short circuit.",
                )
            if "unrouted" in title_lower or "unconnected" in desc_lower:
                return ActionableFix(
                    action_type="ROUTE_NET",
                    net=f.nets[0] if f.nets else None,
                    description=f"Complete copper trace routing for unconnected net '{f.nets[0] if f.nets else ''}'.",
                )

        # Default fallback actionable fix
        if f.recommendation:
            return ActionableFix(
                action_type="MANUAL_REVIEW",
                component=f.components[0] if f.components else None,
                description=f.recommendation,
            )

        return None
