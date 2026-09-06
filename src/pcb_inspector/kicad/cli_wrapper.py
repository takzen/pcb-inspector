"""KiCad CLI discovery and execution wrapper."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from pcb_inspector.core.exceptions import KiCadCliExecutionError, KiCadCliNotFoundError


class KiCadCli:
    """Interface to kicad-cli for deterministic ERC, DRC, and exports."""

    DEFAULT_WINDOWS_PATHS = [
        Path(r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe"),
        Path(r"C:\Program Files\KiCad\9.0\bin\kicad-cli.exe"),
        Path(r"C:\Program Files\KiCad\8.0\bin\kicad-cli.exe"),
        Path(r"C:\Program Files (x86)\KiCad\10.0\bin\kicad-cli.exe"),
        Path(r"C:\Program Files (x86)\KiCad\9.0\bin\kicad-cli.exe"),
        Path(r"C:\Program Files (x86)\KiCad\8.0\bin\kicad-cli.exe"),
    ]

    DEFAULT_MACOS_PATHS = [
        Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"),
        Path("/Applications/KiCad 10.0/KiCad.app/Contents/MacOS/kicad-cli"),
        Path("/Applications/KiCad 9.0/KiCad.app/Contents/MacOS/kicad-cli"),
        Path("/Applications/KiCad 8.0/KiCad.app/Contents/MacOS/kicad-cli"),
    ]

    DEFAULT_LINUX_PATHS = [
        Path("/usr/bin/kicad-cli"),
        Path("/usr/local/bin/kicad-cli"),
    ]

    def __init__(self, executable_path: str | Path | None = None) -> None:
        self.executable = self._resolve_executable(executable_path)

    @classmethod
    def find_executable(cls, candidate: str | Path | None = None) -> Path | None:
        """Search PATH and platform-standard locations for kicad-cli."""
        if candidate:
            p = Path(candidate)
            if p.is_file() and os.access(p, os.X_OK):
                return p

        # Check system PATH
        in_path = shutil.which("kicad-cli")
        if in_path:
            return Path(in_path)

        candidates = cls.DEFAULT_WINDOWS_PATHS + cls.DEFAULT_MACOS_PATHS + cls.DEFAULT_LINUX_PATHS
        for p in candidates:
            if p.exists() and p.is_file():
                return p

        return None

    def _resolve_executable(self, candidate: str | Path | None) -> Path:
        resolved = self.find_executable(candidate)
        if not resolved:
            raise KiCadCliNotFoundError(
                "kicad-cli executable was not found. Ensure KiCad 8+ is installed or provide the path."
            )
        return resolved

    def get_version(self) -> str:
        """Return the kicad-cli version string."""
        cmd = [str(self.executable), "version"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return res.stdout.strip()
        except (subprocess.SubprocessError, OSError) as err:
            raise KiCadCliExecutionError(f"Failed to query kicad-cli version: {err}") from err

    def run_erc(
        self,
        schematic_path: Path | str,
        output_report: Path | str | None = None,
        as_json: bool = True,
    ) -> str:
        """Run Electrical Rules Check (ERC) on a schematic file."""
        cmd = [str(self.executable), "sch", "erc"]
        if as_json:
            cmd.extend(["--format", "json"])
        if output_report:
            cmd.extend(["--output", str(output_report)])
        cmd.append(str(schematic_path))

        res = subprocess.run(cmd, capture_output=True, text=True)
        # kicad-cli may exit with non-zero if ERC violations are found;
        # stdout contains the JSON report regardless.
        return res.stdout

    def run_drc(
        self,
        pcb_path: Path | str,
        output_report: Path | str | None = None,
        as_json: bool = True,
    ) -> str:
        """Run Design Rules Check (DRC) on a PCB layout file."""
        cmd = [str(self.executable), "pcb", "drc"]
        if as_json:
            cmd.extend(["--format", "json"])
        if output_report:
            cmd.extend(["--output", str(output_report)])
        cmd.append(str(pcb_path))

        res = subprocess.run(cmd, capture_output=True, text=True)
        return res.stdout
