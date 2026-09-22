"""Shared PCB resolution for heuristic rules.

Every geometric rule accepts the same context shapes (a parsed ``PcbBoard``, a
``.kicad_pcb`` path, or a project directory). Resolving that inline in each rule
meant five copies of the same block, five silent ``except Exception`` handlers,
and five full re-parses of the same file per audit. This module centralises it.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pcb_inspector.core.exceptions import ProjectParsingError
from pcb_inspector.kicad.pcb_model import PcbBoard, load_pcb_board

logger = logging.getLogger(__name__)

#: Parsed boards keyed by (resolved path, mtime_ns, size). Re-parsing the same
#: file for each rule is pure waste, but a stale cache across a --watch
#: iteration would be a correctness bug, so the file's stat signature is part
#: of the key and an edited file misses the cache automatically.
_CACHE: dict[tuple[str, int, int], PcbBoard] = {}


def _cache_key(path: Path) -> tuple[str, int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (str(path.resolve()), st.st_mtime_ns, st.st_size)


def load_board_cached(path: Path) -> PcbBoard:
    """Parse a .kicad_pcb file, reusing the result across rules in the same audit.

    Raises:
        ProjectParsingError: If the file cannot be parsed.
        FileNotFoundError: If the file does not exist.
    """
    key = _cache_key(path)
    if key is not None:
        cached = _CACHE.get(key)
        if cached is not None:
            logger.debug("Board cache hit for %s", path)
            return cached

    board = load_pcb_board(path)
    if key is not None:
        _CACHE[key] = board
    return board


def clear_cache() -> None:
    """Drop all cached boards. Intended for tests and long-running processes."""
    _CACHE.clear()


def find_pcb_file(context: Any) -> Path | None:
    """Locate the .kicad_pcb file a rule context refers to, or None."""
    if not isinstance(context, (str, Path)):
        return None

    p = Path(context)
    if p.is_file() and p.suffix == ".kicad_pcb":
        return p

    if p.is_file():
        candidate = p.with_suffix(".kicad_pcb")
        return candidate if candidate.exists() else None

    if p.is_dir():
        # Prefer a board named after the directory, else the first one found.
        named = p / f"{p.name}.kicad_pcb"
        if named.exists():
            return named
        return next(iter(sorted(p.glob("*.kicad_pcb"))), None)

    candidate = p.with_suffix(".kicad_pcb")
    return candidate if candidate.exists() else None


def find_design_file(context: Any) -> Path | None:
    """Locate the board, or failing that the schematic, a target refers to.

    None means there is nothing to audit. Every rule then returns no findings,
    which used to be reported as a clean PASSED run.
    """
    pcb = find_pcb_file(context)
    if pcb is not None:
        return pcb
    if not isinstance(context, (str, Path)):
        return None

    p = Path(context)
    if p.is_dir():
        return next(iter(sorted(p.glob("*.kicad_sch"))), None)
    sch = p if p.suffix == ".kicad_sch" else p.with_suffix(".kicad_sch")
    return sch if sch.is_file() else None


def resolve_board(context: Any) -> PcbBoard | None:
    """Resolve a rule context into a parsed board.

    Returns None when the context simply does not designate a PCB (for example a
    schematic-only run), which is not an error.

    Raises:
        ProjectParsingError: If a PCB file was found but could not be parsed.
            Callers must not swallow this: a rule that silently returns no
            findings for an unparseable board is indistinguishable from a rule
            that found nothing wrong.
    """
    if isinstance(context, PcbBoard):
        return context

    pcb_path = find_pcb_file(context)
    if pcb_path is None:
        return None

    try:
        return load_board_cached(pcb_path)
    except ProjectParsingError:
        raise
    except (OSError, ValueError) as err:
        raise ProjectParsingError(f"Failed to parse PCB file '{pcb_path}': {err}") from err
