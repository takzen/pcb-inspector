"""Core domain models, configuration, and exceptions for pcb-inspector."""

from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.core.exceptions import (
    KiCadCliExecutionError,
    KiCadCliNotFoundError,
    PcbInspectorError,
    ProjectParsingError,
    RuleExecutionError,
    VisionReviewError,
)
from pcb_inspector.core.models import (
    AuditResult,
    AuditSummary,
    Coordinate,
    Finding,
    FindingCategory,
    Severity,
)

__all__ = [
    "AuditResult",
    "AuditSummary",
    "Coordinate",
    "Finding",
    "FindingCategory",
    "InspectorConfig",
    "KiCadCliExecutionError",
    "KiCadCliNotFoundError",
    "PcbInspectorError",
    "ProjectParsingError",
    "RuleExecutionError",
    "Severity",
    "VisionReviewError",
]
