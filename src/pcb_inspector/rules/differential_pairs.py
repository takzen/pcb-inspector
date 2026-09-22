"""Heuristic rule verifying differential pair length matching and skew."""

from __future__ import annotations

from typing import Any

from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.core.models import Coordinate, Finding, FindingCategory, Severity
from pcb_inspector.kicad.pcb_model import PcbBoard, TrackSegment, net_tokens, normalize_net
from pcb_inspector.rules.base import BaseRule
from pcb_inspector.rules.board_loader import resolve_board

#: Complementary net-name suffixes, longest first so that USB_DP is matched as
#: ("_DP", "_DM") and not as ("_P", "_N") with a stray "USB_D" base.
#: The previous implementation only understood _P/_N and +/-, which misses the
#: most common pair in consumer electronics (USB D+/D-, written DP/DM) as well
#: as CAN's H/L convention.
DIFF_PAIR_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("_DP", "_DM"),
    ("_TXP", "_TXN"),
    ("_RXP", "_RXN"),
    ("_DP", "_DN"),
    ("_P", "_N"),
    ("_H", "_L"),
    ("DP", "DM"),
    ("+", "-"),
    ("P", "N"),
    ("H", "L"),
)

#: Base-name tokens of interfaces whose edges are fast enough for millimetres
#: of skew to matter. A token matches when it starts with one of these, so
#: PCIE_TX0 and USB3 count; a prefix rather than a substring keeps ADDR from
#: reading as DDR. Clocks are matched anywhere: REFCLK, SYSCLK, CLK100M.
HIGH_SPEED_TOKENS: tuple[str, ...] = (
    "USB", "HDMI", "TMDS", "PCIE", "SATA", "LVDS", "MIPI", "CSI", "DSI",
    "ETH", "MDI", "RGMII", "SGMII", "SERDES", "DDR", "SFP",
)
#: Suffixes that name a fast pair outright: serial transceiver lanes, and USB's
#: D+/D- and DP/DM, which hubs write as /U2D+ without the word USB.
HIGH_SPEED_SUFFIXES: tuple[str, ...] = (
    "_TXP", "_TXN", "_RXP", "_RXN", "D+", "D-", "DP", "DM",
)


def is_high_speed_pair(base: str, net_name: str) -> bool:
    """True if a pair's naming identifies an interface where skew matters.

    Skew only matters relative to edge rate: 7 mm is about 45 ps, a real
    defect on USB and irrelevant on a CAN bus or a sensor coil read at kHz.
    The rule cannot see edge rates, so it trusts naming.
    """
    if net_name.upper().endswith(HIGH_SPEED_SUFFIXES):
        return True
    return any(t.startswith(HIGH_SPEED_TOKENS) or "CLK" in t for t in net_tokens(base))


def _min_base_len(suffix: str) -> int:
    """Shortest base name accepted for a given suffix.

    A delimited or symbolic suffix is unambiguous, so "D+"/"D-" — the canonical
    USB naming — is accepted on a one-character base. A bare letter suffix is
    not: allowing it on short bases would pair VIN with a hypothetical VIP, so
    those require enough base to look like a real signal name (CLKP/CLKN,
    CANH/CANL).
    """
    return 1 if not suffix[0].isalnum() or suffix.startswith("_") else 3


def split_diff_pair_suffix(net_name: str) -> tuple[str, str] | None:
    """Split a net name into (base, polarity) where polarity is "P" or "N".

    Returns None when the name does not look like half of a differential pair.
    Matching is case-insensitive but the returned base preserves the original
    casing so that report text echoes the designer's own naming.
    """
    upper = net_name.upper()
    for pos, neg in DIFF_PAIR_SUFFIXES:
        for suffix, polarity in ((pos, "P"), (neg, "N")):
            if not upper.endswith(suffix):
                continue
            base = net_name[: len(net_name) - len(suffix)]
            if len(base) >= _min_base_len(suffix):
                return base, polarity
    return None


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

    @staticmethod
    def _routed_length(board: PcbBoard, net_name: str, tracks: list[TrackSegment]) -> float:
        """Total routed length of a net, including vertical travel through vias.

        Summing only track segments understates a net that changes layer, which
        is exactly where skew between the two halves of a pair creeps in.
        """
        planar = sum(t.length for t in tracks)
        via_count = sum(1 for v in board.vias if normalize_net(v.net_name) == normalize_net(net_name))
        return planar + via_count * board.thickness

    def evaluate(self, context: Any, config: InspectorConfig) -> list[Finding]:
        findings: list[Finding] = []

        board = resolve_board(context)
        if board is None:
            return findings

        max_skew = self.param(config, "max_skew_mm", config.max_diff_pair_skew_mm)

        # Group nets by base differential name
        all_nets = set(board.nets.values())
        pairs: dict[str, dict[str, str]] = {}  # base_name -> {"P": net_name, "N": net_name}

        for net in all_nets:
            if not net:
                continue
            split = split_diff_pair_suffix(net)
            if split is None:
                continue
            base, polarity = split
            pairs.setdefault(base.upper(), {})[polarity] = net

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

            len_p = self._routed_length(board, net_p, tracks_p)
            len_n = self._routed_length(board, net_n, tracks_n)
            skew = abs(len_p - len_n)

            if skew > max_skew:
                shorter_net = net_p if len_p < len_n else net_n
                longer_net = net_n if len_p < len_n else net_p
                shorter_len = min(len_p, len_n)
                longer_len = max(len_p, len_n)

                # Representative coordinate for inspection
                sample_track = tracks_p[0]
                coord = Coordinate(x=sample_track.start_x, y=sample_track.start_y, layer=sample_track.layer)

                high_speed = is_high_speed_pair(base, net_p)
                if high_speed:
                    sev = Severity.CRITICAL if skew > 1.0 else Severity.WARNING
                    title = f"Differential pair skew on '{base}' ({skew:.2f} mm mismatch)"
                    recommendation = (
                        f"Apply length tuning (meanders/serpentines) to shorter net '{shorter_net}' "
                        f"to add {skew:.2f} mm and achieve skew < {max_skew:.2f} mm."
                    )
                else:
                    # Nothing in the name says this pair is fast. A CAN bus or
                    # an analog sensor pair was reported CRITICAL for skew that
                    # amounts to picoseconds at its own frequencies.
                    sev = Severity.SUGGESTION
                    title = (
                        f"Differential pair '{base}' has {skew:.2f} mm skew "
                        "(signal speed unknown)"
                    )
                    recommendation = (
                        f"If '{base}' carries fast edges (hundreds of MHz or more), add "
                        f"{skew:.2f} mm to '{shorter_net}'. For low-frequency or analog "
                        "signals, length matching is not needed; keep the two traces "
                        "together and symmetric so noise couples into both equally."
                    )

                findings.append(
                    Finding(
                        id=f"DIFF-SKEW-{base.upper()}",
                        title=title,
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
                            "into common-mode noise, degrading receiver eye diagram and emitting EMI radiation. "
                            "The effect scales with edge rate: 1 mm is roughly 6-7 ps."
                        ),
                        recommendation=recommendation,
                        raw_data={
                            "base_name": base,
                            "net_p": net_p,
                            "net_n": net_n,
                            "length_p_mm": round(len_p, 3),
                            "length_n_mm": round(len_n, 3),
                            "skew_mm": round(skew, 3),
                            "max_skew_threshold_mm": max_skew,
                            "high_speed_interface": high_speed,
                        },
                    )
                )

        return findings
