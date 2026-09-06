"""Tool implementations for pcb-inspector Model Context Protocol (MCP) server."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from pcb_inspector import __version__
from pcb_inspector.core.aggregator import FindingAggregator
from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.core.models import AuditResult, Severity
from pcb_inspector.rules.decoupling import DecouplingProximityRule
from pcb_inspector.rules.kicad_drc_erc import KiCadDrcErcRule
from pcb_inspector.rules.registry import default_registry


def _serialize_finding(finding: Any) -> dict[str, Any]:
    """Convert a Finding object into an agent-friendly dictionary."""
    coords = [
        {"x": c.x, "y": c.y, "layer": c.layer}
        for c in getattr(finding, "coordinates", [])
    ]
    return {
        "id": getattr(finding, "id", ""),
        "rule_id": getattr(finding, "rule_id", ""),
        "title": getattr(finding, "title", ""),
        "severity": getattr(finding.severity, "value", str(finding.severity)),
        "category": getattr(finding.category, "value", str(finding.category)),
        "description": getattr(finding, "description", ""),
        "components": getattr(finding, "components", []),
        "nets": getattr(finding, "nets", []),
        "coordinates": coords,
        "rationale": getattr(finding, "rationale", None),
        "recommendation": getattr(finding, "recommendation", None),
        "actionable_fix": (
            finding.actionable_fix.model_dump() if getattr(finding, "actionable_fix", None) else None
        ),
        "correlated_with": getattr(finding, "correlated_with", []),
    }


def inspect_project_tool(
    project_path: str,
    fail_on: str = "CRITICAL",
    enable_vision: bool = False,
    vision_model: str = "gemini-3.8-flash",
    config_path: str | None = None,
) -> dict[str, Any]:
    """Run a comprehensive 3-layer design audit (DRC, heuristics, vision) on a KiCad project.

    Args:
        project_path: Path to .kicad_pro, .kicad_pcb, .kicad_sch, or project directory.
        fail_on: Severity threshold that causes failure: CRITICAL, WARNING, or SUGGESTION.
        enable_vision: Whether to run Layer 3 multimodal vision AI inspection.
        vision_model: Vision LLM identifier (gemini-3.8-flash, fable-5, gpt-6-astra, mock).
        config_path: Optional path to custom .pcb-inspector.yaml configuration file.

    Returns:
        Structured audit report including pass/fail status, health score, findings, and actionable fixes.
    """
    start_time = time.perf_counter()
    p = Path(project_path)
    if not p.exists():
        return {
            "error": f"Path '{project_path}' does not exist.",
            "passed": False,
            "health_score": 0.0,
            "findings": [],
            "actionable_fixes": [],
        }

    try:
        threshold = Severity(fail_on.upper())
    except ValueError:
        threshold = Severity.CRITICAL

    project_dir = p if p.is_dir() else p.parent
    cfg = InspectorConfig.load(Path(config_path) if config_path else None, project_dir=project_dir)
    cfg.fail_on = threshold
    cfg.enable_vision = enable_vision
    cfg.vision_model = vision_model

    raw_findings = default_registry.evaluate_all(context=p, config=cfg)
    findings = FindingAggregator().aggregate(raw_findings)

    duration = time.perf_counter() - start_time
    result = AuditResult.create(
        project_path=str(p),
        findings=findings,
        tool_version=__version__,
        duration_seconds=duration,
        fail_on=threshold,
    )

    return {
        "project_path": str(p),
        "passed": result.summary.passed,
        "health_score": result.health_score,
        "summary": {
            "critical": result.summary.critical_count,
            "warning": result.summary.warning_count,
            "suggestion": result.summary.suggestion_count,
            "pass": result.summary.pass_count,
            "total": result.summary.total_findings,
            "duration_seconds": round(duration, 3),
        },
        "actionable_fixes": [fix.model_dump() for fix in result.actionable_fixes],
        "findings": [_serialize_finding(f) for f in result.findings],
    }


def check_decoupling_tool(
    pcb_path: str,
    max_distance_mm: float = 3.5,
    config_path: str | None = None,
) -> dict[str, Any]:
    """Perform rapid spatial verification of bypass/decoupling capacitors near IC power pins.

    Args:
        pcb_path: Path to the .kicad_pcb layout file or project directory.
        max_distance_mm: Maximum allowable placement distance in millimeters.
        config_path: Optional path to custom configuration file.

    Returns:
        Structured decoupling report with identified violations and actionable placement fixes.
    """
    p = Path(pcb_path)
    if not p.exists():
        return {"error": f"Path '{pcb_path}' does not exist.", "passed": False, "findings": []}

    project_dir = p if p.is_dir() else p.parent
    cfg = InspectorConfig.load(Path(config_path) if config_path else None, project_dir=project_dir)
    cfg.max_decoupling_distance_mm = max_distance_mm

    rule = DecouplingProximityRule()
    raw_findings = rule.evaluate(context=p, config=cfg)
    findings = FindingAggregator().aggregate(raw_findings)

    fixes = [f.actionable_fix.model_dump() for f in findings if f.actionable_fix]
    passed = len([f for f in findings if f.severity in (Severity.CRITICAL, Severity.WARNING)]) == 0

    return {
        "pcb_path": str(p),
        "passed": passed,
        "max_distance_mm": max_distance_mm,
        "violation_count": len(findings),
        "findings": [_serialize_finding(f) for f in findings],
        "actionable_fixes": fixes,
    }


def run_drc_tool(
    pcb_path: str,
    config_path: str | None = None,
) -> dict[str, Any]:
    """Execute native KiCad deterministic Design Rule Checking (DRC) and Electrical Rule Checking (ERC).

    Args:
        pcb_path: Path to the .kicad_pcb, .kicad_sch, or project directory.
        config_path: Optional path to custom configuration file.

    Returns:
        Report detailing DRC/ERC violations parsed from kicad-cli output.
    """
    p = Path(pcb_path)
    if not p.exists():
        return {"error": f"Path '{pcb_path}' does not exist.", "passed": False, "findings": []}

    project_dir = p if p.is_dir() else p.parent
    cfg = InspectorConfig.load(Path(config_path) if config_path else None, project_dir=project_dir)

    rule = KiCadDrcErcRule()
    raw_findings = rule.evaluate(context=p, config=cfg)
    findings = FindingAggregator().aggregate(raw_findings)

    fixes = [f.actionable_fix.model_dump() for f in findings if f.actionable_fix]
    critical_count = len([f for f in findings if f.severity == Severity.CRITICAL])
    warning_count = len([f for f in findings if f.severity == Severity.WARNING])

    return {
        "path": str(p),
        "passed": critical_count == 0,
        "critical_count": critical_count,
        "warning_count": warning_count,
        "total_violations": len(findings),
        "findings": [_serialize_finding(f) for f in findings],
        "actionable_fixes": fixes,
    }


def get_actionable_fixes_tool(
    project_path: str,
    enable_vision: bool = False,
    vision_model: str = "gemini-3.8-flash",
    config_path: str | None = None,
) -> list[dict[str, Any]]:
    """Retrieve machine-readable actionable fixes with precise coordinates for autonomous AI agent closed-loop repairs.

    Args:
        project_path: Path to KiCad project file (.kicad_pro), layout (.kicad_pcb), or directory.
        enable_vision: Whether to include vision AI findings in repair proposals.
        vision_model: Vision LLM identifier.
        config_path: Optional path to custom configuration file.

    Returns:
        List of ActionableFix dictionaries specifying component, target coordinates, layer, and rationale.
    """
    audit = inspect_project_tool(
        project_path=project_path,
        fail_on="SUGGESTION",
        enable_vision=enable_vision,
        vision_model=vision_model,
        config_path=config_path,
    )
    fixes = audit.get("actionable_fixes", [])
    if isinstance(fixes, list):
        return [f for f in fixes if isinstance(f, dict)]
    return []
