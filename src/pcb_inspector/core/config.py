"""Configuration management for pcb-inspector."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from pcb_inspector.core.models import Severity


class InspectorConfig(BaseModel):
    """Global configuration settings and rule thresholds for pcb-inspector."""

    # Execution controls
    kicad_cli_path: str | None = Field(
        default=None, description="Explicit path to kicad-cli binary if not in PATH"
    )
    enable_drc: bool = Field(default=True, description="Run KiCad Design Rules Check")
    enable_erc: bool = Field(default=True, description="Run KiCad Electrical Rules Check")
    enable_heuristics: bool = Field(
        default=True, description="Run programmatic geometric & spatial heuristic rules"
    )
    enable_vision: bool = Field(
        default=False, description="Run multimodal visual inspection with LLM"
    )

    # Heuristic thresholds
    max_decoupling_distance_mm: float = Field(
        default=3.5,
        description="Maximum allowed distance between IC power pin and decoupling capacitor",
    )
    min_power_trace_width_mm: float = Field(
        default=0.3,
        description="Minimum recommended trace width for high current / power nets (mm)",
    )
    max_diff_pair_skew_mm: float = Field(
        default=0.15,
        description="Maximum allowable trace length mismatch within a differential pair (mm)",
    )

    # Vision settings
    vision_model: str = Field(
        default="gemini-3.8-flash", description="Model identifier for visual inspection"
    )
    vision_api_key_env: str = Field(
        default="GEMINI_API_KEY", description="Environment variable holding the Vision API key"
    )

    # Reporting and behavior
    fail_on: Severity = Field(
        default=Severity.CRITICAL,
        description="Threshold severity causing the process exit code to fail (non-zero)",
    )
    output_dir: str = Field(
        default="reports", description="Default directory where generated reports are stored"
    )
    custom_rules: dict[str, Any] = Field(
        default_factory=dict, description="Arbitrary custom rule parameters"
    )

    @classmethod
    def load(cls, config_path: Path | str | None = None) -> InspectorConfig:
        """Load configuration from a YAML file, or default if None or file not found."""
        if config_path is None:
            # Check default candidate paths
            for candidate in (
                Path(".pcb-inspector.yaml"),
                Path(".pcb-inspector.yml"),
                Path("pcb-inspector.yaml"),
                Path("rules.yaml"),
                Path("rules.yml"),
            ):
                if candidate.exists():
                    config_path = candidate
                    break

        if config_path is None:
            return cls()

        path = Path(config_path)
        if not path.exists():
            return cls()

        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        return cls(**data)
