"""Benchmarking and verification against reference Golden Sample boards."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from pcb_inspector.cli.main import app
from pcb_inspector.core.aggregator import FindingAggregator
from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.core.layers import HEURISTIC_CATEGORIES
from pcb_inspector.core.models import AuditResult, FindingCategory, Severity
from pcb_inspector.rules.registry import default_registry

runner = CliRunner()
GOLDEN_SAMPLES_DIR = Path(__file__).parent / "golden_samples"
CLEAN_BOARD_PATH = GOLDEN_SAMPLES_DIR / "clean_board" / "clean_board.kicad_pcb"
FLAWED_BOARD_PATH = GOLDEN_SAMPLES_DIR / "flawed_board" / "flawed_board.kicad_pcb"


def test_clean_board_benchmark() -> None:
    """Benchmark: Verify that the clean reference board passes all heuristic rules with 100% health."""
    assert CLEAN_BOARD_PATH.exists()
    cfg = InspectorConfig(enable_vision=False)

    # Evaluate heuristic rules (Layer 2)
    raw_findings = default_registry.evaluate_filtered(
        context=CLEAN_BOARD_PATH,
        config=cfg,
        categories={
            FindingCategory.DECOUPLING,
            FindingCategory.POWER_DELIVERY,
            FindingCategory.SIGNAL_INTEGRITY,
            FindingCategory.THERMAL,
            FindingCategory.PLACEMENT,
            FindingCategory.SILKSCREEN,
            FindingCategory.MECHANICAL,
        },
    )
    findings = FindingAggregator().aggregate(raw_findings)
    result = AuditResult.create(
        project_path=str(CLEAN_BOARD_PATH),
        findings=findings,
        fail_on=Severity.WARNING,
    )

    # In clean board, all heuristics should be clean
    assert result.summary.passed is True
    assert result.health_score == 100.0
    assert result.summary.critical_count == 0
    assert result.summary.warning_count == 0
    assert len(result.actionable_fixes) == 0


def test_flawed_board_benchmark() -> None:
    """Benchmark: Verify that all deliberate layout flaws are detected and produce actionable fixes."""
    assert FLAWED_BOARD_PATH.exists()
    cfg = InspectorConfig(enable_vision=False)

    raw_findings = default_registry.evaluate_filtered(
        context=FLAWED_BOARD_PATH,
        config=cfg,
        categories={
            FindingCategory.DECOUPLING,
            FindingCategory.POWER_DELIVERY,
            FindingCategory.SIGNAL_INTEGRITY,
            FindingCategory.THERMAL,
            FindingCategory.PLACEMENT,
            FindingCategory.SILKSCREEN,
            FindingCategory.MECHANICAL,
        },
    )
    findings = FindingAggregator().aggregate(raw_findings)
    result = AuditResult.create(
        project_path=str(FLAWED_BOARD_PATH),
        findings=findings,
        fail_on=Severity.WARNING,
    )

    # Flawed board must fail with degraded health score
    assert result.summary.passed is False
    assert result.health_score < 60.0
    assert result.summary.warning_count >= 4

    triggered_rule_ids = {f.rule_id for f in findings}
    # 1. Decoupling capacitor placed 35mm away
    assert "HEUR-DEC-001" in triggered_rule_ids
    # 2. Power net +12V with 0.15mm trace width (< 0.4mm)
    assert "HEUR-PWR-001" in triggered_rule_ids
    # 3. Differential pair USB_P (35mm) vs USB_N (20mm) skew = 15mm (> 0.15mm)
    assert "HEUR-DIFF-001" in triggered_rule_ids
    # 4. DC/DC converter high di/dt loop area exceeded
    assert "HEUR-DCDC-001" in triggered_rule_ids
    # 5. Missing ground plane zone
    assert "HEUR-GND-001" in triggered_rule_ids

    # Verify actionable fixes were generated
    action_types = {fix.action_type for fix in result.actionable_fixes}
    assert "RELOCATE_COMPONENT" in action_types
    assert "WIDEN_TRACE" in action_types
    assert "TUNE_DIFF_PAIR_SKEW" in action_types

    # Each fix must match the rule that produced it. Dispatching on category
    # instead of rule_id handed the ground-plane finding a diff-pair fix and
    # the switching-loop finding a widen-trace fix.
    fix_by_rule = {
        f.rule_id: f.actionable_fix.action_type
        for f in findings
        if f.actionable_fix is not None
    }
    assert fix_by_rule["HEUR-GND-001"] == "EXPAND_GROUND_PLANE"
    assert fix_by_rule["HEUR-DCDC-001"] == "COMPACT_SWITCHING_LOOP"
    assert fix_by_rule["HEUR-DIFF-001"] == "TUNE_DIFF_PAIR_SKEW"
    assert fix_by_rule["HEUR-PWR-001"] == "WIDEN_TRACE"


def test_cli_golden_sample_reports(tmp_path: Path) -> None:
    """Verify end-to-end report generation (Markdown, HTML, JSON) on the golden samples."""
    # Test clean board passes with exit code 0
    clean_out_dir = tmp_path / "clean_reports"
    clean_out_dir.mkdir()
    clean_base = clean_out_dir / "clean_result"

    clean_res = runner.invoke(
        app,
        [
            "analyze",
            str(CLEAN_BOARD_PATH),
            "-o",
            str(clean_base),
            "-f",
            "all",
            "--fail-on",
            "WARNING",
        ],
    )
    assert clean_res.exit_code == 0
    assert (clean_out_dir / "clean_result.md").exists()
    assert (clean_out_dir / "clean_result.json").exists()
    assert (clean_out_dir / "clean_result.html").exists()

    # Test flawed board fails with exit code 1 due to critical violations
    flawed_out_dir = tmp_path / "flawed_reports"
    flawed_out_dir.mkdir()
    flawed_base = flawed_out_dir / "flawed_result"

    flawed_res = runner.invoke(
        app,
        [
            "analyze",
            str(FLAWED_BOARD_PATH),
            "-o",
            str(flawed_base),
            "-f",
            "all",
            "--fail-on",
            "CRITICAL",
        ],
    )
    assert flawed_res.exit_code == 1
    assert (flawed_out_dir / "flawed_result.md").exists()
    assert (flawed_out_dir / "flawed_result.json").exists()
    assert (flawed_out_dir / "flawed_result.html").exists()

    # Verify content in HTML report
    html_content = (flawed_out_dir / "flawed_result.html").read_text(encoding="utf-8")
    assert "HEUR-DEC-001" in html_content
    assert "HEUR-PWR-001" in html_content


MIXED_SIGNAL_BOARD_PATH = (
    GOLDEN_SAMPLES_DIR / "mixed_signal_board" / "mixed_signal_board.kicad_pcb"
)


def test_mixed_signal_board_benchmark() -> None:
    """Benchmark: an STM32-style board must yield real defects and no false ones.

    This board is built from the exact confusions the heuristics used to make.
    Against the pre-fix engine it produced one finding, and that finding was
    wrong (SWCLK read as a DC/DC switching node), while both genuine defects
    went unreported.
    """
    assert MIXED_SIGNAL_BOARD_PATH.exists()
    cfg = InspectorConfig(enable_vision=False)

    raw_findings = default_registry.evaluate_filtered(
        context=MIXED_SIGNAL_BOARD_PATH,
        config=cfg,
        categories=HEURISTIC_CATEGORIES,
    )
    findings = FindingAggregator(config=cfg).aggregate(raw_findings)
    by_rule = {f.rule_id for f in findings}

    # No false positives: SWCLK, SWDIO and PHY_TXD span the board but none is a
    # regulator switching node, and there is no inductor anywhere on it.
    assert "HEUR-DCDC-001" not in by_rule

    # The board has a B.Cu ground pour covering all routing.
    assert "HEUR-GND-001" not in by_rule

    # Real defect 1: CN1 is a connector on +3V3, not a bypass capacitor, so U1
    # has no decoupling at all.
    dec = [f for f in findings if f.rule_id == "HEUR-DEC-001"]
    assert len(dec) == 1
    assert dec[0].severity is Severity.CRITICAL
    assert dec[0].id == "DEC-MISSING-U1-1"
    assert dec[0].actionable_fix is not None
    assert dec[0].actionable_fix.action_type == "ADD_DECOUPLING_CAPACITOR"

    # Real defect 2: USB_DP is 35mm and USB_DM is 20mm.
    diff = [f for f in findings if f.rule_id == "HEUR-DIFF-001"]
    assert len(diff) == 1
    assert diff[0].raw_data["skew_mm"] == pytest.approx(15.0)
    assert set(diff[0].nets) == {"USB_DP", "USB_DM"}

    assert len(findings) == 2, [f.id for f in findings]
