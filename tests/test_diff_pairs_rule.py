"""Unit tests for DifferentialPairSkewRule."""

from __future__ import annotations

from pathlib import Path

from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.rules.differential_pairs import DifferentialPairSkewRule


def test_diff_pair_skew_detection(tmp_path: Path) -> None:
    # USB_D+ is 10mm long, USB_D- is 12mm long (skew 2.0mm > 0.15mm)
    pcb_content = """(kicad_pcb (version 20240108) (generator pcbnew)
      (net 0 "") (net 1 "USB_D+") (net 2 "USB_D-")
      (segment (start 10 10) (end 20 10) (width 0.2) (layer "F.Cu") (net 1))
      (segment (start 10 12) (end 22 12) (width 0.2) (layer "F.Cu") (net 2))
    )"""
    p = tmp_path / "diff.kicad_pcb"
    p.write_text(pcb_content, encoding="utf-8")

    rule = DifferentialPairSkewRule()
    cfg = InspectorConfig(max_diff_pair_skew_mm=0.15)
    findings = rule.evaluate(p, cfg)

    assert len(findings) == 1
    f = findings[0]
    assert "USB_D" in f.title
    assert "2.00 mm" in f.description
    assert f.rule_id == "HEUR-DIFF-001"
