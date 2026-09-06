"""Common test fixtures and sample data."""

from __future__ import annotations

import pytest

from pcb_inspector.core.models import (
    AuditResult,
    Coordinate,
    Finding,
    FindingCategory,
    Severity,
)


@pytest.fixture
def sample_coordinate() -> Coordinate:
    return Coordinate(x=12.5, y=34.2, layer="F.Cu", rotation=90.0)


@pytest.fixture
def sample_critical_finding(sample_coordinate: Coordinate) -> Finding:
    return Finding(
        id="DRC-001-SHORT-U1-GND",
        title="Clearance violation / short circuit",
        severity=Severity.CRITICAL,
        category=FindingCategory.DRC_ERC,
        description="Trace VCC short-circuited to GND plane",
        rule_id="DRC_CLEARANCE",
        components=["U1", "C1"],
        nets=["VCC", "GND"],
        coordinates=[sample_coordinate],
        rationale="Physical overlap creates direct power supply short circuit",
        recommendation="Reroute trace away from GND plane with at least 0.2mm clearance",
    )


@pytest.fixture
def sample_warning_finding() -> Finding:
    return Finding(
        id="DEC-002-U1-C3",
        title="Decoupling capacitor excessive distance",
        severity=Severity.WARNING,
        category=FindingCategory.DECOUPLING,
        description="Bypass capacitor C3 is placed 6.8mm away from IC power pin 4",
        rule_id="DECOUPLING_PROXIMITY",
        components=["U1", "C3"],
        nets=["+3V3", "GND"],
        coordinates=[Coordinate(x=50.0, y=25.0)],
        rationale="High loop inductance degrades high-frequency switching transient decoupling",
        recommendation="Relocate C3 to within 3.5mm of pin 4",
    )


@pytest.fixture
def sample_audit_result(
    sample_critical_finding: Finding, sample_warning_finding: Finding
) -> AuditResult:
    return AuditResult.create(
        project_path="projects/demo/demo.kicad_pro",
        findings=[sample_critical_finding, sample_warning_finding],
        tool_version="0.1.0",
        duration_seconds=1.234,
        fail_on=Severity.CRITICAL,
    )
