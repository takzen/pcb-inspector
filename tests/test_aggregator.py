"""Unit tests for FindingAggregator and ActionableFix generation."""

from __future__ import annotations

from pcb_inspector.core.aggregator import FindingAggregator
from pcb_inspector.core.models import (
    AuditResult,
    AuditSummary,
    Coordinate,
    Finding,
    FindingCategory,
    Severity,
)


def test_aggregator_correlates_shared_component_and_coordinates() -> None:
    f1 = Finding(
        id="DRC-001",
        title="Clearance violation",
        severity=Severity.CRITICAL,
        category=FindingCategory.DRC_ERC,
        description="Pad clearance violation on U1",
        rule_id="DRC_CLEARANCE",
        components=["U1"],
        nets=["+3V3"],
        coordinates=[Coordinate(x=50.0, y=50.0)],
    )

    f2 = Finding(
        id="DEC-001",
        title="Decoupling capacitor excessive distance",
        severity=Severity.WARNING,
        category=FindingCategory.DECOUPLING,
        description="Capacitor C1 is placed too far from U1",
        rule_id="HEUR-DEC-001",
        components=["U1", "C1"],
        nets=["+3V3"],
        coordinates=[Coordinate(x=51.0, y=50.5)],  # Distance ~1.12mm (within 2.5mm)
    )

    aggregator = FindingAggregator(correlation_radius_mm=2.5)
    consolidated = aggregator.aggregate([f1, f2])

    assert len(consolidated) == 2
    # Critical should come first
    assert consolidated[0].id == "DRC-001"
    assert consolidated[1].id == "DEC-001"

    # Both should be correlated with each other
    assert "DEC-001" in consolidated[0].correlated_with
    assert "DRC-001" in consolidated[1].correlated_with


def test_aggregator_synthesizes_actionable_fixes() -> None:
    f_dec = Finding(
        id="DEC-001",
        title="Decoupling cap distance",
        severity=Severity.WARNING,
        category=FindingCategory.DECOUPLING,
        description="C1 is far from U1",
        rule_id="HEUR-DEC-001",
        components=["U1", "C1"],
        nets=["+3V3"],
        coordinates=[Coordinate(x=10.0, y=10.0)],
    )

    f_pwr = Finding(
        id="PWR-001",
        title="Thin trace on power net",
        severity=Severity.WARNING,
        category=FindingCategory.POWER_DELIVERY,
        description="Net +5V width is 0.2mm",
        rule_id="HEUR-PWR-001",
        nets=["+5V"],
    )

    f_silk = Finding(
        id="SILK-001",
        title="Missing Pin 1 indicator on IC",
        severity=Severity.WARNING,
        category=FindingCategory.SILKSCREEN,
        description="U2 lacks pin 1 mark",
        rule_id="VISION-AI-001",
        components=["U2"],
    )

    aggregator = FindingAggregator()
    results = aggregator.aggregate([f_dec, f_pwr, f_silk])

    assert results[0].actionable_fix is not None
    assert results[0].actionable_fix.action_type in ("RELOCATE_COMPONENT", "WIDEN_TRACE", "ADD_PIN1_MARKER")

    # Verify actionable fixes property on AuditResult
    res = AuditResult.create(project_path="test.kicad_pcb", findings=results)
    fixes = res.actionable_fixes
    assert len(fixes) == 3


def test_health_score_calculation() -> None:
    f_crit = Finding(
        id="C1",
        title="Short circuit",
        severity=Severity.CRITICAL,
        category=FindingCategory.DRC_ERC,
        description="desc",
        rule_id="DRC",
    )
    f_warn = Finding(
        id="W1",
        title="Cap distance",
        severity=Severity.WARNING,
        category=FindingCategory.DECOUPLING,
        description="desc",
        rule_id="HEUR",
    )

    summary = AuditSummary.calculate([f_crit, f_warn])
    # 100 - (1 * 30) - (1 * 10) = 60
    assert summary.health_score == 60.0
    assert summary.critical_count == 1
    assert summary.warning_count == 1
    assert summary.passed is False
