"""Inspection rules subsystem and registry."""

from pcb_inspector.rules.base import BaseRule
from pcb_inspector.rules.decoupling import DecouplingProximityRule
from pcb_inspector.rules.differential_pairs import DifferentialPairSkewRule
from pcb_inspector.rules.kicad_drc_erc import KiCadDrcErcRule
from pcb_inspector.rules.registry import RuleRegistry, default_registry, init_default_registry
from pcb_inspector.rules.return_paths import GroundPlaneIntegrityRule
from pcb_inspector.rules.switching_loops import SwitchingLoopGeometryRule
from pcb_inspector.rules.trace_width import PowerTraceWidthRule

__all__ = [
    "BaseRule",
    "DecouplingProximityRule",
    "DifferentialPairSkewRule",
    "GroundPlaneIntegrityRule",
    "KiCadDrcErcRule",
    "PowerTraceWidthRule",
    "RuleRegistry",
    "SwitchingLoopGeometryRule",
    "default_registry",
    "init_default_registry",
]
