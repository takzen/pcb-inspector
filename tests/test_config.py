"""Unit tests for configuration loading and defaults."""

from __future__ import annotations

from pathlib import Path

from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.core.models import Severity


def test_config_defaults() -> None:
    cfg = InspectorConfig()
    assert cfg.enable_drc is True
    assert cfg.enable_erc is True
    assert cfg.enable_heuristics is True
    assert cfg.enable_vision is False
    assert cfg.max_decoupling_distance_mm == 3.5
    assert cfg.fail_on == Severity.CRITICAL


def test_config_load_from_yaml(tmp_path: Path) -> None:
    config_file = tmp_path / "custom-config.yaml"
    config_file.write_text(
        """
        max_decoupling_distance_mm: 2.0
        min_power_trace_width_mm: 0.5
        fail_on: WARNING
        enable_vision: true
        """,
        encoding="utf-8",
    )

    cfg = InspectorConfig.load(config_file)
    assert cfg.max_decoupling_distance_mm == 2.0
    assert cfg.min_power_trace_width_mm == 0.5
    assert cfg.fail_on == Severity.WARNING
    assert cfg.enable_vision is True


def test_config_load_missing_file_returns_default() -> None:
    cfg = InspectorConfig.load(Path("non_existent_file_xyz.yaml"))
    assert cfg.max_decoupling_distance_mm == 3.5
