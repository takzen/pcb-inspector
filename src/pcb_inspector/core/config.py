"""Configuration management for pcb-inspector."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError

from pcb_inspector.core.exceptions import ConfigError
from pcb_inspector.core.models import Severity

logger = logging.getLogger(__name__)


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
    require_kicad_cli: bool = Field(
        default=False,
        description=(
            "Treat a missing kicad-cli as a CRITICAL finding instead of a WARNING. "
            "Recommended in CI, where a silently skipped Layer 1 would otherwise pass."
        ),
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
        default="gemini-3.8-flash",
        description="Model identifier for visual inspection (gemini-3.8-flash, fable-5, gpt-6-astra)",
    )
    vision_api_key_env: str | None = Field(
        default=None,
        description=(
            "Environment variable holding the vision API key. Leave unset to use the "
            "selected provider's standard variable (GEMINI_API_KEY, OPENAI_API_KEY, or "
            "ANTHROPIC_API_KEY). It used to default to GEMINI_API_KEY for every provider, "
            "so choosing an OpenAI model with only OPENAI_API_KEY set was refused."
        ),
    )
    vision_cache_dir: str = Field(
        default=".pcb_vision_cache",
        description="Directory used to cache multimodal vision model inspection results",
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
    def load(
        cls,
        config_path: Path | str | None = None,
        project_dir: Path | str | None = None,
    ) -> InspectorConfig:
        """Load configuration from YAML with per-project override support.

        Resolution hierarchy:
        1. Explicitly provided `config_path` via CLI flag (-c / --config)
        2. Per-project override in `project_dir` (.pcb-inspector.yaml / rules.yaml)
        3. Global / root directory configuration (rules.yaml / .pcb-inspector.yaml)
        4. Built-in defaults
        """
        if config_path is not None:
            # A path the user named must exist. Falling back to defaults here
            # meant a typo in --config silently audited against the built-in
            # thresholds while the user believed their own were in force.
            path = Path(config_path)
            if not path.is_file():
                raise ConfigError(f"Configuration file not found: {path}")
            return cls.from_file(path)

        candidates: list[Path] = []
        if project_dir is not None:
            p_dir = Path(project_dir)
            for name in (".pcb-inspector.yaml", ".pcb-inspector.yml", "pcb-inspector.yaml", "rules.yaml"):
                candidates.append(p_dir / name)

        for name in (".pcb-inspector.yaml", ".pcb-inspector.yml", "pcb-inspector.yaml", "rules.yaml", "rules.yml"):
            candidates.append(Path(name))

        # Discovered files are optional, so absence falls through to defaults;
        # a discovered file that is present but broken still raises.
        for candidate in candidates:
            if candidate.is_file():
                return cls.from_file(candidate)

        return cls()

    @classmethod
    def from_file(cls, path: Path) -> InspectorConfig:
        """Parse one YAML configuration file.

        Raises:
            ConfigError: If the file is unreadable, not YAML, not a mapping, or
                holds values of the wrong type.
        """
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except OSError as err:
            raise ConfigError(f"Cannot read configuration file {path}: {err}") from err
        except yaml.YAMLError as err:
            raise ConfigError(f"Configuration file {path} is not valid YAML: {err}") from err

        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ConfigError(
                f"Configuration file {path} must contain a mapping of settings, "
                f"not a {type(data).__name__}."
            )

        # Unknown keys are reported rather than rejected, so a config written
        # for a newer version still loads. They used to vanish silently, which
        # turned a misspelt threshold into the default without a word.
        unknown = sorted(set(data) - set(cls.model_fields))
        if unknown:
            logger.warning(
                "Ignoring unknown setting(s) in %s: %s", path, ", ".join(map(str, unknown))
            )
            data = {k: v for k, v in data.items() if k in cls.model_fields}

        try:
            return cls(**data)
        except ValidationError as err:
            raise ConfigError(f"Invalid configuration in {path}:\n{err}") from err
