"""Inspection prompts and structured response parsing for Multimodal Vision AI."""

from __future__ import annotations

import json
import re
from typing import Any

from pcb_inspector.core.models import (
    Coordinate,
    Finding,
    FindingCategory,
    Severity,
)

SYSTEM_PROMPT_VISION = """You are an expert Senior Hardware Design & PCB Quality Engineer performing automated Design-for-Manufacturing (DFM) and layout reviews.

You are inspecting rendered 2D vector layouts (copper, pads, vias, and silkscreen layers) of a printed circuit board.

Carefully inspect the image for the following potential flaws:
1. **Silkscreen & Polarity Markings**:
   - Diodes and LEDs: Are cathode markings (bars, lines) clear and unambiguous?
   - Polarized capacitors (electrolytic, tantalum): Are polarity (+ / -) markings visible?
   - Integrated Circuits (ICs): Does every multi-pin IC have a visible Pin 1 indicator (dot, notch, or tick mark) outside the package body?
   - Reference Designators: Are labels (e.g. R1, C1, U1) clearly legible, not hidden directly underneath component bodies, and not clipped by drilled vias or board cutouts?
2. **Routing & Geometric Sanity**:
   - Acute angle traces (<90°, acid traps) that can trap chemical etchant during PCB fabrication.
   - Unnecessary serpentine routing or awkward detours on sensitive nets.
   - High-voltage or noisy switching pads placed unnecessarily close to low-voltage signals.
3. **Mechanical Clearances & Assembly**:
   - Component bodies encroaching on or overlapping board edges (Edge.Cuts).
   - Insufficient clearance around mounting holes (risk of screw heads or washers crushing nearby SMD components).
   - Connectors facing inward instead of outward toward the board edge.

You MUST respond strictly with a valid JSON object following this exact schema:
{
  "inspected_view": "top" | "bottom",
  "summary": "Brief 1-2 sentence overall visual assessment",
  "findings": [
    {
      "id": "VIS-<CATEGORY>-<SEQ>",
      "title": "Clear, concise issue title",
      "severity": "CRITICAL" | "WARNING" | "SUGGESTION",
      "category": "VISION" | "SILKSCREEN" | "PLACEMENT" | "MECHANICAL" | "SIGNAL_INTEGRITY",
      "components": ["U1"],
      "nets": [],
      "coordinates": {"x": 12.3, "y": 45.6, "layer": "F.Silkscreen"},
      "description": "Specific observation of the visual defect.",
      "rationale": "Underlying engineering reason why this is a risk during assembly or operation.",
      "recommendation": "Concrete, actionable step for the layout engineer to fix this issue."
    }
  ]
}
If no issues are found, return an empty "findings" array: {"inspected_view": "...", "summary": "No visual defects detected.", "findings": []}.
Do not include any conversational preamble or text outside of the JSON object.
"""


def build_vision_prompt(view_name: str = "top", board_context: dict[str, Any] | None = None) -> str:
    """Build user prompt for multimodal inspection with optional board metadata context."""
    prompt_lines = [
        f"Inspect the attached rendered {view_name.upper()} view of the printed circuit board layout.",
    ]

    if board_context:
        prompt_lines.append("\nBoard Design Metadata:")
        if "footprints_count" in board_context:
            prompt_lines.append(f"- Total Footprints: {board_context['footprints_count']}")
        if "tracks_count" in board_context:
            prompt_lines.append(f"- Total Tracks: {board_context['tracks_count']}")
        if "components" in board_context:
            comps = ", ".join(board_context["components"][:20])
            prompt_lines.append(f"- Key Components: {comps}")

    prompt_lines.append("\nReturn your inspection findings strictly formatted as the required JSON object.")
    return "\n".join(prompt_lines)


def parse_vision_response(
    raw_text: str, default_rule_id: str = "VISION-AI-001"
) -> list[Finding]:
    """Parse structured JSON response from vision LLM into validated Finding objects."""
    if not raw_text or not raw_text.strip():
        return []

    # Strip markdown code fences if model wrapped response in ```json ... ```
    text = raw_text.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if match:
        text = match.group(1).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Fallback regex extraction of JSON object if there's trailing narrative
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                data = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return []
        else:
            return []

    if not isinstance(data, dict):
        return []

    raw_findings = data.get("findings", [])
    if not isinstance(raw_findings, list):
        return []

    findings: list[Finding] = []
    for idx, item in enumerate(raw_findings):
        if not isinstance(item, dict):
            continue

        fid = str(item.get("id", f"VIS-AI-{idx + 1:03d}"))
        title = str(item.get("title", "Visual layout inspection finding"))

        # Parse Severity
        raw_sev = str(item.get("severity", "WARNING")).upper()
        try:
            severity = Severity(raw_sev)
        except ValueError:
            severity = Severity.WARNING

        # Parse Category
        raw_cat = str(item.get("category", "VISION")).upper()
        try:
            category = FindingCategory(raw_cat)
        except ValueError:
            category = FindingCategory.VISION

        # Parse Coordinates
        coords: list[Coordinate] = []
        raw_coord = item.get("coordinates")
        if isinstance(raw_coord, dict):
            try:
                coords.append(
                    Coordinate(
                        x=float(raw_coord.get("x", 0.0)),
                        y=float(raw_coord.get("y", 0.0)),
                        layer=str(raw_coord.get("layer", "F.Cu")),
                    )
                )
            except (ValueError, TypeError):
                pass

        finding = Finding(
            id=fid,
            title=title,
            severity=severity,
            category=category,
            description=str(item.get("description", "")),
            rule_id=default_rule_id,
            components=[str(c) for c in item.get("components", []) if isinstance(c, (str, int))],
            nets=[str(n) for n in item.get("nets", []) if isinstance(n, str)],
            coordinates=coords,
            rationale=str(item.get("rationale", "")),
            recommendation=str(item.get("recommendation", "")),
        )
        findings.append(finding)

    return findings
