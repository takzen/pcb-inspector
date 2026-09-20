"""Findings aggregator: cross-layer correlation and actionable fix generation."""

from __future__ import annotations

import math
from collections.abc import Callable

from pcb_inspector.core.config import InspectorConfig
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

    def __init__(
        self,
        correlation_radius_mm: float = 2.5,
        config: InspectorConfig | None = None,
    ) -> None:
        self.correlation_radius_mm = correlation_radius_mm
        # Thresholds quoted in generated fixes must match the ones the rules
        # applied, otherwise an agent repairs towards a different target than
        # the one it will be re-audited against.
        self.config = config or InspectorConfig()

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
        """Derive a concrete agent-executable repair action from a finding.

        Dispatch is keyed on rule_id, not category. Categories are shared
        between rules, and a category-ordered if-cascade mislabelled every
        finding whose category another rule claimed first: a missing ground
        plane (SIGNAL_INTEGRITY) was handed TUNE_DIFF_PAIR_SKEW, and an
        oversized switching loop (POWER_DELIVERY) was handed WIDEN_TRACE.
        """
        builder = _FIX_BUILDERS.get(f.rule_id.upper())
        if builder is not None:
            return builder(self, f)
        return self._fallback_fix(f)

    # -- per-rule builders --------------------------------------------------

    def _fix_decoupling(self, f: Finding) -> ActionableFix:
        # components is [ic, capacitor] for a distance finding, and [ic] alone
        # when no capacitor exists on the rail at all.
        target_comp = f.components[1] if len(f.components) > 1 else None
        target_ic = f.components[0] if f.components else "IC"
        max_distance = self._threshold(
            f, "max_threshold_mm", self.config.max_decoupling_distance_mm
        )
        if target_comp is None:
            return ActionableFix(
                action_type="ADD_DECOUPLING_CAPACITOR",
                component=target_ic,
                net=f.nets[0] if f.nets else None,
                target_coordinates=f.coordinates[0] if f.coordinates else None,
                parameters={"target_ic": target_ic, "max_distance_mm": max_distance},
                description=(
                    f"Add a 100nF ceramic capacitor within {max_distance:.2f}mm of "
                    f"{target_ic} power pins."
                ),
            )
        return ActionableFix(
            action_type="RELOCATE_COMPONENT",
            component=target_comp,
            net=f.nets[0] if f.nets else None,
            target_coordinates=f.coordinates[0] if f.coordinates else None,
            parameters={"target_ic": target_ic, "max_distance_mm": max_distance},
            description=(
                f"Relocate capacitor {target_comp} within {max_distance:.2f}mm of "
                f"{target_ic} power pins."
            ),
        )

    def _fix_trace_width(self, f: Finding) -> ActionableFix:
        net_name = f.nets[0] if f.nets else None
        min_width = self._threshold(
            f, "min_width_threshold_mm", self.config.min_power_trace_width_mm
        )
        return ActionableFix(
            action_type="WIDEN_TRACE",
            net=net_name,
            parameters={
                "recommended_min_width_mm": min_width,
                "segment_count": f.raw_data.get("segment_count"),
                "layer": f.raw_data.get("layer"),
            },
            description=(
                f"Widen power track for net '{net_name or 'power'}' to at least "
                f"{min_width:.2f}mm to reduce IR drop."
            ),
        )

    def _fix_diff_skew(self, f: Finding) -> ActionableFix:
        max_skew = self._threshold(
            f, "max_skew_threshold_mm", self.config.max_diff_pair_skew_mm
        )
        return ActionableFix(
            action_type="TUNE_DIFF_PAIR_SKEW",
            net=f.nets[0] if f.nets else None,
            parameters={
                "max_skew_mm": max_skew,
                "measured_skew_mm": f.raw_data.get("skew_mm"),
            },
            description=(
                "Add serpentine meandering or length-tuning loops to match differential "
                f"pair trace lengths to within {max_skew:.2f}mm."
            ),
        )

    def _fix_switching_loop(self, f: Finding) -> ActionableFix:
        max_area = self._threshold(f, "max_recommended_area_mm2", 30.0)
        return ActionableFix(
            action_type="COMPACT_SWITCHING_LOOP",
            net=f.nets[0] if f.nets else None,
            parameters={
                "max_loop_area_mm2": max_area,
                "measured_area_mm2": f.raw_data.get("measured_area_mm2"),
            },
            description=(
                "Reposition switching inductor, diode/FET, and input filter capacitors into "
                f"a compact polygon below {max_area:.1f} mm2."
            ),
        )

    def _fix_ground_plane(self, f: Finding) -> ActionableFix:
        return ActionableFix(
            action_type="EXPAND_GROUND_PLANE",
            net="GND",
            parameters={"unreferenced_count": f.raw_data.get("unreferenced_count")},
            description=(
                "Ensure a continuous copper ground pour exists directly underneath "
                "high-speed signal tracks."
            ),
        )

    # -- fallbacks ----------------------------------------------------------

    @staticmethod
    def _threshold(f: Finding, key: str, default: float) -> float:
        """Prefer the threshold the rule actually applied over a global default.

        Rules record what they measured against in raw_data. Reading it back
        keeps the machine-readable fix consistent with the report text; the
        previous hardcoded constants disagreed with both (0.50mm against a
        configured 0.30mm, 50 mm2 against a rule limit of 30 mm2).
        """
        value = f.raw_data.get(key, default)
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _fallback_fix(self, f: Finding) -> ActionableFix | None:
        """Best-effort action for findings from rules without a dedicated builder.

        Covers vision findings and native DRC/ERC violations, whose remedy is
        inferred from the violation text rather than from a known rule.
        """
        rule = f.rule_id.upper()
        title_lower = f.title.lower()
        desc_lower = f.description.lower()

        if f.category == FindingCategory.SILKSCREEN or "SILK" in rule or "pin 1" in title_lower:
            comp = f.components[0] if f.components else None
            return ActionableFix(
                action_type="ADD_PIN1_MARKER",
                component=comp,
                description=(
                    "Add silkscreen Pin 1 dot or bevel indicator adjacent to pin 1 on "
                    f"component {comp or ''}."
                ),
            )

        if f.category == FindingCategory.DRC_ERC:
            if "clearance" in title_lower or "clearance" in desc_lower:
                return ActionableFix(
                    action_type="ADJUST_CLEARANCE",
                    component=f.components[0] if f.components else None,
                    net=f.nets[0] if f.nets else None,
                    description=(
                        "Reroute trace or move component pad to meet electrical clearance "
                        "requirement."
                    ),
                )
            if "short" in title_lower or "short" in desc_lower:
                return ActionableFix(
                    action_type="REMOVE_SHORT_CIRCUIT",
                    net=f.nets[0] if f.nets else None,
                    description=(
                        "Separate colliding copper nets to eliminate direct electrical short "
                        "circuit."
                    ),
                )
            if "unrouted" in title_lower or "unconnected" in desc_lower:
                return ActionableFix(
                    action_type="ROUTE_NET",
                    net=f.nets[0] if f.nets else None,
                    description=(
                        "Complete copper trace routing for unconnected net "
                        f"'{f.nets[0] if f.nets else ''}'."
                    ),
                )

        if f.recommendation:
            return ActionableFix(
                action_type="MANUAL_REVIEW",
                component=f.components[0] if f.components else None,
                description=f.recommendation,
            )

        return None


#: rule_id -> builder. Keyed on the rule that produced the finding so that two
#: rules sharing a FindingCategory cannot be conflated.
_FIX_BUILDERS: dict[str, Callable[[FindingAggregator, Finding], ActionableFix]] = {
    "HEUR-DEC-001": FindingAggregator._fix_decoupling,
    "HEUR-PWR-001": FindingAggregator._fix_trace_width,
    "HEUR-DIFF-001": FindingAggregator._fix_diff_skew,
    "HEUR-DCDC-001": FindingAggregator._fix_switching_loop,
    "HEUR-GND-001": FindingAggregator._fix_ground_plane,
}
