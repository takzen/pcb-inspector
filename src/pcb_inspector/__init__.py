"""pcb-inspector: Automated Multimodal Design Reviewer & Linter for KiCad Projects."""

__version__ = "0.1.0"
__author__ = "Krzysztof Pika"

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
    "Severity",
    "__author__",
    "__version__",
]
