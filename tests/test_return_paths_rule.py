"""Unit tests for GroundPlaneIntegrityRule."""

from __future__ import annotations

from pathlib import Path

from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.rules.return_paths import GroundPlaneIntegrityRule


def test_missing_ground_plane_detection(tmp_path: Path) -> None:
    # 6 tracks but no ground zone on board
    tracks_str = "\n".join(
        f'(segment (start 10 {i * 5}) (end 30 {i * 5}) (width 0.25) (layer "F.Cu") (net 1 "SIG{i}"))'
        for i in range(1, 7)
    )
    pcb_content = f"""(kicad_pcb (version 20240108) (generator pcbnew)
      (net 0 "") (net 1 "GND")
      {tracks_str}
    )"""
    p = tmp_path / "no_gnd.kicad_pcb"
    p.write_text(pcb_content, encoding="utf-8")

    rule = GroundPlaneIntegrityRule()
    cfg = InspectorConfig()
    findings = rule.evaluate(p, cfg)

    assert len(findings) == 1
    assert "No ground plane" in findings[0].title
    assert findings[0].rule_id == "HEUR-GND-001"
