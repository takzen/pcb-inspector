"""Consistency checks for the user-facing documentation."""

from __future__ import annotations

from pathlib import Path

import pytest

from pcb_inspector.rules.registry import init_default_registry

ROOT = Path(__file__).parent.parent


@pytest.mark.parametrize("name", ["README.md", "MANUAL.md"])
def test_code_fences_are_balanced(name: str) -> None:
    """MANUAL.md left its CI example unclosed, so the whole MCP section below it
    rendered as one code block."""
    lines = (ROOT / name).read_text(encoding="utf-8").splitlines()
    fences = [line for line in lines if line.startswith("```")]
    assert len(fences) % 2 == 0, f"{name} has an unclosed code block"


def test_manual_catalogue_lists_exactly_the_registered_rules() -> None:
    """The catalogue documented KICAD-DRC-001 and KICAD-ERC-001, which never
    existed; the registry has one combined KICAD-DRC-ERC-001."""
    manual = (ROOT / "MANUAL.md").read_text(encoding="utf-8")
    catalogue = manual[manual.index("## 5. Active Inspection Rules") : manual.index("## 6.")]
    registered = {rule.rule_id for rule in init_default_registry().list_rules()}

    documented = {
        cell.strip("` ")
        for row in catalogue.splitlines()
        if row.startswith("| `")
        for cell in [row.split("|")[1]]
    }
    assert documented == registered
