"""Core data models and domain types for pcb-inspector."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Severity(str, Enum):
    """Severity levels for inspection findings."""

    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    SUGGESTION = "SUGGESTION"
    PASS = "PASS"

    @property
    def badge_emoji(self) -> str:
        match self:
            case Severity.CRITICAL:
                return "🔴"
            case Severity.WARNING:
                return "🟠"
            case Severity.SUGGESTION:
                return "🟡"
            case Severity.PASS:
                return "🟢"


class FindingCategory(str, Enum):
    """Categorization of findings by discipline and domain."""

    DRC_ERC = "DRC_ERC"
    DECOUPLING = "DECOUPLING"
    POWER_DELIVERY = "POWER_DELIVERY"
    SIGNAL_INTEGRITY = "SIGNAL_INTEGRITY"
    THERMAL = "THERMAL"
    PLACEMENT = "PLACEMENT"
    SILKSCREEN = "SILKSCREEN"
    MECHANICAL = "MECHANICAL"
    VISION = "VISION"


class Coordinate(BaseModel):
    """2D / layer-aware PCB coordinate point in millimeters."""

    model_config = ConfigDict(frozen=True)

    x: float = Field(..., description="X position in mm")
    y: float = Field(..., description="Y position in mm")
    layer: str | None = Field(default=None, description="KiCad layer name e.g. F.Cu, B.Cu")
    rotation: float | None = Field(default=None, description="Orientation in degrees")

    def __str__(self) -> str:
        layer_str = f" [{self.layer}]" if self.layer else ""
        return f"({self.x:.2f}, {self.y:.2f} mm){layer_str}"


class ActionableFix(BaseModel):
    """Machine-readable actionable fix specification for automated agent self-correction loops."""

    action_type: str = Field(
        ...,
        description="Type of repair action: RELOCATE_COMPONENT, WIDEN_TRACE, REROUTE_NET, ADD_PIN1_MARKER, ADJUST_CLEARANCE",
    )
    component: str | None = Field(default=None, description="Target component designator")
    net: str | None = Field(default=None, description="Target net name")
    target_coordinates: Coordinate | None = Field(
        default=None, description="Recommended relocation coordinates"
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict, description="Numerical/contextual parameters (e.g. recommended_width_mm)"
    )
    description: str = Field(..., description="Human and agent-readable repair instruction")


class Finding(BaseModel):
    """Structured inspection finding produced by any verification layer."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str = Field(..., description="Unique deterministic identifier e.g. DEC-001-U1-C4")
    title: str = Field(..., description="Brief human-readable summary")
    severity: Severity = Field(..., description="Severity level")
    category: FindingCategory = Field(..., description="Engineering discipline category")
    description: str = Field(..., description="Detailed description of the issue")
    rule_id: str = Field(..., description="Machine-readable rule identifier")
    components: list[str] = Field(
        default_factory=list, description="Affected component designators (RefDes)"
    )
    nets: list[str] = Field(default_factory=list, description="Affected net names")
    coordinates: list[Coordinate] = Field(
        default_factory=list, description="Relevant board coordinates"
    )
    rationale: str | None = Field(
        default=None, description="Underlying engineering rationale / physics principle"
    )
    recommendation: str | None = Field(
        default=None, description="Actionable step to resolve the issue"
    )
    visual_snapshot: str | None = Field(
        default=None, description="Relative path or URI to an image snapshot highlighting the issue"
    )
    correlated_with: list[str] = Field(
        default_factory=list, description="IDs of correlated findings across layers"
    )
    actionable_fix: ActionableFix | None = Field(
        default=None, description="Machine-readable fix instruction for agents"
    )
    raw_data: dict[str, Any] = Field(
        default_factory=dict, description="Arbitrary raw context data from parser or DRC"
    )


class AuditSummary(BaseModel):
    """Aggregated numerical metrics for an audit run."""

    total_findings: int = 0
    critical_count: int = 0
    warning_count: int = 0
    suggestion_count: int = 0
    pass_count: int = 0
    health_score: float = 100.0
    passed: bool = True
    duration_seconds: float = 0.0

    @classmethod
    def calculate(
        cls,
        findings: list[Finding],
        duration_seconds: float = 0.0,
        fail_on: Severity = Severity.CRITICAL,
    ) -> AuditSummary:
        crit = sum(1 for f in findings if f.severity == Severity.CRITICAL)
        warn = sum(1 for f in findings if f.severity == Severity.WARNING)
        sugg = sum(1 for f in findings if f.severity == Severity.SUGGESTION)
        passed_cnt = sum(1 for f in findings if f.severity == Severity.PASS)

        if fail_on == Severity.CRITICAL:
            passed = crit == 0
        elif fail_on == Severity.WARNING:
            passed = crit == 0 and warn == 0
        else:
            passed = crit == 0 and warn == 0 and sugg == 0

        # Layout Health Score: 0 to 100
        penalty = (crit * 30.0) + (warn * 10.0) + (sugg * 2.0)
        score = max(0.0, round(100.0 - penalty, 1))

        return cls(
            total_findings=len(findings),
            critical_count=crit,
            warning_count=warn,
            suggestion_count=sugg,
            pass_count=passed_cnt,
            health_score=score,
            passed=passed,
            duration_seconds=round(duration_seconds, 3),
        )


class AuditResult(BaseModel):
    """Complete serialized result of an audit for reporting and agent loops."""

    project_path: str = Field(..., description="Path to the inspected KiCad project")
    tool_version: str = Field(..., description="pcb-inspector version")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of audit completion",
    )
    summary: AuditSummary = Field(..., description="Summary metrics")
    findings: list[Finding] = Field(default_factory=list, description="All audit findings")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Environment and execution metadata"
    )

    @classmethod
    def create(
        cls,
        project_path: str,
        findings: list[Finding],
        tool_version: str = "0.1.0",
        duration_seconds: float = 0.0,
        fail_on: Severity = Severity.CRITICAL,
        metadata: dict[str, Any] | None = None,
    ) -> AuditResult:
        summary = AuditSummary.calculate(
            findings=findings, duration_seconds=duration_seconds, fail_on=fail_on
        )
        return cls(
            project_path=project_path,
            tool_version=tool_version,
            summary=summary,
            findings=findings,
            metadata=metadata or {},
        )

    @property
    def health_score(self) -> float:
        """Layout health score (0-100) from summary."""
        return self.summary.health_score

    @property
    def actionable_fixes(self) -> list[ActionableFix]:
        """Return list of non-null actionable fixes for automated repair agents."""
        return [f.actionable_fix for f in self.findings if f.actionable_fix is not None]
