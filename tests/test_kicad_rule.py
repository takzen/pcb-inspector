"""Unit and integration tests for KiCadDrcErcRule."""

from __future__ import annotations

from pathlib import Path

from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.rules.kicad_drc_erc import KiCadDrcErcRule
from pcb_inspector.rules.registry import default_registry


def test_rule_properties() -> None:
    rule = KiCadDrcErcRule()
    assert rule.rule_id == "KICAD-DRC-ERC-001"
    assert "DRC" in rule.name


def test_rule_execution_with_real_pcb(tmp_path: Path) -> None:
    # Minimal PCB with no Edge.Cuts
    pcb_content = """(kicad_pcb (version 20240108) (generator pcbnew)
  (general (thickness 1.6))
  (layers
    (0 "F.Cu" signal)
    (31 "B.Cu" signal)
    (40 "Edge.Cuts" user)
  )
  (net 0 "")
)"""
    pcb_path = tmp_path / "sample.kicad_pcb"
    pcb_path.write_bytes(pcb_content.encode("utf-8"))

    cfg = InspectorConfig()
    rule = KiCadDrcErcRule()
    findings = rule.evaluate(pcb_path, cfg)

    assert isinstance(findings, list)
    # The default registry also includes this rule
    reg_findings = default_registry.evaluate_all(pcb_path, cfg)
    assert len(reg_findings) == len(findings)
