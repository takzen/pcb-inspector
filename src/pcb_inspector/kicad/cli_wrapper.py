"""KiCad CLI discovery and execution wrapper."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from pcb_inspector.core.exceptions import KiCadCliExecutionError, KiCadCliNotFoundError

if TYPE_CHECKING:
    from pcb_inspector.core.models import Finding


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
        if candidate is not None:
            p = Path(candidate)
            if p.is_file() and os.access(p, os.X_OK):
                return p
            return None

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
        severity_all: bool = True,
    ) -> str:
        """Run Electrical Rules Check (ERC) on a schematic file and return JSON/text string."""
        sch_path = Path(schematic_path)
        if not sch_path.exists():
            raise FileNotFoundError(f"Schematic file not found: {sch_path}")

        temp_file: Path | None = None
        target_output = output_report
        if target_output is None:
            fd, tmp_name = tempfile.mkstemp(suffix=".json" if as_json else ".rpt")
            os.close(fd)
            temp_file = Path(tmp_name)
            target_output = temp_file

        cmd = [str(self.executable), "sch", "erc"]
        if as_json:
            cmd.extend(["--format", "json"])
        if severity_all:
            cmd.append("--severity-all")
        cmd.extend(["--output", str(target_output), str(sch_path)])

        try:
            subprocess.run(cmd, capture_output=True, text=True)
            if Path(target_output).exists():
                return Path(target_output).read_text(encoding="utf-8")
            return ""
        finally:
            if temp_file and temp_file.exists():
                temp_file.unlink(missing_ok=True)

    def run_drc(
        self,
        pcb_path: Path | str,
        output_report: Path | str | None = None,
        as_json: bool = True,
        severity_all: bool = True,
        units: str = "mm",
    ) -> str:
        """Run Design Rules Check (DRC) on a PCB layout file and return JSON/text string."""
        p_path = Path(pcb_path)
        if not p_path.exists():
            raise FileNotFoundError(f"PCB file not found: {p_path}")

        temp_file: Path | None = None
        target_output = output_report
        if target_output is None:
            fd, tmp_name = tempfile.mkstemp(suffix=".json" if as_json else ".rpt")
            os.close(fd)
            temp_file = Path(tmp_name)
            target_output = temp_file

        cmd = [str(self.executable), "pcb", "drc"]
        if as_json:
            cmd.extend(["--format", "json"])
        if severity_all:
            cmd.append("--severity-all")
        cmd.extend(["--units", units, "--output", str(target_output), str(p_path)])

        try:
            subprocess.run(cmd, capture_output=True, text=True)
            if Path(target_output).exists():
                return Path(target_output).read_text(encoding="utf-8")
            return ""
        finally:
            if temp_file and temp_file.exists():
                temp_file.unlink(missing_ok=True)

    def export_svg(
        self,
        pcb_path: Path | str,
        output_dir: Path | str,
        layers: list[str] | None = None,
    ) -> list[Path]:
        """Export PCB layers as SVG files."""
        p_path = Path(pcb_path)
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        cmd = [str(self.executable), "pcb", "export", "svg"]
        if layers:
            cmd.extend(["--layers", ",".join(layers)])
        cmd.extend(["--output", str(out_dir), str(p_path)])

        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise KiCadCliExecutionError(f"SVG export failed: {res.stderr}")

        return list(out_dir.glob("*.svg"))

    def export_gerbers(
        self,
        pcb_path: Path | str,
        output_dir: Path | str,
    ) -> list[Path]:
        """Export PCB Gerbers to output directory."""
        p_path = Path(pcb_path)
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        cmd = [str(self.executable), "pcb", "export", "gerbers", "--output", str(out_dir), str(p_path)]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise KiCadCliExecutionError(f"Gerber export failed: {res.stderr}")

        return list(out_dir.glob("*.g*")) + list(out_dir.glob("*.drl"))

    def export_pdf(
        self,
        pcb_path: Path | str,
        output_file: Path | str,
        layers: list[str] | None = None,
    ) -> Path:
        """Export PCB layers as a PDF file."""
        p_path = Path(pcb_path)
        out_file = Path(output_file)
        out_file.parent.mkdir(parents=True, exist_ok=True)

        cmd = [str(self.executable), "pcb", "export", "pdf"]
        if layers:
            cmd.extend(["--layers", ",".join(layers)])
        cmd.extend(["--output", str(out_file), str(p_path)])

        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise KiCadCliExecutionError(f"PDF export failed: {res.stderr}")

        return out_file

    def export_netlist(
        self,
        schematic_path: Path | str,
        output_file: Path | str,
    ) -> Path:
        """Export netlist from a schematic file."""
        sch_path = Path(schematic_path)
        out_file = Path(output_file)
        out_file.parent.mkdir(parents=True, exist_ok=True)

        cmd = [str(self.executable), "sch", "export", "netlist", "--output", str(out_file), str(sch_path)]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise KiCadCliExecutionError(f"Netlist export failed: {res.stderr}")

        return out_file

    def execute_drc(self, pcb_path: Path | str) -> list[Finding]:
        """Execute DRC and parse into standard findings."""
        from pcb_inspector.kicad.report_parser import parse_drc_json

        raw_json = self.run_drc(pcb_path, as_json=True)
        return parse_drc_json(raw_json)

    def execute_erc(self, schematic_path: Path | str) -> list[Finding]:
        """Execute ERC and parse into standard findings."""
        from pcb_inspector.kicad.report_parser import parse_erc_json

        raw_json = self.run_erc(schematic_path, as_json=True)
        return parse_erc_json(raw_json)
