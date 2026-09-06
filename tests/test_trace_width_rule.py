"""Unit tests for PowerTraceWidthRule."""

from __future__ import annotations

from pathlib import Path

from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.rules.trace_width import PowerTraceWidthRule


def test_power_trace_width_detection(tmp_path: Path) -> None:
    # Net +5V has trace width 0.15mm (< min 0.3mm)
    pcb_content = """(kicad_pcb (version 20240108) (generator pcbnew)
      (net 0 "") (net 1 "+5V") (net 2 "GND")
      (segment (start 10 10) (end 20 10) (width 0.15) (layer "F.Cu") (net 1))
      (segment (start 10 20) (end 20 20) (width 0.50) (layer "F.Cu") (net 1))
    )"""
    p = tmp_path / "width.kicad_pcb"
    p.write_text(pcb_content, encoding="utf-8")

    rule = PowerTraceWidthRule()
    cfg = InspectorConfig(min_power_trace_width_mm=0.30)
    findings = rule.evaluate(p, cfg)

    assert len(findings) == 1
    f = findings[0]
    assert "+5V" in f.nets
    assert "0.15 mm" in f.description
    assert f.rule_id == "HEUR-PWR-001"
