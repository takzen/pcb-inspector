"""Unit tests for HtmlReporter."""

from __future__ import annotations

from pathlib import Path

from pcb_inspector.core.models import (
    ActionableFix,
    AuditResult,
    Coordinate,
    Finding,
    FindingCategory,
    Severity,
)
from pcb_inspector.reporters.html_reporter import HtmlReporter


def test_html_reporter_empty_findings(tmp_path: Path) -> None:
    result = AuditResult.create(
        project_path="demo.kicad_pcb",
        findings=[],
        duration_seconds=0.12,
    )
    reporter = HtmlReporter()
    html_content = reporter.render(result)

    assert "<!DOCTYPE html>" in html_content
    assert "demo.kicad_pcb" in html_content
    assert "Health Score" in html_content
    assert "100" in html_content
    assert "All inspection checks passed" in html_content

    # Test file writing
    out_file = tmp_path / "report.html"
    reporter.write_to_file(result, out_file)
    assert out_file.exists()
    assert out_file.read_text(encoding="utf-8") == html_content


def test_html_reporter_with_findings() -> None:
    fix = ActionableFix(
        action_type="RELOCATE_COMPONENT",
        component="C1",
        description="Move C1 closer to U1",
    )
    f = Finding(
        id="VIS-001",
        title="Visual Acid Trap",
        severity=Severity.WARNING,
        category=FindingCategory.VISION,
        description="Sharp track entering pad",
        rule_id="VISION-AI-001",
        components=["U1", "C1"],
        nets=["+3V3"],
        coordinates=[Coordinate(x=25.4, y=12.7, layer="F.Cu")],
        rationale="Etchant accumulation",
        recommendation="Reroute track with 45 degree bevels",
        correlated_with=["DRC-002"],
        actionable_fix=fix,
    )
    result = AuditResult.create(
        project_path="hardware/rev_a.kicad_pcb",
        findings=[f],
        duration_seconds=1.45,
    )
    reporter = HtmlReporter()
    html_content = reporter.render(result)

    assert "VIS-001" in html_content
    assert "Visual Acid Trap" in html_content
    assert "U1" in html_content
    assert "C1" in html_content
    assert "+3V3" in html_content
    assert "25.40, 12.70 mm" in html_content
    assert "Etchant accumulation" in html_content
    assert "Reroute track with 45 degree bevels" in html_content
    assert "DRC-002" in html_content
    assert "RELOCATE_COMPONENT" in html_content
    assert "filterSev" in html_content
