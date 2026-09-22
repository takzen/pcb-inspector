"""Execution status tracking for the three verification layers.

An audit that reports no findings is only meaningful if the layers actually ran.
This module derives, from the config and the produced findings, which layers
executed, which were skipped, and which ran but failed partway — so that
reporters and agents can distinguish "clean board" from "not checked".
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.core.models import Finding, FindingCategory

#: Finding IDs emitted by rules to signal that a layer could not run.
LAYER1_UNAVAILABLE_ID = "DRC-CLI-UNAVAILABLE"
LAYER1_FAILED_PREFIX = "DRC-EXEC-FAILED"
#: Prefixes of finding IDs meaning the vision layer did not complete. Call
#: failures carry the side reviewed, e.g. VIS-API-CALL-ERR-BOTTOM.
LAYER3_FAILURE_PREFIXES = ("VIS-NO-API-KEY", "VIS-RENDER-ERR", "VIS-API-CALL-ERR")

HEURISTIC_CATEGORIES = frozenset(
    {
        FindingCategory.DECOUPLING,
        FindingCategory.POWER_DELIVERY,
        FindingCategory.SIGNAL_INTEGRITY,
        FindingCategory.THERMAL,
        FindingCategory.PLACEMENT,
        FindingCategory.SILKSCREEN,
        FindingCategory.MECHANICAL,
    }
)


class LayerStatus(str, Enum):
    """Outcome of a single verification layer."""

    EXECUTED = "executed"
    SKIPPED = "skipped"
    FAILED = "failed"
    DISABLED = "disabled"


class Layer(str, Enum):
    """The three verification layers, in pipeline order."""

    DRC_ERC = "layer1_drc_erc"
    HEURISTICS = "layer2_heuristics"
    VISION = "layer3_vision"

    @property
    def label(self) -> str:
        match self:
            case Layer.DRC_ERC:
                return "Layer 1 — KiCad DRC/ERC"
            case Layer.HEURISTICS:
                return "Layer 2 — Engineering heuristics"
            case Layer.VISION:
                return "Layer 3 — Multimodal vision"


def _requested(
    layer: Layer, config: InspectorConfig, categories: set[FindingCategory] | None
) -> bool:
    """True if this layer was in scope for the run."""
    match layer:
        case Layer.DRC_ERC:
            in_scope = categories is None or FindingCategory.DRC_ERC in categories
            return in_scope and (config.enable_drc or config.enable_erc)
        case Layer.HEURISTICS:
            in_scope = categories is None or bool(categories & HEURISTIC_CATEGORIES)
            return in_scope and config.enable_heuristics
        case Layer.VISION:
            in_scope = categories is None or FindingCategory.VISION in categories
            return in_scope and config.enable_vision


def evaluate_layer_status(
    findings: list[Finding],
    config: InspectorConfig,
    categories: set[FindingCategory] | None = None,
) -> dict[str, str]:
    """Determine the execution status of each verification layer.

    Args:
        findings: All findings produced by the run, before or after aggregation.
        config: Configuration the run used.
        categories: Category filter the run applied, or None when unfiltered.

    Returns:
        Mapping of ``Layer`` value to ``LayerStatus`` value, suitable for
        storing verbatim in ``AuditResult.metadata``.
    """
    ids = {f.id for f in findings}
    layer1_unavailable = LAYER1_UNAVAILABLE_ID in ids
    layer1_failed = any(i.startswith(LAYER1_FAILED_PREFIX) for i in ids)
    layer3_failed = any(i.startswith(LAYER3_FAILURE_PREFIXES) for i in ids)

    status: dict[str, str] = {}
    for layer in Layer:
        if not _requested(layer, config, categories):
            status[layer.value] = LayerStatus.DISABLED.value
            continue

        match layer:
            case Layer.DRC_ERC if layer1_unavailable:
                status[layer.value] = LayerStatus.SKIPPED.value
            case Layer.DRC_ERC if layer1_failed:
                status[layer.value] = LayerStatus.FAILED.value
            case Layer.VISION if layer3_failed:
                status[layer.value] = LayerStatus.FAILED.value
            case _:
                status[layer.value] = LayerStatus.EXECUTED.value

    return status


def incomplete_layers(metadata: dict[str, Any]) -> list[str]:
    """Return human-readable labels of layers that did not complete successfully.

    Disabled layers are excluded: turning a layer off is a deliberate choice,
    whereas skipped and failed layers mean the board was not fully checked.
    """
    layers = metadata.get("layers")
    if not isinstance(layers, dict):
        return []

    incomplete: list[str] = []
    for layer in Layer:
        state = layers.get(layer.value)
        if state == LayerStatus.SKIPPED.value:
            incomplete.append(f"{layer.label}: NOT RUN")
        elif state == LayerStatus.FAILED.value:
            incomplete.append(f"{layer.label}: FAILED")
    return incomplete
