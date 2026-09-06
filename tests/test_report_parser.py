"""Unit tests for KiCad DRC and ERC report parser."""

from __future__ import annotations

from typing import Any

from pcb_inspector.core.models import FindingCategory, Severity
from pcb_inspector.kicad.report_parser import (
    extract_coordinates,
    extract_nets,
    extract_refdes,
    map_kicad_severity,
    parse_drc_json,
    parse_erc_json,
)


def test_map_kicad_severity() -> None:
    assert map_kicad_severity("error") == Severity.CRITICAL
    assert map_kicad_severity("fatal") == Severity.CRITICAL
    assert map_kicad_severity("warning") == Severity.WARNING
    assert map_kicad_severity("exclusion") == Severity.SUGGESTION
    assert map_kicad_severity("unknown") == Severity.WARNING


def test_extract_refdes_and_nets() -> None:
    text = "Pad 1 [VCC] of U1 on F.Cu is too close to Pad 2 [GND] of C14"
    refs = extract_refdes(text)
    assert "U1" in refs
    assert "C14" in refs

    nets = extract_nets(text)
    assert "VCC" in nets
    assert "GND" in nets


def test_extract_coordinates() -> None:
    items: list[dict[str, Any]] = [
        {"pos": {"x": 10.5, "y": 20.25}},
        {"pos": {"x": 30.0, "y": 40.0}},
        {"no_pos": True},
    ]
    coords = extract_coordinates(items)
    assert len(coords) == 2
    assert coords[0].x == 10.5
    assert coords[0].y == 20.25
    assert coords[1].x == 30.0
    assert coords[1].y == 40.0


def test_parse_drc_json_complete() -> None:
    drc_payload = {
        "$schema": "https://schemas.kicad.org/drc.v1.json",
        "kicad_version": "10.0.6",
        "violations": [
            {
                "description": "Clearance violation (netclass 'Default' clearance 0.2000 mm; actual 0.1000 mm)",
                "severity": "error",
                "type": "clearance",
                "items": [
                    {
                        "description": "Track [GND] on F.Cu",
                        "pos": {"x": 12.0, "y": 14.0},
                    },
                    {
                        "description": "Pad 1 [+5V] of U2 on F.Cu",
                        "pos": {"x": 12.1, "y": 14.0},
                    },
                ],
            },
            {
                "description": "Track has unconnected end",
                "severity": "warning",
                "type": "track_dangling",
                "items": [
                    {
                        "description": "Track [RESET] on B.Cu",
                        "pos": {"x": 5.0, "y": 5.0},
                    }
                ],
            },
        ],
        "unconnected_items": [
            {
                "description": "Missing connection between items",
                "items": [
                    {
                        "description": "Pad 3 [SCL] of U1 on F.Cu",
                        "pos": {"x": 50.0, "y": 50.0},
                    }
                ],
            }
        ],
        "schematic_parity": [
            {
                "description": "Component C99 in schematic is missing on PCB",
            }
        ],
    }

    findings = parse_drc_json(drc_payload)
    assert len(findings) == 4

    # First violation (clearance)
    f_clear = findings[0]
    assert f_clear.severity == Severity.CRITICAL
    assert f_clear.category == FindingCategory.DRC_ERC
    assert f_clear.rule_id == "KICAD_DRC_CLEARANCE"
    assert "U2" in f_clear.components
    assert "GND" in f_clear.nets
    assert "+5V" in f_clear.nets
    assert len(f_clear.coordinates) == 2

    # Second violation (dangling track)
    f_dang = findings[1]
    assert f_dang.severity == Severity.WARNING
    assert f_dang.rule_id == "KICAD_DRC_TRACK_DANGLING"
    assert "RESET" in f_dang.nets

    # Unconnected item
    f_unconn = findings[2]
    assert f_unconn.severity == Severity.CRITICAL
    assert f_unconn.rule_id == "KICAD_DRC_UNCONNECTED"
    assert "U1" in f_unconn.components
    assert "SCL" in f_unconn.nets

    # Parity mismatch
    f_parity = findings[3]
    assert f_parity.severity == Severity.WARNING
    assert f_parity.rule_id == "KICAD_DRC_PARITY"
    assert "C99" in f_parity.components


def test_parse_erc_json_complete() -> None:
    erc_payload = {
        "$schema": "https://schemas.kicad.org/erc.v1.json",
        "kicad_version": "10.0.6",
        "sheets": [
            {
                "path": "/PowerSheet/",
                "violations": [
                    {
                        "description": "Pin not connected",
                        "severity": "error",
                        "type": "pin_not_connected",
                        "items": [
                            {
                                "description": "Pin 1 [GND] of U3",
                                "pos": {"x": 100.0, "y": 150.0},
                            }
                        ],
                    }
                ],
            }
        ],
    }

    findings = parse_erc_json(erc_payload)
    assert len(findings) == 1
    f = findings[0]
    assert f.severity == Severity.CRITICAL
    assert f.rule_id == "KICAD_ERC_PIN_NOT_CONNECTED"
    assert "U3" in f.components
    assert "GND" in f.nets
    assert "/PowerSheet/" in f.title
