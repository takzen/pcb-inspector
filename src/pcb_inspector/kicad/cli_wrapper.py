"""KiCad CLI discovery and execution wrapper."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from pcb_inspector.core.exceptions import KiCadCliExecutionError, KiCadCliNotFoundError

if TYPE_CHECKING:
    from pcb_inspector.core.models import Finding

logger = logging.getLogger(__name__)

#: Wall-clock limit for any single kicad-cli invocation. Without it a stalled
#: kicad-cli hangs the whole audit (and the CI job) indefinitely.
DEFAULT_TIMEOUT_SECONDS = 300

#: kicad-cli returns 5 from `pcb drc` / `sch erc` when `--exit-code-violations`
#: is passed and violations exist. That is a successful run reporting findings,
#: not an execution failure. Every other non-zero code is a real error
#: (verified against KiCad 9.0.7: corrupt board -> rc=3 "Failed to load board").
VIOLATIONS_EXIT_CODE = 5


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

    @classmethod
    def detect(cls) -> KiCadCli | None:
        """Attempt to locate kicad-cli on the system and return an instance, or None."""
        exe = cls.find_executable()
        if exe:
            return cls(exe)
        return None

    def is_available(self) -> bool:
        """Return True if the configured executable exists and is runnable."""
        return self.executable.exists() and os.access(self.executable, os.X_OK)

    def _resolve_executable(self, candidate: str | Path | None) -> Path:
        resolved = self.find_executable(candidate)
        if not resolved:
            raise KiCadCliNotFoundError(
                "kicad-cli executable was not found. Ensure KiCad 8+ is installed or provide the path."
            )
        return resolved

    def _run(
        self,
        cmd: list[str],
        action: str,
        timeout: float | None = DEFAULT_TIMEOUT_SECONDS,
    ) -> subprocess.CompletedProcess[str]:
        """Execute a kicad-cli command, converting process failures into KiCadCliExecutionError.

        Args:
            cmd: Full argument vector, including the executable.
            action: Human-readable operation name used in error messages.
            timeout: Wall-clock limit in seconds; None disables it.

        Raises:
            KiCadCliExecutionError: On timeout or OS-level spawn failure.
        """
        logger.debug("Running kicad-cli: %s", " ".join(cmd))
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as err:
            raise KiCadCliExecutionError(
                f"{action} timed out after {timeout}s. Command: {' '.join(cmd)}"
            ) from err
        except OSError as err:
            raise KiCadCliExecutionError(f"{action} could not be started: {err}") from err

    @staticmethod
    def _check_returncode(res: subprocess.CompletedProcess[str], action: str) -> None:
        """Raise KiCadCliExecutionError unless the process reported success or violations."""
        if res.returncode in (0, VIOLATIONS_EXIT_CODE):
            return
        detail = (res.stderr or res.stdout or "").strip() or "no diagnostic output"
        raise KiCadCliExecutionError(f"{action} failed (exit code {res.returncode}): {detail}")

    def get_version(self) -> str:
        """Return the kicad-cli version string."""
        cmd = [str(self.executable), "version"]
        res = self._run(cmd, action="kicad-cli version query", timeout=30)
        self._check_returncode(res, "kicad-cli version query")
        return res.stdout.strip()

    def run_erc(
        self,
        schematic_path: Path | str,
        output_report: Path | str | None = None,
        as_json: bool = True,
        severity_all: bool = True,
        timeout: float | None = DEFAULT_TIMEOUT_SECONDS,
    ) -> str:
        """Run Electrical Rules Check (ERC) on a schematic file and return JSON/text string.

        Raises:
            KiCadCliExecutionError: If kicad-cli fails, times out, or writes no report.
        """
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

        action = f"ERC on {sch_path.name}"
        try:
            res = self._run(cmd, action=action, timeout=timeout)
            self._check_returncode(res, action)
            report = Path(target_output)
            if not report.exists():
                raise KiCadCliExecutionError(
                    f"{action} reported success but produced no report at {report}. "
                    f"stderr: {(res.stderr or '').strip() or 'none'}"
                )
            return report.read_text(encoding="utf-8")
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
        timeout: float | None = DEFAULT_TIMEOUT_SECONDS,
        schematic_parity: bool = False,
    ) -> str:
        """Run Design Rules Check (DRC) on a PCB layout file and return JSON/text string.

        Raises:
            KiCadCliExecutionError: If kicad-cli fails, times out, or writes no report.
        """
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
        if schematic_parity:
            cmd.append("--schematic-parity")
        cmd.extend(["--units", units, "--output", str(target_output), str(p_path)])

        action = f"DRC on {p_path.name}"
        try:
            res = self._run(cmd, action=action, timeout=timeout)
            self._check_returncode(res, action)
            report = Path(target_output)
            if not report.exists():
                raise KiCadCliExecutionError(
                    f"{action} reported success but produced no report at {report}. "
                    f"stderr: {(res.stderr or '').strip() or 'none'}"
                )
            return report.read_text(encoding="utf-8")
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

        # Snapshot pre-existing files so stale renders from an earlier run are
        # not reported as the output of this one.
        before = set(out_dir.glob("*.svg"))
        res = self._run(cmd, action="SVG export")
        self._check_returncode(res, "SVG export")

        produced = sorted(set(out_dir.glob("*.svg")) - before)
        return produced or sorted(out_dir.glob("*.svg"))

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
        res = self._run(cmd, action="Gerber export")
        self._check_returncode(res, "Gerber export")

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

        res = self._run(cmd, action="PDF export")
        self._check_returncode(res, "PDF export")

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
        res = self._run(cmd, action="Netlist export")
        self._check_returncode(res, "Netlist export")

        return out_file

    def execute_drc(self, pcb_path: Path | str) -> list[Finding]:
        """Execute DRC and parse into standard findings."""
        from pcb_inspector.kicad.report_parser import parse_drc_json

        # The report parser always handled a schematic_parity section, but the
        # flag that fills it was never passed, so a board that had drifted
        # from its schematic (16 missing footprints, 72 net conflicts on a
        # real one) passed Layer 1 without a word.
        schematic = Path(pcb_path).with_suffix(".kicad_sch")
        raw_json = self.run_drc(pcb_path, as_json=True, schematic_parity=schematic.exists())
        return parse_drc_json(raw_json)

    def execute_erc(self, schematic_path: Path | str) -> list[Finding]:
        """Execute ERC and parse into standard findings."""
        from pcb_inspector.kicad.report_parser import parse_erc_json

        raw_json = self.run_erc(schematic_path, as_json=True)
        return parse_erc_json(raw_json)
