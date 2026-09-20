"""Net class settings: the designer's declared intent for each net.

A net class carries the track width, clearance, via sizes and differential pair
geometry a designer chose for a group of nets. Rules that check widths against a
single global threshold cannot tell a deliberately thin signal trace from an
undersized power rail; the net class can.

Storage moved between KiCad versions, so both locations are read:

* KiCad 6 and later keep net classes in the ``.kicad_pro`` project file, as JSON
  under ``net_settings``, with nets matched to classes by wildcard pattern.
* KiCad 5 kept them inline in the ``.kicad_pcb`` as ``(net_class ...)`` nodes
  listing their nets explicitly with ``(add_net ...)``.

Verified against KiCad 9.0.7 and 10.0.6 demo projects: modern ``.kicad_pcb``
files contain no ``net_class`` node at all.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from pcb_inspector.kicad.sexpr_parser import find_all, find_first

logger = logging.getLogger(__name__)

#: Name KiCad gives the class that applies to otherwise unmatched nets.
DEFAULT_CLASS_NAME = "Default"


class NetClass(BaseModel):
    """Design intent for a group of nets. All dimensions in millimetres.

    Fields are optional because neither storage format guarantees every key,
    and a missing value must not be confused with a value of zero.
    """

    name: str
    clearance: float | None = None
    track_width: float | None = None
    via_diameter: float | None = None
    via_drill: float | None = None
    diff_pair_width: float | None = None
    diff_pair_gap: float | None = None


class NetClassSettings(BaseModel):
    """Net classes plus the rules mapping nets onto them."""

    classes: dict[str, NetClass] = Field(default_factory=dict)
    #: (pattern, class name) in file order.
    patterns: list[tuple[str, str]] = Field(default_factory=list)
    #: Explicit net name -> class name, which outranks any pattern.
    assignments: dict[str, str] = Field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.classes)

    @property
    def default(self) -> NetClass | None:
        return self.classes.get(DEFAULT_CLASS_NAME)

    def class_for(self, net_name: str) -> NetClass | None:
        """Resolve the net class governing ``net_name``.

        Precedence: an explicit assignment, then the first matching pattern in
        file order, then the Default class.

        This is an approximation of KiCad's own resolution, which since KiCad 8
        can compose several classes onto one net. For a heuristic that reads a
        single expected width or gap, the first match is the useful answer; it
        is not a reimplementation of KiCad's DRC engine.
        """
        if not self.classes:
            return None

        explicit = self.assignments.get(net_name)
        if explicit and explicit in self.classes:
            return self.classes[explicit]

        for pattern, class_name in self.patterns:
            if class_name in self.classes and _matches(pattern, net_name):
                return self.classes[class_name]

        return self.default


def _matches(pattern: str, net_name: str) -> bool:
    """Match a net name against a KiCad net class pattern.

    KiCad accepts both shell wildcards (``*``, ``?``) and regular expressions.
    Wildcards are tried first since they are by far the common case; a pattern
    that is not a plain wildcard is then tried as a regex. ``fnmatchcase`` is
    used rather than ``fnmatch`` because the latter folds case on Windows,
    while KiCad net names are case-sensitive on every platform.
    """
    if fnmatch.fnmatchcase(net_name, pattern):
        return True

    # Only worth a regex attempt when the pattern carries syntax fnmatch lacks.
    if not any(ch in pattern for ch in r"()[]{}|+^$\\"):
        return False
    try:
        return re.fullmatch(pattern, net_name) is not None
    except re.error:
        logger.debug("Net class pattern %r is neither a valid glob nor regex.", pattern)
        return False


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    # KiCad writes 0 for "inherit / unset" on some keys; a zero-width track is
    # not a meaningful expectation either way.
    return result if result > 0.0 else None


def parse_project_net_classes(data: dict[str, Any]) -> NetClassSettings:
    """Build settings from the parsed contents of a ``.kicad_pro`` file."""
    settings = NetClassSettings()
    net_settings = data.get("net_settings")
    if not isinstance(net_settings, dict):
        return settings

    for raw in net_settings.get("classes") or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name", "")).strip()
        if not name:
            continue
        settings.classes[name] = NetClass(
            name=name,
            clearance=_as_float(raw.get("clearance")),
            track_width=_as_float(raw.get("track_width")),
            via_diameter=_as_float(raw.get("via_diameter")),
            via_drill=_as_float(raw.get("via_drill")),
            diff_pair_width=_as_float(raw.get("diff_pair_width")),
            diff_pair_gap=_as_float(raw.get("diff_pair_gap")),
        )

    for raw in net_settings.get("netclass_patterns") or []:
        if not isinstance(raw, dict):
            continue
        pattern = raw.get("pattern")
        class_name = raw.get("netclass")
        if isinstance(pattern, str) and isinstance(class_name, str):
            settings.patterns.append((pattern, class_name))

    raw_assignments = net_settings.get("netclass_assignments")
    if isinstance(raw_assignments, dict):
        for net, class_name in raw_assignments.items():
            # KiCad 8+ may store a list here when several classes are composed;
            # the first entry is the one a single-value heuristic can use.
            if isinstance(class_name, list) and class_name:
                class_name = class_name[0]
            if isinstance(net, str) and isinstance(class_name, str):
                settings.assignments[net] = class_name

    return settings


def parse_legacy_net_classes(parsed: list[Any]) -> NetClassSettings:
    """Build settings from KiCad 5 style ``(net_class ...)`` nodes in a board."""
    settings = NetClassSettings()

    for node in find_all(parsed, "net_class"):
        if len(node) < 2:
            continue
        name = str(node[1]).strip()
        if not name:
            continue

        def value(tag: str, node: list[Any] = node) -> float | None:
            found = find_first(node, tag)
            return _as_float(found[1]) if found and len(found) > 1 else None

        settings.classes[name] = NetClass(
            name=name,
            clearance=value("clearance"),
            track_width=value("trace_width"),
            via_diameter=value("via_dia"),
            via_drill=value("via_drill"),
            diff_pair_width=value("diff_pair_width"),
            diff_pair_gap=value("diff_pair_gap"),
        )

        # Legacy classes list their nets outright rather than by pattern.
        for add_net in find_all(node, "add_net"):
            if len(add_net) > 1:
                settings.assignments[str(add_net[1])] = name

    return settings


def find_project_file(pcb_path: Path) -> Path | None:
    """Locate the ``.kicad_pro`` belonging to a board file."""
    sibling = pcb_path.with_suffix(".kicad_pro")
    if sibling.is_file():
        return sibling

    candidates = sorted(pcb_path.parent.glob("*.kicad_pro"))
    return candidates[0] if len(candidates) == 1 else None


def load_net_classes(pcb_path: Path, parsed_board: list[Any] | None = None) -> NetClassSettings:
    """Load net classes for a board, preferring the project file.

    Falls back to legacy in-board definitions, and returns empty settings when
    neither is present. A project file that cannot be read is logged and
    treated as absent: net classes enrich the audit, so losing them must not
    fail it.
    """
    project = find_project_file(pcb_path)
    if project is not None:
        try:
            data = json.loads(project.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as err:
            logger.warning("Could not read net classes from %s: %s", project, err)
        else:
            if isinstance(data, dict):
                settings = parse_project_net_classes(data)
                if settings:
                    return settings

    if parsed_board is not None:
        return parse_legacy_net_classes(parsed_board)

    return NetClassSettings()
