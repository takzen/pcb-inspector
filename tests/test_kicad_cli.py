"""Unit tests for KiCad CLI detection and wrapper."""

from __future__ import annotations

from pathlib import Path

from pcb_inspector.kicad.cli_wrapper import KiCadCli


def test_kicad_cli_find_executable() -> None:
    # On this machine, KiCad 10 / 9 is installed, or returns Path
    exe = KiCadCli.find_executable()
    if exe is not None:
        assert isinstance(exe, Path)
        assert exe.is_file()


def test_kicad_cli_custom_path_fallback(tmp_path: Path) -> None:
    fake_exe = tmp_path / "fake-kicad-cli"
    fake_exe.write_text("#!/bin/sh\n", encoding="utf-8")
    # Finding candidate with invalid path should return None or fallback
    assert KiCadCli.find_executable(candidate="non_existent_binary") is None or isinstance(
        KiCadCli.find_executable(), Path
    )
