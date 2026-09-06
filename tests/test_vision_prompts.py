"""Unit tests for vision prompts and structured output parsing."""

from __future__ import annotations

from pcb_inspector.core.models import FindingCategory, Severity
from pcb_inspector.vision.prompts import (
    build_vision_prompt,
    parse_vision_response,
)


def test_build_vision_prompt() -> None:
    prompt = build_vision_prompt(
        view_name="top",
        board_context={"footprints_count": 12, "tracks_count": 45, "components": ["U1", "C1"]},
    )
    assert "TOP view" in prompt
    assert "Total Footprints: 12" in prompt
    assert "Total Tracks: 45" in prompt
    assert "Key Components: U1, C1" in prompt


def test_parse_vision_response_valid_json() -> None:
    json_text = """
    {
      "inspected_view": "top",
      "summary": "Found minor silkscreen defects",
      "findings": [
        {
          "id": "VIS-SILK-001",
          "title": "Missing Pin 1 Indicator",
          "severity": "WARNING",
          "category": "SILKSCREEN",
          "components": ["U1"],
          "nets": [],
          "coordinates": {"x": 50.0, "y": 50.0, "layer": "F.Silkscreen"},
          "description": "IC U1 lacks a visible Pin 1 silkscreen dot.",
          "rationale": "Without Pin 1 marker, assembly technicians cannot verify orientation.",
          "recommendation": "Add a visible silkscreen dot adjacent to pin 1."
        }
      ]
    }
    """
    findings = parse_vision_response(json_text)
    assert len(findings) == 1
    f = findings[0]
    assert f.id == "VIS-SILK-001"
    assert f.title == "Missing Pin 1 Indicator"
    assert f.severity == Severity.WARNING
    assert f.category == FindingCategory.SILKSCREEN
    assert f.components == ["U1"]
    assert len(f.coordinates) == 1
    assert f.coordinates[0].x == 50.0
    assert f.coordinates[0].y == 50.0


def test_parse_vision_response_markdown_codeblock() -> None:
    wrapped_text = """
    Here are the visual review findings:
    ```json
    {
      "inspected_view": "top",
      "findings": [
        {
          "id": "VIS-ACID-001",
          "title": "Acute angle trace",
          "severity": "CRITICAL",
          "category": "VISION",
          "description": "Severe acid trap detected."
        }
      ]
    }
    ```
    Please review the above issues carefully.
    """
    findings = parse_vision_response(wrapped_text)
    assert len(findings) == 1
    assert findings[0].id == "VIS-ACID-001"
    assert findings[0].severity == Severity.CRITICAL


def test_parse_vision_response_malformed() -> None:
    assert parse_vision_response("") == []
    assert parse_vision_response("This is not JSON at all.") == []
    assert parse_vision_response('{"invalid": true}') == []
