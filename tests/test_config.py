"""Unit tests for configuration loading and defaults."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.core.exceptions import ConfigError
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


def test_explicit_missing_config_raises() -> None:
    """A named config that does not exist used to fall back to defaults silently,
    auditing against thresholds the user had not chosen."""
    with pytest.raises(ConfigError, match="not found"):
        InspectorConfig.load(Path("non_existent_file_xyz.yaml"))


def test_absent_discovered_config_still_means_defaults(tmp_path: Path, monkeypatch) -> None:
    """Discovery is optional: no file found is not an error."""
    monkeypatch.chdir(tmp_path)
    cfg = InspectorConfig.load(project_dir=tmp_path)
    assert cfg.max_decoupling_distance_mm == 3.5


def test_invalid_yaml_raises(tmp_path: Path) -> None:
    bad = tmp_path / "c.yaml"
    bad.write_text("max_decoupling_distance_mm: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid YAML"):
        InspectorConfig.load(bad)


def test_non_mapping_yaml_raises(tmp_path: Path) -> None:
    bad = tmp_path / "c.yaml"
    bad.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="mapping"):
        InspectorConfig.load(bad)


def test_wrong_type_raises(tmp_path: Path) -> None:
    bad = tmp_path / "c.yaml"
    bad.write_text("max_decoupling_distance_mm: close\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="max_decoupling_distance_mm"):
        InspectorConfig.load(bad)


def test_unknown_key_is_reported_not_silently_dropped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A misspelt threshold used to vanish and quietly become the default."""
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text("max_decoupling_distanse_mm: 1.0\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        cfg = InspectorConfig.load(cfg_file)

    assert cfg.max_decoupling_distance_mm == 3.5
    assert "max_decoupling_distanse_mm" in caplog.text


def test_empty_config_file_is_defaults(tmp_path: Path) -> None:
    empty = tmp_path / "c.yaml"
    empty.write_text("", encoding="utf-8")
    assert InspectorConfig.load(empty).max_decoupling_distance_mm == 3.5


def test_shipped_rules_yaml_loads_without_unknown_keys(caplog: pytest.LogCaptureFixture) -> None:
    root = Path(__file__).parent.parent
    for name in ("rules.yaml", ".pcb-inspector.yaml.example"):
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            InspectorConfig.from_file(root / name)
        assert "unknown setting" not in caplog.text, f"{name}: {caplog.text}"


def test_config_load_project_dir_override(tmp_path: Path) -> None:
    proj_dir = tmp_path / "my_project"
    proj_dir.mkdir()
    override_file = proj_dir / ".pcb-inspector.yaml"
    override_file.write_text(
        """
        max_decoupling_distance_mm: 1.8
        fail_on: SUGGESTION
        """,
        encoding="utf-8",
    )

    cfg = InspectorConfig.load(project_dir=proj_dir)
    assert cfg.max_decoupling_distance_mm == 1.8
    assert cfg.fail_on == Severity.SUGGESTION



def test_every_yaml_example_in_the_manual_is_valid(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The manual documented two config keys that never existed and a wrong
    default. This keeps its examples honest against the real schema."""
    import re

    manual = (Path(__file__).parent.parent / "MANUAL.md").read_text(encoding="utf-8")
    # Only the configuration sections: later ones hold GitHub Actions workflows,
    # which are YAML but not pcb-inspector configs.
    config_sections = manual[
        manual.index("## 3. Configuration System") : manual.index("## 5. Active Inspection Rules")
    ]
    blocks = re.findall(r"```yaml\n(.*?)```", config_sections, re.DOTALL)
    assert len(blocks) >= 3, "expected the manual's config examples"

    for i, block in enumerate(blocks):
        cfg_file = tmp_path / f"example_{i}.yaml"
        cfg_file.write_text(block, encoding="utf-8")
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            InspectorConfig.from_file(cfg_file)
        assert "unknown setting" not in caplog.text, f"MANUAL.md YAML block {i}: {caplog.text}"
