"""Unit and integration tests for KiCad CLI wrapper."""

from __future__ import annotations

from pathlib import Path

import pytest

from pcb_inspector.core.exceptions import KiCadCliNotFoundError
from pcb_inspector.kicad.cli_wrapper import KiCadCli


def test_kicad_cli_find_executable() -> None:
    exe = KiCadCli.find_executable()
    if exe is not None:
        assert isinstance(exe, Path)
        assert exe.is_file()


def test_kicad_cli_not_found_raises() -> None:
    with pytest.raises(KiCadCliNotFoundError):
        KiCadCli(executable_path="completely_bogus_binary_path_xyz123")


def test_kicad_cli_get_version() -> None:
    exe = KiCadCli.find_executable()
    if exe is None:
        pytest.skip("KiCad CLI not installed on this system")

    cli = KiCadCli(exe)
    ver = cli.get_version()
    assert len(ver) > 0


def test_kicad_cli_real_drc_execution(tmp_path: Path) -> None:
    exe = KiCadCli.find_executable()
    if exe is None:
        pytest.skip("KiCad CLI not installed on this system")

    cli = KiCadCli(exe)

    # Minimal PCB with deliberate missing Edge.Cuts (will produce a DRC violation)
    pcb_content = """(kicad_pcb (version 20240108) (generator pcbnew)
  (general (thickness 1.6))
  (layers
    (0 "F.Cu" signal)
    (31 "B.Cu" signal)
    (40 "Edge.Cuts" user)
  )
  (net 0 "")
  (net 1 "GND")
  (segment (start 10 10) (end 20 10) (width 0.25) (layer "F.Cu") (net 1))
)"""
    pcb_path = tmp_path / "board.kicad_pcb"
    pcb_path.write_bytes(pcb_content.encode("utf-8"))

    findings = cli.execute_drc(pcb_path)
    assert isinstance(findings, list)
    assert len(findings) > 0
    # At least invalid_outline or track_dangling should be detected
    rule_ids = [f.rule_id for f in findings]
    assert any("INVALID_OUTLINE" in r or "TRACK_DANGLING" in r for r in rule_ids)
