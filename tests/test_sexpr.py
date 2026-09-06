"""Unit tests for S-Expression parser."""

from __future__ import annotations

import pytest

from pcb_inspector.core.exceptions import ProjectParsingError
from pcb_inspector.kicad.sexpr_parser import (
    find_all,
    find_first,
    get_value,
    parse_sexpr,
    tokenize,
)


def test_tokenize_basic() -> None:
    text = '(kicad_pcb (version 20240108) (generator "pcbnew"))'
    tokens = tokenize(text)
    assert tokens == ["(", "kicad_pcb", "(", "version", "20240108", ")", "(", "generator", '"pcbnew"', ")", ")"]


def test_parse_sexpr_nested() -> None:
    text = '(kicad_pcb (version 20240108) (general (thickness 1.6)))'
    parsed = parse_sexpr(text)
    assert parsed == ["kicad_pcb", ["version", "20240108"], ["general", ["thickness", "1.6"]]]


def test_find_first_and_find_all() -> None:
    text = """
    (kicad_pcb
      (footprint "Capacitor_SMD:C_0603" (at 10 20) (property "Reference" "C1"))
      (footprint "Capacitor_SMD:C_0603" (at 30 40) (property "Reference" "C2"))
      (generator "pcbnew")
    )
    """
    parsed = parse_sexpr(text)
    gen = find_first(parsed, "generator")
    assert gen == ["generator", "pcbnew"]
    assert get_value(gen, 1) == "pcbnew"

    footprints = find_all(parsed, "footprint")
    assert len(footprints) == 2
    assert footprints[0][1] == "Capacitor_SMD:C_0603"
    assert footprints[1][1] == "Capacitor_SMD:C_0603"


def test_parse_sexpr_mismatched_parentheses() -> None:
    with pytest.raises(ProjectParsingError, match="Unclosed parentheses"):
        parse_sexpr("(kicad_pcb (version 2024)")

    with pytest.raises(ProjectParsingError, match="Mismatched closing parenthesis"):
        parse_sexpr("(kicad_pcb))")
