"""Parsers for KiCad ERC and DRC reports (JSON & text formats)."""

from __future__ import annotations

import json
import re
from typing import Any

from pcb_inspector.core.models import Coordinate, Finding, FindingCategory, Severity

# Regex patterns to extract components and nets from KiCad descriptions
REFDES_RE = re.compile(r"\b([A-Z]{1,3}\d+)\b")
NET_RE = re.compile(r"\[([^\]\n]+)\]")


def map_kicad_severity(severity_str: str) -> Severity:
    """Map KiCad report severity to pcb-inspector Severity."""
    s = severity_str.lower().strip()
    if s in ("error", "fatal", "critical"):
        return Severity.CRITICAL
    if s in ("warning", "warn"):
        return Severity.WARNING
    if s in ("exclusion", "info", "note"):
        return Severity.SUGGESTION
    return Severity.WARNING


def extract_refdes(text: str) -> list[str]:
    """Extract candidate component reference designators from a description string."""
    matches = REFDES_RE.findall(text)
    # Filter out common false positives like layer names or common units
    ignored = {"MM", "MIL", "IN", "VCC", "GND", "ERC", "DRC", "PCB", "SCH", "F", "B"}
    unique_refs: list[str] = []
    for m in matches:
        if m not in ignored and m not in unique_refs:
            unique_refs.append(m)
    return unique_refs


def extract_nets(text: str) -> list[str]:
    """Extract net names from brackets in KiCad item descriptions."""
    matches = NET_RE.findall(text)
    unique_nets: list[str] = []
    for m in matches:
        net = m.strip()
        if net and net not in unique_nets:
            unique_nets.append(net)
    return unique_nets


def extract_coordinates(items: list[dict[str, Any]]) -> list[Coordinate]:
    """Extract coordinates from a list of violation items."""
    coords: list[Coordinate] = []
    for it in items:
        pos = it.get("pos")
        if isinstance(pos, dict) and "x" in pos and "y" in pos:
            try:
                coords.append(Coordinate(x=float(pos["x"]), y=float(pos["y"])))
            except (ValueError, TypeError):
                continue
    return coords


def parse_drc_json(report_input: str | dict[str, Any]) -> list[Finding]:
    """Parse a KiCad DRC JSON report into a list of standardized Findings."""
    if isinstance(report_input, str):
        if not report_input.strip():
            return []
        data: dict[str, Any] = json.loads(report_input)
    else:
        data = report_input

    findings: list[Finding] = []

    # 1. Parse standard violations
    violations = data.get("violations", [])
    for idx, v in enumerate(violations, start=1):
        v_type = v.get("type", "unknown_violation")
        desc = v.get("description", "DRC violation")
        sev_str = v.get("severity", "error")
        items = v.get("items", [])

        # Gather context
        all_item_desc = " ".join(str(it.get("description", "")) for it in items)
        combined_text = f"{desc} {all_item_desc}"
        comps = extract_refdes(combined_text)
        nets = extract_nets(combined_text)
        coords = extract_coordinates(items)

        finding_id = f"DRC-{v_type.upper()}-{idx:03d}"
        findings.append(
            Finding(
                id=finding_id,
                title=f"DRC: {desc}",
                severity=map_kicad_severity(sev_str),
                category=FindingCategory.DRC_ERC,
                description=desc,
                rule_id=f"KICAD_DRC_{v_type.upper()}",
                components=comps,
                nets=nets,
                coordinates=coords,
                rationale="Native KiCad DRC violation indicating geometric or electrical design rule breach.",
                recommendation=f"Resolve '{v_type}' in KiCad PCB layout editor.",
                raw_data=v,
            )
        )

    # 2. Parse unconnected items
    unconnected = data.get("unconnected_items", [])
    for idx, u in enumerate(unconnected, start=1):
        desc = u.get("description", "Unconnected net/pad")
        items = u.get("items", [])
        all_item_desc = " ".join(str(it.get("description", "")) for it in items)
        combined_text = f"{desc} {all_item_desc}"

        findings.append(
            Finding(
                id=f"DRC-UNCONNECTED-{idx:03d}",
                title=f"DRC Unconnected: {desc}",
                severity=Severity.CRITICAL,
                category=FindingCategory.DRC_ERC,
                description=desc,
                rule_id="KICAD_DRC_UNCONNECTED",
                components=extract_refdes(combined_text),
                nets=extract_nets(combined_text),
                coordinates=extract_coordinates(items),
                rationale="Unconnected tracks or pads cause open circuits and board failure.",
                recommendation="Route the missing connection in PCB layout.",
                raw_data=u,
            )
        )

    # 3. Parse schematic parity issues
    parity = data.get("schematic_parity", [])
    for idx, p in enumerate(parity, start=1):
        desc = p.get("description", "Schematic parity mismatch")
        findings.append(
            Finding(
                id=f"DRC-PARITY-{idx:03d}",
                title=f"DRC Parity Mismatch: {desc}",
                severity=Severity.WARNING,
                category=FindingCategory.DRC_ERC,
                description=desc,
                rule_id="KICAD_DRC_PARITY",
                components=extract_refdes(desc),
                nets=extract_nets(desc),
                rationale="Discrepancy between schematic netlist and PCB layout.",
                recommendation="Update PCB from schematic ('F8' in KiCad).",
                raw_data=p,
            )
        )

    return findings


def parse_erc_json(report_input: str | dict[str, Any]) -> list[Finding]:
    """Parse a KiCad ERC JSON report into a list of standardized Findings."""
    if isinstance(report_input, str):
        if not report_input.strip():
            return []
        data: dict[str, Any] = json.loads(report_input)
    else:
        data = report_input

    findings: list[Finding] = []
    sheets = data.get("sheets", [])

    idx = 1
    for sheet in sheets:
        sheet_path = sheet.get("path", "/")
        violations = sheet.get("violations", [])

        for v in violations:
            v_type = v.get("type", "unknown_violation")
            desc = v.get("description", "ERC violation")
            sev_str = v.get("severity", "error")
            items = v.get("items", [])

            all_item_desc = " ".join(str(it.get("description", "")) for it in items)
            combined_text = f"{desc} {all_item_desc}"
            comps = extract_refdes(combined_text)
            nets = extract_nets(combined_text)
            coords = extract_coordinates(items)

            finding_id = f"ERC-{v_type.upper()}-{idx:03d}"
            findings.append(
                Finding(
                    id=finding_id,
                    title=f"ERC [{sheet_path}]: {desc}",
                    severity=map_kicad_severity(sev_str),
                    category=FindingCategory.DRC_ERC,
                    description=f"{desc} (Sheet: {sheet_path})",
                    rule_id=f"KICAD_ERC_{v_type.upper()}",
                    components=comps,
                    nets=nets,
                    coordinates=coords,
                    rationale="Native KiCad ERC violation indicating electrical conflict or dangling pins.",
                    recommendation=f"Resolve electrical connection '{v_type}' in schematic editor.",
                    raw_data=v,
                )
            )
            idx += 1

    return findings
