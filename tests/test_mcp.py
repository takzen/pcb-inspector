"""Unit tests for the MCP (Model Context Protocol) server, tools, and resources."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pcb_inspector.mcp import (
    check_decoupling_tool,
    create_mcp_server,
    get_actionable_fixes_tool,
    inspect_project_tool,
    run_drc_tool,
)


@pytest.fixture
def sample_board(tmp_path: Path) -> Path:
    pcb_file = tmp_path / "board.kicad_pcb"
    pcb_file.write_text(
        """(kicad_pcb (version 20240108) (generator "test")
  (general (thickness 1.6))
  (net 0 "")
  (net 1 "+3V3")
  (net 2 "GND")
  (footprint "Package_SO:SOIC-8" (layer "F.Cu")
    (at 100 100)
    (property "Reference" "U1")
    (property "Value" "MCU")
    (pad "1" smd rect (at -1.9 -1.27) (size 1.5 0.6) (layers "F.Cu") (net 1 "+3V3") (pinfunction "VDD"))
    (pad "2" smd rect (at -1.9 0) (size 1.5 0.6) (layers "F.Cu") (net 2 "GND") (pinfunction "VSS"))
  )
)""",
        encoding="utf-8",
    )
    return pcb_file


def test_inspect_project_tool_success(sample_board: Path) -> None:
    res = inspect_project_tool(str(sample_board), fail_on="CRITICAL", enable_vision=False)
    assert res["project_path"] == str(sample_board)
    assert "passed" in res
    assert "health_score" in res
    assert "summary" in res
    assert "findings" in res
    assert "actionable_fixes" in res
    assert res["summary"]["total"] >= 0


def test_inspect_project_tool_nonexistent(tmp_path: Path) -> None:
    res = inspect_project_tool(str(tmp_path / "nonexistent.kicad_pcb"))
    assert res["passed"] is False
    assert "does not exist" in res["error"]
    assert res["findings"] == []


def test_check_decoupling_tool(sample_board: Path) -> None:
    res = check_decoupling_tool(str(sample_board), max_distance_mm=3.5)
    assert res["pcb_path"] == str(sample_board)
    assert "passed" in res
    assert "violation_count" in res
    assert "findings" in res
    assert "actionable_fixes" in res
    # U1 has power pin VDD with no bypass capacitor placed, so it should report decoupling violation
    assert res["violation_count"] >= 1
    assert len(res["actionable_fixes"]) >= 1


def test_check_decoupling_tool_nonexistent(tmp_path: Path) -> None:
    res = check_decoupling_tool(str(tmp_path / "missing.kicad_pcb"))
    assert res["passed"] is False
    assert "error" in res


def test_run_drc_tool(sample_board: Path) -> None:
    res = run_drc_tool(str(sample_board))
    assert res["path"] == str(sample_board)
    assert "passed" in res
    assert "total_violations" in res
    assert "findings" in res


def test_run_drc_tool_nonexistent(tmp_path: Path) -> None:
    res = run_drc_tool(str(tmp_path / "missing.kicad_pcb"))
    assert res["passed"] is False
    assert "error" in res


def test_get_actionable_fixes_tool(sample_board: Path) -> None:
    fixes = get_actionable_fixes_tool(str(sample_board), enable_vision=False)
    assert isinstance(fixes, list)
    if fixes:
        fix = fixes[0]
        assert "action_type" in fix
        assert "description" in fix


def test_mcp_server_metadata() -> None:
    server = create_mcp_server()
    assert server.name == "pcb-inspector"
    assert server.instructions is not None
    assert "3-layer" in server.instructions


def test_mcp_server_call_tool(sample_board: Path) -> None:
    from mcp.types import CallToolResult

    async def _test() -> None:
        server = create_mcp_server()
        res = await server.call_tool("check_decoupling", {"pcb_path": str(sample_board)})
        assert isinstance(res, CallToolResult)
        assert not res.is_error
        assert len(res.content) > 0
        text = getattr(res.content[0], "text", "{}")
        payload = json.loads(text)
        assert payload["pcb_path"] == str(sample_board)
        assert "violation_count" in payload

    import asyncio
    asyncio.run(_test())


def test_mcp_server_resources() -> None:
    async def _test() -> None:
        server = create_mcp_server()
        resources = await server.list_resources()
        uris = [str(r.uri) for r in resources]
        assert "rules://list" in uris
        assert "rules://categories" in uris
        assert "schema://actionable_fix" in uris

        # Read rules://list
        rule_res = await server.read_resource("rules://list")
        rule_items = list(rule_res)
        rule_data = json.loads(str(getattr(rule_items[0], "content", "")))
        assert isinstance(rule_data, list)
        rule_ids = [r["rule_id"] for r in rule_data]
        assert "HEUR-DEC-001" in rule_ids

        # Read rules://categories
        cat_res = await server.read_resource("rules://categories")
        cat_items = list(cat_res)
        cats = json.loads(str(getattr(cat_items[0], "content", "")))
        assert "DRC_ERC" in cats
        assert "DECOUPLING" in cats

        # Read schema://actionable_fix
        schema_res = await server.read_resource("schema://actionable_fix")
        schema_items = list(schema_res)
        schema = json.loads(str(getattr(schema_items[0], "content", "")))
        assert "properties" in schema
        assert "action_type" in schema["properties"]

    import asyncio
    asyncio.run(_test())


def test_mcp_server_prompts() -> None:
    async def _test() -> None:
        server = create_mcp_server()
        prompts = await server.list_prompts()
        prompt_names = [p.name for p in prompts]
        assert "pcb_design_review" in prompt_names
        assert "pcb_repair_loop" in prompt_names

        p1 = await server.get_prompt("pcb_design_review", {"project_path": "/tmp/test.kicad_pro"})
        assert "inspect_project" in str(p1)

        p2 = await server.get_prompt("pcb_repair_loop", {"project_path": "/tmp/test.kicad_pro"})
        assert "get_actionable_fixes" in str(p2)

    import asyncio
    asyncio.run(_test())
