"""Unit tests for DecouplingProximityRule."""

from __future__ import annotations

from pathlib import Path

from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.core.models import Severity
from pcb_inspector.rules.decoupling import DecouplingProximityRule


def test_decoupling_close_passes(tmp_path: Path) -> None:
    # C1 is 2mm away from U1 power pin
    pcb_content = """(kicad_pcb (version 20240108) (generator pcbnew)
      (net 0 "") (net 1 "GND") (net 2 "+3V3")
      (footprint "LQFP" (layer "F.Cu") (at 50 50)
        (property "Reference" "U1")
        (pad "1" smd rect (at 0 0) (layers "F.Cu") (net 2 "+3V3"))
      )
      (footprint "C_0402" (layer "F.Cu") (at 52 50)
        (property "Reference" "C1")
        (pad "1" smd rect (at 0 0) (layers "F.Cu") (net 2 "+3V3"))
        (pad "2" smd rect (at 1 0) (layers "F.Cu") (net 1 "GND"))
      )
    )"""
    p = tmp_path / "close.kicad_pcb"
    p.write_text(pcb_content, encoding="utf-8")

    rule = DecouplingProximityRule()
    cfg = InspectorConfig(max_decoupling_distance_mm=3.5)
    findings = rule.evaluate(p, cfg)
    assert len(findings) == 0


def test_decoupling_far_fails(tmp_path: Path) -> None:
    # C1 is 20mm away from U1 power pin
    pcb_content = """(kicad_pcb (version 20240108) (generator pcbnew)
      (net 0 "") (net 1 "GND") (net 2 "+3V3")
      (footprint "LQFP" (layer "F.Cu") (at 50 50)
        (property "Reference" "U1")
        (pad "1" smd rect (at 0 0) (layers "F.Cu") (net 2 "+3V3"))
      )
      (footprint "C_0402" (layer "F.Cu") (at 70 50)
        (property "Reference" "C1")
        (pad "1" smd rect (at 0 0) (layers "F.Cu") (net 2 "+3V3"))
      )
    )"""
    p = tmp_path / "far.kicad_pcb"
    p.write_text(pcb_content, encoding="utf-8")

    rule = DecouplingProximityRule()
    cfg = InspectorConfig(max_decoupling_distance_mm=3.5)
    findings = rule.evaluate(p, cfg)
    assert len(findings) == 1
    f = findings[0]
    assert f.severity == Severity.CRITICAL
    assert "U1" in f.components
    assert "C1" in f.components
    assert "+3V3" in f.nets
    assert "20.00 mm" in f.description


def test_decoupling_missing_capacitor(tmp_path: Path) -> None:
    # U1 has +3V3 pin but no capacitors on +3V3 exist on board
    pcb_content = """(kicad_pcb (version 20240108) (generator pcbnew)
      (net 0 "") (net 1 "GND") (net 2 "+3V3")
      (footprint "LQFP" (layer "F.Cu") (at 50 50)
        (property "Reference" "U1")
        (pad "1" smd rect (at 0 0) (layers "F.Cu") (net 2 "+3V3"))
      )
    )"""
    p = tmp_path / "missing.kicad_pcb"
    p.write_text(pcb_content, encoding="utf-8")

    rule = DecouplingProximityRule()
    cfg = InspectorConfig()
    findings = rule.evaluate(p, cfg)
    assert len(findings) == 1
    assert findings[0].severity == Severity.CRITICAL
    assert "Missing decoupling capacitor" in findings[0].title
