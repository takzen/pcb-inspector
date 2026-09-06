"""Rule registry and execution dispatcher."""

from __future__ import annotations

import logging
from typing import Any

from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.core.exceptions import RuleExecutionError
from pcb_inspector.core.models import Finding, FindingCategory
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

    def get_rules_by_category(
        self, categories: list[FindingCategory] | set[FindingCategory]
    ) -> list[BaseRule]:
        """Return registered rules matching any of the specified categories."""
        cat_set = set(categories)
        return [r for r in self._rules.values() if r.category in cat_set]

    def evaluate_all(
        self, context: Any, config: InspectorConfig, fail_fast: bool = False
    ) -> list[Finding]:
        """Evaluate all registered rules against the given context."""
        return self.evaluate_filtered(context=context, config=config, fail_fast=fail_fast)

    def evaluate_filtered(
        self,
        context: Any,
        config: InspectorConfig,
        categories: list[FindingCategory] | set[FindingCategory] | None = None,
        rule_ids: list[str] | set[str] | None = None,
        fail_fast: bool = False,
    ) -> list[Finding]:
        """Evaluate only rules matching specified categories or IDs."""
        all_findings: list[Finding] = []
        target_cats = set(categories) if categories is not None else None
        target_ids = set(rule_ids) if rule_ids is not None else None

        for rule_id, rule in self._rules.items():
            if target_cats is not None and rule.category not in target_cats:
                continue
            if target_ids is not None and rule_id not in target_ids:
                continue
            try:
                findings = rule.evaluate(context, config)
                all_findings.extend(findings)
            except Exception as err:
                logger.error("Error executing rule %s (%s): %s", rule_id, rule.name, err)
                if fail_fast:
                    raise RuleExecutionError(f"Rule {rule_id} failed: {err}") from err

        return all_findings


def init_default_registry() -> RuleRegistry:
    """Initialize standard registry with built-in deterministic and heuristic rules."""
    from pcb_inspector.rules.decoupling import DecouplingProximityRule
    from pcb_inspector.rules.differential_pairs import DifferentialPairSkewRule
    from pcb_inspector.rules.kicad_drc_erc import KiCadDrcErcRule
    from pcb_inspector.rules.return_paths import GroundPlaneIntegrityRule
    from pcb_inspector.rules.switching_loops import SwitchingLoopGeometryRule
    from pcb_inspector.rules.trace_width import PowerTraceWidthRule
    from pcb_inspector.rules.vision_review import VisionReviewRule

    reg = RuleRegistry()
    # Layer 1: KiCad Native DRC & ERC
    reg.register(KiCadDrcErcRule())

    # Layer 2: Programmatic Engineering Heuristics
    reg.register(DecouplingProximityRule())
    reg.register(PowerTraceWidthRule())
    reg.register(DifferentialPairSkewRule())
    reg.register(SwitchingLoopGeometryRule())
    reg.register(GroundPlaneIntegrityRule())

    # Layer 3: Multimodal Vision AI Review
    reg.register(VisionReviewRule())

    return reg


# Global default registry instance
default_registry = init_default_registry()
