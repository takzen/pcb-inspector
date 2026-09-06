"""JSON report exporter for machine consumption and AI agent loops."""

from __future__ import annotations

import json
from typing import Any

from pcb_inspector.core.models import AuditResult
from pcb_inspector.reporters.base import BaseReporter


class JsonReporter(BaseReporter):
    """Produces structured JSON reports."""

    def __init__(self, indent: int = 2) -> None:
        self.indent = indent

    def render(self, result: AuditResult) -> str:
        """Serialize AuditResult to JSON string."""
        data: dict[str, Any] = result.model_dump(mode="json")
        return json.dumps(data, indent=self.indent, ensure_ascii=False)
