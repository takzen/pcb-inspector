"""Rule running KiCad native deterministic DRC and ERC checks via kicad-cli."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.core.exceptions import KiCadCliNotFoundError
from pcb_inspector.core.models import Finding, FindingCategory, Severity
from pcb_inspector.kicad.cli_wrapper import KiCadCli
from pcb_inspector.rules.base import BaseRule

logger = logging.getLogger(__name__)


class KiCadDrcErcRule(BaseRule):
    """Executes KiCad DRC and ERC checks to establish deterministic baseline."""

    rule_id = "KICAD-DRC-ERC-001"
    name = "Native KiCad DRC/ERC Verification"
    category = FindingCategory.DRC_ERC
    default_severity = Severity.CRITICAL
    description = "Runs kicad-cli sch erc and pcb drc, parsing all design rule violations."

    def evaluate(self, context: Any, config: InspectorConfig) -> list[Finding]:
        findings: list[Finding] = []
        path = Path(context)
        if not path.exists():
            return findings

        # Identify files to check
        pcb_file: Path | None = None
        sch_file: Path | None = None

        if path.suffix == ".kicad_pcb":
            pcb_file = path
            # Look for companion schematic
            sch_candidate = path.with_suffix(".kicad_sch")
            if sch_candidate.exists():
                sch_file = sch_candidate
        elif path.suffix == ".kicad_sch":
            sch_file = path
            # Look for companion PCB
            pcb_candidate = path.with_suffix(".kicad_pcb")
            if pcb_candidate.exists():
                pcb_file = pcb_candidate
        elif path.suffix == ".kicad_pro" or path.is_dir():
            directory = path if path.is_dir() else path.parent
            stem = path.stem if path.is_file() else None

            # Look for matching or first pcb and sch
            if stem:
                candidate_pcb = directory / f"{stem}.kicad_pcb"
                candidate_sch = directory / f"{stem}.kicad_sch"
                if candidate_pcb.exists():
                    pcb_file = candidate_pcb
                if candidate_sch.exists():
                    sch_file = candidate_sch

            if not pcb_file:
                found_pcbs = list(directory.glob("*.kicad_pcb"))
                if found_pcbs:
                    pcb_file = found_pcbs[0]

            if not sch_file:
                found_schs = list(directory.glob("*.kicad_sch"))
                if found_schs:
                    sch_file = found_schs[0]

        try:
            cli = KiCadCli(config.kicad_cli_path)
        except KiCadCliNotFoundError as err:
            # A skipped Layer 1 must never look like a clean board. Surface it as a
            # finding so it reaches the report and the process exit code.
            logger.warning("KiCad CLI not available: %s. Skipping native DRC/ERC.", err)
            severity = Severity.CRITICAL if config.require_kicad_cli else Severity.WARNING
            return [
                Finding(
                    id="DRC-CLI-UNAVAILABLE",
                    title="Layer 1 skipped: kicad-cli not found",
                    severity=severity,
                    category=FindingCategory.DRC_ERC,
                    description=(
                        "Native KiCad DRC/ERC verification did not run because kicad-cli could not "
                        f"be located. This audit covers only the remaining layers. Details: {err}"
                    ),
                    rule_id=self.rule_id,
                    rationale=(
                        "Layer 1 is the deterministic ground truth of the audit. Without it, "
                        "clearance violations, shorts, and unconnected nets are not checked at all."
                    ),
                    recommendation=(
                        "Install KiCad 8+ and ensure 'kicad-cli' is on PATH, or set 'kicad_cli_path' "
                        "in your .pcb-inspector.yaml configuration."
                    ),
                )
            ]

        # Run DRC
        if config.enable_drc and pcb_file and pcb_file.exists():
            try:
                findings.extend(cli.execute_drc(pcb_file))
            except Exception as err:
                logger.error("Failed executing DRC on %s: %s", pcb_file, err, exc_info=True)
                findings.append(self._execution_failure("DRC", pcb_file, err))

        # Run ERC
        if config.enable_erc and sch_file and sch_file.exists():
            try:
                findings.extend(cli.execute_erc(sch_file))
            except Exception as err:
                logger.error("Failed executing ERC on %s: %s", sch_file, err, exc_info=True)
                findings.append(self._execution_failure("ERC", sch_file, err))

        return findings

    def _execution_failure(self, check: str, target: Path, err: Exception) -> Finding:
        """Build a CRITICAL finding for a check that could not be executed.

        A failed check is reported as a defect rather than silently dropped, so that
        a crashed kicad-cli can never be mistaken for a board with no violations.
        """
        return Finding(
            id=f"DRC-EXEC-FAILED-{check}",
            title=f"{check} could not be executed on {target.name}",
            severity=Severity.CRITICAL,
            category=FindingCategory.DRC_ERC,
            description=(
                f"kicad-cli failed while running {check} on '{target.name}', so no {check} "
                f"results are available for this audit. Error: {err}"
            ),
            rule_id=self.rule_id,
            rationale=(
                f"An unexecuted {check} produces zero violations, which is indistinguishable "
                "from a clean board. The audit result for this layer is unknown, not passing."
            ),
            recommendation=(
                f"Open '{target.name}' in KiCad to confirm it loads, verify the kicad-cli version "
                "matches the file format, and re-run the audit."
            ),
            raw_data={"check": check, "target": str(target), "error": str(err)},
        )
