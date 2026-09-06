"""Unit tests for VisionReviewRule."""

from __future__ import annotations

from pathlib import Path

import pytest

from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.rules.registry import default_registry
from pcb_inspector.rules.vision_review import VisionReviewRule

SAMPLE_PCB = """(kicad_pcb (version 20240108) (generator pcbnew)
  (general (thickness 1.6))
  (footprint "Package_QFP:LQFP-48" (layer "F.Cu") (at 50 50 0)
    (property "Reference" "U1")
    (pad "1" smd rect (at -3.5 0) (size 0.3 1.2) (layers "F.Cu") (net 1 "VCC"))
  )
)"""


def test_vision_rule_disabled_by_default(tmp_path: Path) -> None:
    pcb_file = tmp_path / "board.kicad_pcb"
    pcb_file.write_text(SAMPLE_PCB, encoding="utf-8")

    cfg = InspectorConfig()
    assert cfg.enable_vision is False

    rule = VisionReviewRule()
    findings = rule.evaluate(pcb_file, cfg)
    assert findings == []


def test_vision_rule_enabled_mock(tmp_path: Path) -> None:
    pcb_file = tmp_path / "board.kicad_pcb"
    pcb_file.write_text(SAMPLE_PCB, encoding="utf-8")

    cfg = InspectorConfig(enable_vision=True, vision_model="mock")
    rule = VisionReviewRule()
    findings = rule.evaluate(pcb_file, cfg)

    assert len(findings) >= 1
    for f in findings:
        assert f.rule_id == "VISION-AI-001"


def test_vision_rule_missing_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pcb_file = tmp_path / "board.kicad_pcb"
    pcb_file.write_text(SAMPLE_PCB, encoding="utf-8")

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    cfg = InspectorConfig(enable_vision=True, vision_model="gemini-3.8-flash")
    rule = VisionReviewRule()
    findings = rule.evaluate(pcb_file, cfg)

    assert len(findings) == 1
    assert findings[0].id == "VIS-NO-API-KEY"


def test_vision_rule_in_registry() -> None:
    rule = default_registry.get("VISION-AI-001")
    assert rule is not None
    assert isinstance(rule, VisionReviewRule)
