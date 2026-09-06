"""Unit tests for SwitchingLoopGeometryRule."""

from __future__ import annotations

from pathlib import Path

from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.rules.switching_loops import SwitchingLoopGeometryRule


def test_switching_loop_area_detection(tmp_path: Path) -> None:
    # Large triangular switching loop spanning 0,0 to 10,0 to 0,10 (area = 50 mm² > 30 mm²)
    pcb_content = """(kicad_pcb (version 20240108) (generator pcbnew)
      (net 0 "") (net 1 "SW_NODE")
      (footprint "U_REG" (layer "F.Cu") (at 0 0)
        (property "Reference" "U1")
        (pad "1" smd rect (at 0 0) (layers "F.Cu") (net 1 "SW_NODE"))
      )
      (footprint "L_PWR" (layer "F.Cu") (at 10 0)
        (property "Reference" "L1")
        (pad "1" smd rect (at 0 0) (layers "F.Cu") (net 1 "SW_NODE"))
      )
      (footprint "D_PWR" (layer "F.Cu") (at 0 10)
        (property "Reference" "D1")
        (pad "1" smd rect (at 0 0) (layers "F.Cu") (net 1 "SW_NODE"))
      )
    )"""
    p = tmp_path / "sw.kicad_pcb"
    p.write_text(pcb_content, encoding="utf-8")

    rule = SwitchingLoopGeometryRule()
    cfg = InspectorConfig()
    findings = rule.evaluate(p, cfg)

    assert len(findings) == 1
    f = findings[0]
    assert "SW_NODE" in f.nets
    assert "50.0 mm²" in f.description
    assert f.rule_id == "HEUR-DCDC-001"
