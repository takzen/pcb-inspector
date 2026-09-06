"""File watcher for continuous inspection during layout iterations."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

WATCH_EXTENSIONS = {
    ".kicad_pcb",
    ".kicad_sch",
    ".kicad_pro",
    ".kicad_prl",
    ".kicad_dru",
    ".yaml",
    ".yml",
}


def get_watch_files(target: Path) -> dict[Path, float]:
    """Collect paths and their last modified timestamps for watched files."""
    files: dict[Path, float] = {}
    if target.is_file():
        # Watch the file and companion files in the same directory
        directory = target.parent
        try:
            for f in directory.iterdir():
                if f.is_file() and f.suffix in WATCH_EXTENSIONS:
                    try:
                        files[f] = f.stat().st_mtime
                    except OSError:
                        pass
        except OSError:
            pass
    elif target.is_dir():
        try:
            for f in target.rglob("*"):
                if f.is_file() and f.suffix in WATCH_EXTENSIONS:
                    try:
                        files[f] = f.stat().st_mtime
                    except OSError:
                        pass
        except OSError:
            pass
    return files


def watch_and_run(
    target: Path,
    action: Callable[[], Any],
    poll_interval: float = 1.0,
    max_iterations: int | None = None,
) -> None:
    """Run an action repeatedly whenever watched files change.

    Args:
        target: Target file or directory to watch.
        action: Callback returning result of evaluation.
        poll_interval: Polling interval in seconds.
        max_iterations: Maximum iterations to run (primarily for testing).
    """
    iteration = 0
    # Run once initially
    action()
    iteration += 1

    last_snapshot = get_watch_files(target)

    try:
        while True:
            if max_iterations is not None and iteration >= max_iterations:
                break
            time.sleep(poll_interval)
            current_snapshot = get_watch_files(target)

            changed = False
            for f, mtime in current_snapshot.items():
                if f not in last_snapshot or mtime > last_snapshot[f]:
                    changed = True
                    break

            if not changed and len(current_snapshot) != len(last_snapshot):
                changed = True

            if changed:
                last_snapshot = current_snapshot
                iteration += 1
                action()
    except KeyboardInterrupt:
        pass
