"""Rule registry and execution dispatcher."""

from __future__ import annotations

import logging
from typing import Any

from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.core.exceptions import RuleExecutionError
from pcb_inspector.core.models import Finding
from pcb_inspector.rules.base import BaseRule

logger = logging.getLogger(__name__)


class RuleRegistry:
    """Central registry maintaining active rules and running evaluations."""

    def __init__(self) -> None:
        self._rules: dict[str, BaseRule] = {}

    def register(self, rule: BaseRule) -> None:
        """Register a new inspection rule."""
        self._rules[rule.rule_id] = rule

    def get(self, rule_id: str) -> BaseRule | None:
        """Retrieve a rule by its unique ID."""
        return self._rules.get(rule_id)

    def list_rules(self) -> list[BaseRule]:
        """Return all registered rules."""
        return list(self._rules.values())

    def evaluate_all(
        self, context: Any, config: InspectorConfig, fail_fast: bool = False
    ) -> list[Finding]:
        """Evaluate all registered rules against the given context."""
        all_findings: list[Finding] = []

        for rule_id, rule in self._rules.items():
            try:
                findings = rule.evaluate(context, config)
                all_findings.extend(findings)
            except Exception as err:
                logger.error("Error executing rule %s (%s): %s", rule_id, rule.name, err)
                if fail_fast:
                    raise RuleExecutionError(f"Rule {rule_id} failed: {err}") from err

        return all_findings


def init_default_registry() -> RuleRegistry:
    """Initialize standard registry with built-in rules."""
    from pcb_inspector.rules.kicad_drc_erc import KiCadDrcErcRule

    reg = RuleRegistry()
    reg.register(KiCadDrcErcRule())
    return reg


# Global default registry instance
default_registry = init_default_registry()
