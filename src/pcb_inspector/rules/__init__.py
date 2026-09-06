"""Inspection rules subsystem and registry."""

from pcb_inspector.rules.base import BaseRule
from pcb_inspector.rules.registry import RuleRegistry, default_registry

__all__ = ["BaseRule", "RuleRegistry", "default_registry"]
