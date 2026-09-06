"""Heuristic rule verifying differential pair length matching and skew."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.core.models import Coordinate, Finding, FindingCategory, Severity
from pcb_inspector.kicad.pcb_model import PcbBoard, load_pcb_board
from pcb_inspector.rules.base import BaseRule

DIFF_PAIR_PATTERNS = [
    # (regex_match, pos_suffix, neg_suffix)
    (re.compile(r"^(.*?)[_/-]?(?:P|\+)$", re.IGNORECASE), re.compile(r"^(.*?)[_/-]?(?:N|\-)$", re.IGNORECASE)),
]


class DifferentialPairSkewRule(BaseRule):
    """Verifies length matching and skew constraints within differential signal pairs."""

    rule_id = "HEUR-DIFF-001"
    name = "Differential Pair Length Matching (Skew)"
    category = FindingCategory.SIGNAL_INTEGRITY
    default_severity = Severity.WARNING
    description = (
        "Ensures complementary differential pair signals (e.g. USB, Ethernet, CAN, PCIe) "
        "have matched physical trace lengths to prevent common-mode noise and timing jitter."
    )

    def evaluate(self, context: Any, config: InspectorConfig) -> list[Finding]:
        findings: list[Finding] = []

        board: PcbBoard
        if isinstance(context, PcbBoard):
            board = context
        elif isinstance(context, (str, Path)):
            p = Path(context)
            if p.suffix != ".kicad_pcb":
                pcb_candidate = p.with_suffix(".kicad_pcb") if p.is_file() else (p / f"{p.stem}.kicad_pcb")
                if not pcb_candidate.exists():
                    return findings
                p = pcb_candidate
            try:
                board = load_pcb_board(p)
            except Exception:
                return findings
        else:
            return findings

        max_skew = config.max_diff_pair_skew_mm

        # Group nets by base differential name
        all_nets = set(board.nets.values())
        pairs: dict[str, dict[str, str]] = {}  # base_name -> {"P": net_name, "N": net_name}

        for net in all_nets:
            if not net:
                continue
            # Check for _P or +
            if net.endswith(("_P", "_p", "+")):
                base = net[:-2] if net.endswith(("_P", "_p")) else net[:-1]
                pairs.setdefault(base, {})["P"] = net
            elif net.endswith(("_N", "_n", "-")):
                base = net[:-2] if net.endswith(("_N", "_n")) else net[:-1]
                pairs.setdefault(base, {})["N"] = net

        # Evaluate matched pairs
        for base, pair in pairs.items():
            if "P" not in pair or "N" not in pair:
                continue

            net_p = pair["P"]
            net_n = pair["N"]

            tracks_p = board.get_tracks_by_net(net_p)
            tracks_n = board.get_tracks_by_net(net_n)

            if not tracks_p or not tracks_n:
                continue

            len_p = sum(t.length for t in tracks_p)
            len_n = sum(t.length for t in tracks_n)
            skew = abs(len_p - len_n)

            if skew > max_skew:
                shorter_net = net_p if len_p < len_n else net_n
                longer_net = net_n if len_p < len_n else net_p
                shorter_len = min(len_p, len_n)
                longer_len = max(len_p, len_n)

                # Representative coordinate for inspection
                sample_track = tracks_p[0]
                coord = Coordinate(x=sample_track.start_x, y=sample_track.start_y, layer=sample_track.layer)

                sev = Severity.CRITICAL if skew > 1.0 else Severity.WARNING

                findings.append(
                    Finding(
                        id=f"DIFF-SKEW-{base.upper()}",
                        title=f"Differential pair skew on '{base}' ({skew:.2f} mm mismatch)",
                        severity=sev,
                        category=FindingCategory.SIGNAL_INTEGRITY,
                        description=(
                            f"Differential pair '{base}' has a length mismatch of {skew:.2f} mm "
                            f"(allowed: {max_skew:.2f} mm). Net '{longer_net}' is {longer_len:.2f} mm, "
                            f"while '{shorter_net}' is {shorter_len:.2f} mm."
                        ),
                        rule_id=self.rule_id,
                        nets=[net_p, net_n],
                        coordinates=[coord],
                        rationale=(
                            "Length mismatch between differential traces converts differential signaling "
                            "into common-mode noise, degrading receiver eye diagram and emitting EMI radiation."
                        ),
                        recommendation=(
                            f"Apply length tuning (meanders/serpentines) to shorter net '{shorter_net}' "
                            f"to add {skew:.2f} mm and achieve skew < {max_skew:.2f} mm."
                        ),
                        raw_data={
                            "base_name": base,
                            "net_p": net_p,
                            "net_n": net_n,
                            "length_p_mm": round(len_p, 3),
                            "length_n_mm": round(len_n, 3),
                            "skew_mm": round(skew, 3),
                            "max_skew_threshold_mm": max_skew,
                        },
                    )
                )

        return findings
