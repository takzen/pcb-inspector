"""Unit tests for domain models and data structures."""

from __future__ import annotations

from pcb_inspector.core.models import (
    AuditResult,
    AuditSummary,
    Coordinate,
    Finding,
    FindingCategory,
    Severity,
)


def test_severity_properties() -> None:
    assert Severity.CRITICAL.badge_emoji == "🔴"
    assert Severity.WARNING.badge_emoji == "🟠"
    assert Severity.SUGGESTION.badge_emoji == "🟡"
    assert Severity.PASS.badge_emoji == "🟢"


def test_coordinate_formatting() -> None:
    coord_with_layer = Coordinate(x=10.56, y=20.12, layer="F.Cu", rotation=45.0)
    assert str(coord_with_layer) == "(10.56, 20.12 mm) [F.Cu]"

    coord_simple = Coordinate(x=5.0, y=10.0)
    assert str(coord_simple) == "(5.00, 10.00 mm)"


def test_finding_creation_and_serialization(sample_critical_finding: Finding) -> None:
    assert sample_critical_finding.id == "DRC-001-SHORT-U1-GND"
    assert sample_critical_finding.severity == Severity.CRITICAL
    assert sample_critical_finding.category == FindingCategory.DRC_ERC
    assert sample_critical_finding.components == ["U1", "C1"]
    assert sample_critical_finding.nets == ["VCC", "GND"]

    # Serialization roundtrip
    dumped = sample_critical_finding.model_dump()
    reloaded = Finding.model_validate(dumped)
    assert reloaded == sample_critical_finding


def test_audit_summary_calculation(sample_critical_finding: Finding, sample_warning_finding: Finding) -> None:
    # Fail on CRITICAL: should fail because 1 critical finding exists
    summary = AuditSummary.calculate(
        findings=[sample_critical_finding, sample_warning_finding],
        duration_seconds=0.5,
        fail_on=Severity.CRITICAL,
    )
    assert summary.total_findings == 2
    assert summary.critical_count == 1
    assert summary.warning_count == 1
    assert summary.passed is False

    # Fail on CRITICAL with only WARNING findings: should pass
    summary_warn_only = AuditSummary.calculate(
        findings=[sample_warning_finding],
        duration_seconds=0.2,
        fail_on=Severity.CRITICAL,
    )
    assert summary_warn_only.passed is True

    # Fail on WARNING with 1 warning finding: should fail
    summary_warn_fail = AuditSummary.calculate(
        findings=[sample_warning_finding],
        duration_seconds=0.2,
        fail_on=Severity.WARNING,
    )
    assert summary_warn_fail.passed is False


def test_audit_result_create(sample_audit_result: AuditResult) -> None:
    assert sample_audit_result.project_path == "projects/demo/demo.kicad_pro"
    assert sample_audit_result.tool_version == "0.1.0"
    assert len(sample_audit_result.findings) == 2
    assert sample_audit_result.summary.total_findings == 2
