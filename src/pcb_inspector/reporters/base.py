"""Base class for inspection report generators."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from pcb_inspector.core.models import AuditResult


class BaseReporter(ABC):
    """Abstract base class for all report exporters."""

    @abstractmethod
    def render(self, result: AuditResult) -> str:
        """Render the audit result into the target string format."""
        raise NotImplementedError

    def write_to_file(self, result: AuditResult, output_path: Path | str) -> Path:
        """Render and save report to the target file path."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        content = self.render(result)
        path.write_text(content, encoding="utf-8")
        return path
