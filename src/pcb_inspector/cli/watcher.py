"""File watcher for continuous inspection during layout iterations.

Polling is kept deliberately. The measured cost of a scan is dominated by
walking directories that can never hold a board file: pointed at a repository
root the previous implementation spent 395ms per second to find 12 relevant
files, because rglob descended into .venv, .git and build caches. Pruning
those brings a scan to about a millisecond, which removes the reason to take
on an event-watching dependency for what is a developer convenience rather
than a CI feature.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

WATCH_EXTENSIONS = {
    ".kicad_pcb",
    ".kicad_sch",
    ".kicad_pro",
    ".kicad_prl",
    ".kicad_dru",
    ".yaml",
    ".yml",
}

#: Directories that cannot contain a board worth re-checking. Skipping them is
#: what makes polling cheap enough to keep.
IGNORED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        "build",
        "dist",
        ".eggs",
        "htmlcov",
        ".pcb_vision_cache",
    }
)

#: Quiet period a file set must hold before the action runs. KiCad writes a
#: board in several steps, so reacting to the first event would parse a
#: half-written file and report a bogus parse failure.
DEFAULT_DEBOUNCE_SECONDS = 0.4

#: (mtime_ns, size) per path. Size is part of the signature because a file
#: rewritten within one filesystem timestamp tick would otherwise look
#: unchanged.
Snapshot = dict[Path, tuple[int, int]]


def _signature(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def get_watch_files(target: Path) -> Snapshot:
    """Collect watched files and their change signatures under ``target``."""
    files: Snapshot = {}

    if target.is_file():
        # Watch the file and its companions in the same directory.
        try:
            for entry in target.parent.iterdir():
                if entry.is_file() and entry.suffix in WATCH_EXTENSIONS:
                    sig = _signature(entry)
                    if sig is not None:
                        files[entry] = sig
        except OSError as err:
            logger.debug("Cannot list %s: %s", target.parent, err)
        return files

    if not target.is_dir():
        return files

    for root, dirnames, filenames in os.walk(target):
        # Pruning in place is why os.walk is used rather than Path.rglob.
        dirnames[:] = [
            d for d in dirnames if d not in IGNORED_DIRS and not d.endswith(".egg-info")
        ]
        for name in filenames:
            if os.path.splitext(name)[1] in WATCH_EXTENSIONS:
                path = Path(root) / name
                sig = _signature(path)
                if sig is not None:
                    files[path] = sig

    return files


def watch_and_run(
    target: Path,
    action: Callable[[], Any],
    poll_interval: float = 1.0,
    max_iterations: int | None = None,
    debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
    max_polls: int | None = None,
) -> None:
    """Run an action repeatedly whenever watched files change.

    Args:
        target: File or directory to watch.
        action: Callback invoked once at startup and after each settled change.
        poll_interval: Seconds between scans.
        max_iterations: Stop after this many action runs. Only reached if the
            files actually change that many times, so a test that expects no
            rerun must bound the loop with ``max_polls`` instead.
        debounce_seconds: Quiet period a change must hold before acting. Zero
            disables it.
        max_polls: Stop after this many scans, whether or not anything changed.
    """
    iteration = 0
    action()
    iteration += 1

    last_snapshot = get_watch_files(target)
    polls = 0

    try:
        while True:
            if max_iterations is not None and iteration >= max_iterations:
                break
            if max_polls is not None and polls >= max_polls:
                break

            time.sleep(poll_interval)
            polls += 1
            current = get_watch_files(target)
            if current == last_snapshot:
                continue

            if debounce_seconds > 0:
                current = _settle(target, current, debounce_seconds)

            last_snapshot = current
            iteration += 1
            action()
    except KeyboardInterrupt:
        pass


def _settle(target: Path, snapshot: Snapshot, debounce_seconds: float) -> Snapshot:
    """Wait until the watched files stop changing, then return the final state.

    Bounded so that a file being appended to continuously cannot stall the
    watcher indefinitely.
    """
    deadline = time.monotonic() + max(debounce_seconds * 10, 5.0)
    while time.monotonic() < deadline:
        time.sleep(debounce_seconds)
        current = get_watch_files(target)
        if current == snapshot:
            return current
        snapshot = current

    logger.debug("Watched files kept changing; proceeding with the latest state.")
    return snapshot
