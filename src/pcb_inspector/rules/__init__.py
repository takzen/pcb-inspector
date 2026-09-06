"""Inspection rules subsystem and registry."""

from pcb_inspector.rules.base import BaseRule
from pcb_inspector.rules.kicad_drc_erc import KiCadDrcErcRule
from pcb_inspector.rules.registry import RuleRegistry, default_registry, init_default_registry

__all__ = ["BaseRule", "KiCadDrcErcRule", "RuleRegistry", "default_registry", "init_default_registry"]
