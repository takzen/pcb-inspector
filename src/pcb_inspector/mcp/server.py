"""Built-in Model Context Protocol (MCP) server for pcb-inspector.

Exposes tools, resources, and prompts for autonomous AI design agents (e.g. Konnect, Claude, Antigravity)
to execute deterministic DRC, spatial heuristics, multimodal vision audits, and closed-loop repairs.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer

from pcb_inspector import __version__
from pcb_inspector.core.models import ActionableFix, FindingCategory
from pcb_inspector.mcp.tools import (
    check_decoupling_tool,
    get_actionable_fixes_tool,
    inspect_project_tool,
    run_drc_tool,
)
from pcb_inspector.rules.registry import default_registry


def create_mcp_server() -> MCPServer:
    """Create and configure the pcb-inspector MCPServer instance."""
    server = MCPServer(
        name="pcb-inspector",
        instructions=(
            "pcb-inspector is an automated 3-layer design review and linter engine for KiCad projects.\n"
            "Layer 1: Deterministic KiCad native DRC and ERC via kicad-cli.\n"
            "Layer 2: Spatial, geometric, and physical heuristics (decoupling proximity, return paths, diff pairs, switching loops, power traces).\n"
            "Layer 3: Multimodal visual inspection via frontier vision AI.\n"
            "Use 'inspect_project' for a comprehensive audit, 'get_actionable_fixes' for self-repair loops, "
            "'check_decoupling' for rapid bypass capacitor checks, and 'run_drc' for native DRC verification."
        ),
        version=__version__,
    )

    # -------------------------------------------------------------------------
    # Tools
    # -------------------------------------------------------------------------
    @server.tool(
        name="inspect_project",
        description=(
            "Run a comprehensive 3-layer audit (deterministic DRC/ERC, spatial heuristics, "
            "multimodal vision AI) on a KiCad project. Returns structured findings, layout health score (0-100), "
            "and actionable repair proposals."
        ),
    )
    def inspect_project(
        project_path: str,
        fail_on: str = "CRITICAL",
        enable_vision: bool = False,
        vision_model: str = "gemini-3.8-flash",
        config_path: str | None = None,
    ) -> dict[str, Any]:
        return inspect_project_tool(
            project_path=project_path,
            fail_on=fail_on,
            enable_vision=enable_vision,
            vision_model=vision_model,
            config_path=config_path,
        )

    @server.tool(
        name="check_decoupling",
        description=(
            "Rapidly verify that every IC power pin has a high-frequency bypass/decoupling capacitor "
            "placed within the maximum allowable distance to prevent parasitic trace inductance."
        ),
    )
    def check_decoupling(
        pcb_path: str,
        max_distance_mm: float = 3.5,
        config_path: str | None = None,
    ) -> dict[str, Any]:
        return check_decoupling_tool(
            pcb_path=pcb_path,
            max_distance_mm=max_distance_mm,
            config_path=config_path,
        )

    @server.tool(
        name="run_drc",
        description=(
            "Execute native KiCad Design Rule Check (DRC) and Electrical Rule Check (ERC) "
            "via kicad-cli and return parsed violations with coordinates."
        ),
    )
    def run_drc(
        pcb_path: str,
        config_path: str | None = None,
    ) -> dict[str, Any]:
        return run_drc_tool(
            pcb_path=pcb_path,
            config_path=config_path,
        )

    @server.tool(
        name="get_actionable_fixes",
        description=(
            "Retrieve a prioritized list of machine-executable ActionableFix items with precise coordinates (X, Y), "
            "layer, and target component/net for autonomous AI closed-loop layout repair."
        ),
    )
    def get_actionable_fixes(
        project_path: str,
        enable_vision: bool = False,
        vision_model: str = "gemini-3.8-flash",
        config_path: str | None = None,
    ) -> list[dict[str, Any]]:
        return get_actionable_fixes_tool(
            project_path=project_path,
            enable_vision=enable_vision,
            vision_model=vision_model,
            config_path=config_path,
        )

    # -------------------------------------------------------------------------
    # Resources
    # -------------------------------------------------------------------------
    @server.resource("rules://list")
    def list_rules_resource() -> str:
        """Return registered inspection rules and their metadata."""
        rules = default_registry.list_rules()
        data = [
            {
                "rule_id": r.rule_id,
                "name": r.name,
                "category": r.category.value,
                "default_severity": r.default_severity.value,
                "description": r.description,
            }
            for r in rules
        ]
        return json.dumps(data, indent=2)

    @server.resource("rules://categories")
    def list_categories_resource() -> str:
        """Return available finding categories."""
        cats = [c.value for c in FindingCategory]
        return json.dumps(cats, indent=2)

    @server.resource("schema://actionable_fix")
    def actionable_fix_schema_resource() -> str:
        """Return JSON schema specification for ActionableFix structures."""
        schema = ActionableFix.model_json_schema()
        return json.dumps(schema, indent=2)

    # -------------------------------------------------------------------------
    # Prompts
    # -------------------------------------------------------------------------
    @server.prompt("pcb_design_review")
    def pcb_design_review_prompt(project_path: str) -> str:
        """Prompt template for performing an end-to-end design review."""
        return (
            f"You are an expert PCB layout engineer. Please perform an automated design audit "
            f"on the KiCad project located at '{project_path}'.\n\n"
            f"Step 1: Call `inspect_project(project_path='{project_path}', enable_vision=False)`.\n"
            f"Step 2: Examine findings across Layer 1 (DRC/ERC) and Layer 2 (Spatial Heuristics).\n"
            f"Step 3: Analyze the health score and summarize critical risks (power delivery, ground integrity, skew).\n"
            f"Step 4: Call `get_actionable_fixes(project_path='{project_path}')` and formulate a concrete repair sequence."
        )

    @server.prompt("pcb_repair_loop")
    def pcb_repair_loop_prompt(project_path: str) -> str:
        """Prompt template for autonomous closed-loop PCB repairs."""
        return (
            f"You are an autonomous KiCad PCB repair agent.\n"
            f"Target project: '{project_path}'\n\n"
            f"1. Fetch the exact fixes needed: `get_actionable_fixes(project_path='{project_path}')`.\n"
            f"2. For each fix in order of severity (CRITICAL first):\n"
            f"   - Relocate bypass capacitors or components to suggested_coordinates.\n"
            f"   - Widen power traces or tune differential pair skew to recommended parameters.\n"
            f"3. Re-run `inspect_project` to verify all violations have been eliminated and health_score is 100%."
        )

    return server


def run_mcp_server(transport: Literal["stdio", "sse", "streamable-http"] = "stdio") -> None:
    """Start the MCP server using the specified transport (default stdio)."""
    server = create_mcp_server()
    server.run(transport=transport)
