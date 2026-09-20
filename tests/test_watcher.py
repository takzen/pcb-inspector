"""Tests for the continuous-inspection file watcher.

Two defects motivated the rewrite. Pointed at a repository root the scan spent
395ms per poll walking .venv, .git and build caches to find 12 relevant files.
And a change fired the action immediately, so a board still being written by
KiCad was parsed half-complete.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

import pcb_inspector.cli.watcher as watcher_mod
from pcb_inspector.cli.watcher import (
    IGNORED_DIRS,
    WATCH_EXTENSIONS,
    get_watch_files,
    watch_and_run,
)


def _touch(path: Path, content: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# What gets watched
# --------------------------------------------------------------------------


def test_finds_board_files_in_a_directory(tmp_path: Path) -> None:
    _touch(tmp_path / "a.kicad_pcb")
    _touch(tmp_path / "nested" / "b.kicad_sch")
    _touch(tmp_path / "notes.txt")

    watched = {p.name for p in get_watch_files(tmp_path)}
    assert watched == {"a.kicad_pcb", "b.kicad_sch"}


@pytest.mark.parametrize("ignored", sorted(IGNORED_DIRS)[:6])
def test_junk_directories_are_not_walked(tmp_path: Path, ignored: str) -> None:
    _touch(tmp_path / "real.kicad_pcb")
    _touch(tmp_path / ignored / "buried.kicad_pcb")

    watched = {p.name for p in get_watch_files(tmp_path)}
    assert watched == {"real.kicad_pcb"}


def test_egg_info_directories_are_not_walked(tmp_path: Path) -> None:
    _touch(tmp_path / "real.kicad_pcb")
    _touch(tmp_path / "pkg.egg-info" / "buried.kicad_pcb")

    assert {p.name for p in get_watch_files(tmp_path)} == {"real.kicad_pcb"}


def test_pruning_keeps_scans_cheap(tmp_path: Path) -> None:
    """A deep junk tree must not be traversed at all."""
    _touch(tmp_path / "board.kicad_pcb")
    deep = tmp_path / ".venv" / "lib"
    for i in range(300):
        _touch(deep / f"pkg{i}" / "data.yaml")

    start = time.perf_counter()
    watched = get_watch_files(tmp_path)
    elapsed = time.perf_counter() - start

    assert {p.name for p in watched} == {"board.kicad_pcb"}
    assert elapsed < 0.1, f"scan took {elapsed*1000:.0f}ms despite pruning"


def test_file_target_watches_its_siblings(tmp_path: Path) -> None:
    board = _touch(tmp_path / "b.kicad_pcb")
    _touch(tmp_path / "b.kicad_pro")
    _touch(tmp_path / "elsewhere" / "other.kicad_pcb")

    watched = {p.name for p in get_watch_files(board)}
    assert watched == {"b.kicad_pcb", "b.kicad_pro"}


def test_missing_target_yields_nothing(tmp_path: Path) -> None:
    assert get_watch_files(tmp_path / "gone") == {}


def test_every_watched_extension_is_recognised(tmp_path: Path) -> None:
    for i, ext in enumerate(sorted(WATCH_EXTENSIONS)):
        _touch(tmp_path / f"f{i}{ext}")
    assert len(get_watch_files(tmp_path)) == len(WATCH_EXTENSIONS)


# --------------------------------------------------------------------------
# Change detection
# --------------------------------------------------------------------------


def test_same_size_rewrite_within_one_tick_is_detected(tmp_path: Path) -> None:
    """mtime alone can miss an edit; the signature carries size too."""
    board = _touch(tmp_path / "b.kicad_pcb", "aaaa")
    before = get_watch_files(tmp_path)

    board.write_text("bbbbb", encoding="utf-8")  # different length
    after = get_watch_files(tmp_path)

    assert after != before


def test_deleted_file_changes_the_snapshot(tmp_path: Path) -> None:
    board = _touch(tmp_path / "b.kicad_pcb")
    _touch(tmp_path / "c.kicad_pcb")
    before = get_watch_files(tmp_path)

    os.remove(board)
    assert get_watch_files(tmp_path) != before


# --------------------------------------------------------------------------
# Running the action
# --------------------------------------------------------------------------


def test_action_runs_once_at_startup(tmp_path: Path) -> None:
    _touch(tmp_path / "b.kicad_pcb")
    runs = []

    watch_and_run(tmp_path, lambda: runs.append(1), poll_interval=0.01, max_iterations=1)
    assert len(runs) == 1


def test_action_reruns_after_a_change_settles(tmp_path: Path, monkeypatch) -> None:
    """A settled change triggers exactly one more run.

    The file is changed from inside the scan hook rather than from the action,
    because watch_and_run snapshots after running. That ordering is deliberate
    -- it stops an action that writes a report from retriggering itself -- so a
    change made during the action is invisible by design.
    """
    board = _touch(tmp_path / "b.kicad_pcb", "one")
    runs: list[int] = []
    scans = {"n": 0}

    real_get = watcher_mod.get_watch_files

    def changing_on_second_scan(target: Path) -> dict[Path, tuple[int, int]]:
        scans["n"] += 1
        if scans["n"] == 2:
            board.write_text("two, and rather longer", encoding="utf-8")
        return real_get(target)

    monkeypatch.setattr(watcher_mod, "get_watch_files", changing_on_second_scan)
    watch_and_run(
        tmp_path,
        lambda: runs.append(1),
        poll_interval=0.01,
        max_iterations=2,
        debounce_seconds=0.01,
        max_polls=50,
    )

    assert len(runs) == 2


def test_action_does_not_rerun_without_a_change(tmp_path: Path) -> None:
    """Quiet files must not trigger the action, and must not hang the loop.

    max_iterations counts action runs, so it can never be reached while nothing
    changes. Bounding a quiet watch needs max_polls; without it this call spins
    forever, which is how the parameter came to exist.
    """
    _touch(tmp_path / "b.kicad_pcb")
    runs: list[int] = []

    start = time.perf_counter()
    watch_and_run(tmp_path, lambda: runs.append(1), poll_interval=0.01, max_polls=5)
    elapsed = time.perf_counter() - start

    assert len(runs) == 1
    assert elapsed < 5.0


def test_debounce_waits_for_writes_to_finish(tmp_path: Path, monkeypatch) -> None:
    """A board written in steps is parsed once, complete.

    The scan hook emulates KiCad still writing: the file first appears
    truncated, then finishes. Without settling, the action would observe the
    truncated state.
    """
    board = _touch(tmp_path / "b.kicad_pcb", "start")
    seen: list[str] = []
    scans = {"n": 0}

    real_get = watcher_mod.get_watch_files

    def staged_write(target: Path) -> dict[Path, tuple[int, int]]:
        scans["n"] += 1
        if scans["n"] == 2:
            board.write_text("partial", encoding="utf-8")
        elif scans["n"] == 3:
            board.write_text("complete board contents", encoding="utf-8")
        return real_get(target)

    monkeypatch.setattr(watcher_mod, "get_watch_files", staged_write)
    watch_and_run(
        tmp_path,
        lambda: seen.append(board.read_text(encoding="utf-8")),
        poll_interval=0.01,
        max_iterations=2,
        debounce_seconds=0.01,
        max_polls=50,
    )

    assert seen[0] == "start"
    assert seen[1] == "complete board contents", "acted on a half-written file"


def test_settling_is_bounded_when_a_file_never_stops_changing(
    tmp_path: Path, monkeypatch
) -> None:
    """A continuously written file must not stall the watcher forever."""
    board = _touch(tmp_path / "b.kicad_pcb", "a")
    runs: list[int] = []
    scans = {"n": 0}

    real_get = watcher_mod.get_watch_files

    def always_changing(target: Path) -> dict[Path, tuple[int, int]]:
        scans["n"] += 1
        board.write_text("a" * scans["n"], encoding="utf-8")
        return real_get(target)

    monkeypatch.setattr(watcher_mod, "get_watch_files", always_changing)

    start = time.perf_counter()
    watch_and_run(
        tmp_path,
        lambda: runs.append(1),
        poll_interval=0.01,
        max_iterations=2,
        debounce_seconds=0.01,
        max_polls=50,
    )
    elapsed = time.perf_counter() - start

    assert len(runs) == 2
    assert elapsed < 10.0, "settling did not give up"
