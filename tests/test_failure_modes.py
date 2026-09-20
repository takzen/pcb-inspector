"""Regression tests for silent-failure modes.

Every test here pins down a case where the tool used to report a clean board for
work it had not actually done. They all failed before the Layer-1 reliability
fixes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from typer.testing import CliRunner

from pcb_inspector.cli.main import app
from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.core.exceptions import KiCadCliExecutionError, ProjectParsingError
from pcb_inspector.core.layers import Layer, LayerStatus, evaluate_layer_status, incomplete_layers
from pcb_inspector.core.models import Finding, FindingCategory, Severity
from pcb_inspector.kicad.cli_wrapper import KiCadCli
from pcb_inspector.kicad.pcb_model import load_pcb_board
from pcb_inspector.kicad.report_parser import parse_drc_json, parse_erc_json
from pcb_inspector.reporters.markdown_reporter import MarkdownReporter
from pcb_inspector.rules import board_loader
from pcb_inspector.rules.base import BaseRule
from pcb_inspector.rules.kicad_drc_erc import KiCadDrcErcRule
from pcb_inspector.rules.registry import RuleRegistry

runner = CliRunner()
CLEAN_BOARD = Path(__file__).parent / "golden_samples" / "clean_board" / "clean_board.kicad_pcb"


@pytest.fixture(autouse=True)
def _clear_board_cache() -> Any:
    board_loader.clear_cache()
    yield
    board_loader.clear_cache()


@pytest.fixture
def fake_cli(tmp_path: Path) -> Any:
    """A KiCadCli bound to a stand-in executable, so no real KiCad is required."""
    exe = tmp_path / "kicad-cli"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    exe.chmod(0o755)
    with mock.patch.object(KiCadCli, "find_executable", return_value=exe):
        yield KiCadCli()


# --------------------------------------------------------------------------
# P0-1: kicad-cli process failures
# --------------------------------------------------------------------------


def test_drc_nonzero_exit_raises_instead_of_reporting_clean(fake_cli: KiCadCli, tmp_path: Path) -> None:
    """A crashed kicad-cli must raise, not return an empty report."""
    board = tmp_path / "b.kicad_pcb"
    board.write_text("(kicad_pcb)", encoding="utf-8")

    completed = subprocess.CompletedProcess(
        args=[], returncode=3, stdout="", stderr="Failed to load board"
    )
    with mock.patch("subprocess.run", return_value=completed):
        with pytest.raises(KiCadCliExecutionError, match="Failed to load board"):
            fake_cli.run_drc(board)


def test_drc_success_without_report_file_raises(fake_cli: KiCadCli, tmp_path: Path) -> None:
    """Exit code 0 but no report written is still a failed run."""
    board = tmp_path / "b.kicad_pcb"
    board.write_text("(kicad_pcb)", encoding="utf-8")

    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with mock.patch("subprocess.run", return_value=completed):
        with pytest.raises(KiCadCliExecutionError, match="produced no report"):
            fake_cli.run_drc(board, output_report=tmp_path / "missing.json")


def test_drc_violations_exit_code_is_not_an_error(fake_cli: KiCadCli, tmp_path: Path) -> None:
    """Exit code 5 means 'violations found', which is a successful run."""
    board = tmp_path / "b.kicad_pcb"
    board.write_text("(kicad_pcb)", encoding="utf-8")
    report = tmp_path / "r.json"
    report.write_text('{"violations": []}', encoding="utf-8")

    completed = subprocess.CompletedProcess(args=[], returncode=5, stdout="", stderr="")
    with mock.patch("subprocess.run", return_value=completed):
        assert fake_cli.run_drc(board, output_report=report) == '{"violations": []}'


def test_drc_timeout_raises(fake_cli: KiCadCli, tmp_path: Path) -> None:
    """A stalled kicad-cli must not hang the audit forever."""
    board = tmp_path / "b.kicad_pcb"
    board.write_text("(kicad_pcb)", encoding="utf-8")

    with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="x", timeout=1)):
        with pytest.raises(KiCadCliExecutionError, match="timed out"):
            fake_cli.run_drc(board, timeout=1)


# --------------------------------------------------------------------------
# P0-3: malformed report payloads
# --------------------------------------------------------------------------


@pytest.mark.parametrize("parser", [parse_drc_json, parse_erc_json])
def test_non_json_report_raises_parsing_error(parser: Any) -> None:
    with pytest.raises(ProjectParsingError, match="not valid JSON"):
        parser("Fatal: could not load board file\n")


@pytest.mark.parametrize("parser", [parse_drc_json, parse_erc_json])
def test_json_array_report_is_rejected(parser: Any) -> None:
    with pytest.raises(ProjectParsingError, match="must be a JSON object"):
        parser("[1, 2, 3]")


@pytest.mark.parametrize("parser", [parse_drc_json, parse_erc_json])
def test_empty_report_is_still_empty_findings(parser: Any) -> None:
    """Genuinely empty output stays benign; only malformed output escalates."""
    assert parser("") == []
    assert parser("   ") == []


# --------------------------------------------------------------------------
# P0-2: Layer 1 unavailable / failed
# --------------------------------------------------------------------------


def test_missing_kicad_cli_yields_warning_finding(tmp_path: Path) -> None:
    board = tmp_path / "b.kicad_pcb"
    board.write_text("(kicad_pcb)", encoding="utf-8")

    with mock.patch.object(KiCadCli, "find_executable", return_value=None):
        findings = KiCadDrcErcRule().evaluate(board, InspectorConfig())

    assert [f.id for f in findings] == ["DRC-CLI-UNAVAILABLE"]
    assert findings[0].severity is Severity.WARNING


def test_missing_kicad_cli_is_critical_when_required(tmp_path: Path) -> None:
    board = tmp_path / "b.kicad_pcb"
    board.write_text("(kicad_pcb)", encoding="utf-8")

    with mock.patch.object(KiCadCli, "find_executable", return_value=None):
        findings = KiCadDrcErcRule().evaluate(board, InspectorConfig(require_kicad_cli=True))

    assert findings[0].severity is Severity.CRITICAL


def test_drc_execution_failure_yields_critical_finding(fake_cli: KiCadCli, tmp_path: Path) -> None:
    board = tmp_path / "b.kicad_pcb"
    board.write_text("(kicad_pcb)", encoding="utf-8")

    with mock.patch.object(KiCadCli, "find_executable", return_value=fake_cli.executable), mock.patch.object(
        KiCadCli, "execute_drc", side_effect=KiCadCliExecutionError("boom")
    ):
        findings = KiCadDrcErcRule().evaluate(board, InspectorConfig(enable_erc=False))

    assert [f.id for f in findings] == ["DRC-EXEC-FAILED-DRC"]
    assert findings[0].severity is Severity.CRITICAL
    assert "boom" in findings[0].description


# --------------------------------------------------------------------------
# Rule engine: an exception must not vanish
# --------------------------------------------------------------------------


class _ExplodingRule(BaseRule):
    rule_id = "TEST-BOOM-001"
    name = "Exploding rule"
    category = FindingCategory.DECOUPLING

    def evaluate(self, context: Any, config: InspectorConfig) -> list[Finding]:
        raise RuntimeError("rule blew up")


def test_rule_exception_becomes_a_critical_finding() -> None:
    reg = RuleRegistry()
    reg.register(_ExplodingRule())
    findings = reg.evaluate_all(context=CLEAN_BOARD, config=InspectorConfig())

    assert len(findings) == 1
    assert findings[0].id.startswith("RULE-EXEC-FAILED")
    assert findings[0].severity is Severity.CRITICAL
    assert "TEST-BOOM-001" in findings[0].description


def test_identical_rule_failures_are_reported_once() -> None:
    """Five rules failing the same way is one problem, not five findings."""

    class _Second(_ExplodingRule):
        rule_id = "TEST-BOOM-002"

    reg = RuleRegistry()
    reg.register(_ExplodingRule())
    reg.register(_Second())
    findings = reg.evaluate_all(context=CLEAN_BOARD, config=InspectorConfig())

    assert len(findings) == 1
    assert findings[0].raw_data["failed_rules"] == ["TEST-BOOM-001", "TEST-BOOM-002"]


# --------------------------------------------------------------------------
# P0-5: malformed geometry must not escape as ValueError
# --------------------------------------------------------------------------


def test_non_numeric_coordinates_do_not_raise(tmp_path: Path) -> None:
    board = tmp_path / "bad.kicad_pcb"
    board.write_text(
        '(kicad_pcb (version 20240108) '
        '(segment (start a b) (end 1 2) (width 0.2) (layer "F.Cu") (net 1)))',
        encoding="utf-8",
    )
    parsed = load_pcb_board(board)

    assert len(parsed.tracks) == 1
    assert parsed.tracks[0].start_x == 0.0  # documented fallback
    assert parsed.tracks[0].end_x == 1.0


# --------------------------------------------------------------------------
# Layer status reporting
# --------------------------------------------------------------------------


def test_skipped_layer1_is_recorded_and_surfaced() -> None:
    findings = [
        Finding(
            id="DRC-CLI-UNAVAILABLE",
            title="skipped",
            severity=Severity.WARNING,
            category=FindingCategory.DRC_ERC,
            description="d",
            rule_id="KICAD-DRC-ERC-001",
        )
    ]
    layers = evaluate_layer_status(findings, InspectorConfig(), categories=None)

    assert layers[Layer.DRC_ERC.value] == LayerStatus.SKIPPED.value
    assert layers[Layer.HEURISTICS.value] == LayerStatus.EXECUTED.value
    assert layers[Layer.VISION.value] == LayerStatus.DISABLED.value

    labels = incomplete_layers({"layers": layers})
    assert labels == ["Layer 1 — KiCad DRC/ERC: NOT RUN"]


def test_disabled_layer_is_not_reported_as_incomplete() -> None:
    """Turning vision off is a choice, not an unchecked board."""
    layers = evaluate_layer_status([], InspectorConfig(enable_vision=False), categories=None)
    assert incomplete_layers({"layers": layers}) == []


def test_markdown_report_warns_about_incomplete_audit(sample_audit_result: Any) -> None:
    sample_audit_result.metadata = {
        "layers": {Layer.DRC_ERC.value: LayerStatus.SKIPPED.value}
    }
    out = MarkdownReporter().render(sample_audit_result)

    assert "Incomplete audit" in out
    assert "Layer 1 — KiCad DRC/ERC: NOT RUN" in out
    # The warning must precede the metrics it qualifies.
    assert out.index("Incomplete audit") < out.index("## 📊 Summary")


# --------------------------------------------------------------------------
# P2-2: the board is parsed once per audit, not once per rule
# --------------------------------------------------------------------------


def test_board_is_parsed_once_per_audit() -> None:
    from pcb_inspector.rules.registry import init_default_registry

    calls = 0
    real = board_loader.load_pcb_board

    def counting(path: Path) -> Any:
        nonlocal calls
        calls += 1
        return real(path)

    with mock.patch.object(board_loader, "load_pcb_board", side_effect=counting):
        init_default_registry().evaluate_filtered(
            context=CLEAN_BOARD,
            config=InspectorConfig(),
            categories={
                FindingCategory.DECOUPLING,
                FindingCategory.POWER_DELIVERY,
                FindingCategory.SIGNAL_INTEGRITY,
            },
        )

    assert calls == 1, f"board re-parsed {calls}x for a single audit"


# --------------------------------------------------------------------------
# End to end: a broken Layer 1 must not exit 0
# --------------------------------------------------------------------------


def test_cli_drc_failure_does_not_exit_zero(tmp_path: Path) -> None:
    board = tmp_path / "b.kicad_pcb"
    board.write_text("(kicad_pcb (version 20240108))", encoding="utf-8")

    with mock.patch.object(KiCadCli, "find_executable", return_value=None):
        res = runner.invoke(app, ["drc", str(board), "--require-kicad-cli"])

    assert res.exit_code == 1, res.stdout
    assert "kicad-cli" in res.stdout
