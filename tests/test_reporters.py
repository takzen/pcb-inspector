"""Unit tests for report exporters."""

from __future__ import annotations

import json
from pathlib import Path

from pcb_inspector.core.models import AuditResult
from pcb_inspector.reporters.json_reporter import JsonReporter
from pcb_inspector.reporters.markdown_reporter import MarkdownReporter
from pcb_inspector.reporters.terminal_reporter import TerminalReporter


def test_json_reporter(sample_audit_result: AuditResult, tmp_path: Path) -> None:
    reporter = JsonReporter()
    output_str = reporter.render(sample_audit_result)
    data = json.loads(output_str)

    assert data["project_path"] == "projects/demo/demo.kicad_pro"
    assert data["summary"]["total_findings"] == 2
    assert len(data["findings"]) == 2

    file_path = tmp_path / "report.json"
    saved = reporter.write_to_file(sample_audit_result, file_path)
    assert saved.exists()
    assert json.loads(saved.read_text(encoding="utf-8")) == data


def test_markdown_reporter(sample_audit_result: AuditResult, tmp_path: Path) -> None:
    reporter = MarkdownReporter()
    output_str = reporter.render(sample_audit_result)

    assert "PCB Inspection Report" in output_str
    assert "DRC-001-SHORT-U1-GND" in output_str
    assert "DEC-002-U1-C3" in output_str
    assert "Engineering Rationale" in output_str
    assert "Actionable Recommendation" in output_str

    file_path = tmp_path / "report.md"
    saved = reporter.write_to_file(sample_audit_result, file_path)
    assert saved.exists()


def test_terminal_reporter(sample_audit_result: AuditResult) -> None:
    reporter = TerminalReporter()
    # Ensure it prints without raising any encoding or formatting errors
    reporter.print_result(sample_audit_result)


def test_html_reporter(sample_audit_result: AuditResult, tmp_path: Path) -> None:
    from pcb_inspector.reporters.html_reporter import HtmlReporter

    reporter = HtmlReporter()
    output_str = reporter.render(sample_audit_result)

    assert "<!DOCTYPE html>" in output_str
    assert "DRC-001-SHORT-U1-GND" in output_str
    assert "DEC-002-U1-C3" in output_str
    assert "Health Score" in output_str

    file_path = tmp_path / "report.html"
    saved = reporter.write_to_file(sample_audit_result, file_path)
    assert saved.exists()
    assert saved.read_text(encoding="utf-8") == output_str

