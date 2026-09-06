"""Base interfaces and models for multimodal vision inspection."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.core.models import Finding


class BaseVisionReviewer(ABC):
    """Abstract interface for Multimodal Vision LLM reviewers."""

    @abstractmethod
    def review_renders(
        self,
        image_paths: list[Path],
        context: dict[str, Any],
        config: InspectorConfig,
    ) -> list[Finding]:
        """Submit rendered 2D/3D PCB images to the vision model and return findings."""
        raise NotImplementedError
