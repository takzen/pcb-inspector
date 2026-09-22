"""Auditing the board open in a running KiCad, through its IPC API.

KiCad itself is replaced by a stand-in for kipy's client, so these run without
KiCad. The path was also checked by hand against KiCad 10.0.6.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from kipy.errors import ApiError, ConnectionError
from kipy.proto.common import ApiStatusCode
from typer.testing import CliRunner

from pcb_inspector.core.exceptions import KiCadLiveError
from pcb_inspector.kicad import live
from pcb_inspector.kicad.live import LiveBoard, snapshot_open_board
from pcb_inspector.mcp.tools import check_decoupling_tool, inspect_project_tool

GOLDEN = Path(__file__).parent / "golden_samples" / "flawed_board"
runner = CliRunner()


def _project(tmp_path: Path) -> Path:
    """A project folder as KiCad would have it open, with a local footprint library."""
    project = tmp_path / "project"
    project.mkdir()
    for name in ("flawed_board.kicad_pcb", "flawed_board.kicad_pro"):
        shutil.copy(GOLDEN / name, project / name)
    (project / "fp-lib-table").write_text("(fp_lib_table (version 7))", encoding="utf-8")
    (project / "Local.pretty").mkdir()
    (project / "Local.pretty" / "Part.kicad_mod").write_text("(footprint Part)", encoding="utf-8")
    (project / "notes.txt").write_text("not a project file", encoding="utf-8")
    return project


def _fake_kicad(project: Path, contents: str, errors: list[Exception] | None = None) -> Any:
    """A stand-in for kipy.KiCad serving one open board."""
    pending = list(errors or [])

    class FakeKiCad:
        def __init__(self, **_: Any) -> None:
            pass

        def get_board(self) -> Any:
            if pending:
                raise pending.pop(0)
            document = SimpleNamespace(
                project=SimpleNamespace(path=str(project)), board_filename="flawed_board.kicad_pcb"
            )
            return SimpleNamespace(document=document, get_as_string=lambda: contents)

    return FakeKiCad


def test_snapshot_holds_the_editor_state_and_the_project_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    unsaved = (project / "flawed_board.kicad_pcb").read_text(encoding="utf-8") + "\n"
    monkeypatch.setattr("kipy.KiCad", _fake_kicad(project, unsaved))

    board = snapshot_open_board(tmp_path / "scratch")

    assert board.board_path == project / "flawed_board.kicad_pcb"
    assert board.snapshot_path.read_text(encoding="utf-8") == unsaved
    assert board.audit_target.name == "flawed_board.kicad_pro"
    copied = {p.name for p in (tmp_path / "scratch").iterdir()}
    # Library tables and project libraries, or DRC reports custom footprints missing.
    assert {"fp-lib-table", "Local.pretty", "flawed_board.kicad_pro"} <= copied
    assert "notes.txt" not in copied


def test_a_busy_kicad_is_waited_for(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """KiCad answers "busy" while a dialog is open; that is not a failure."""
    project = _project(tmp_path)
    busy = ApiError("KiCad is busy", code=ApiStatusCode.AS_BUSY)
    monkeypatch.setattr("kipy.KiCad", _fake_kicad(project, "(kicad_pcb)", [busy, busy]))
    monkeypatch.setattr(live, "BUSY_POLL_SECONDS", 0.0)

    assert snapshot_open_board(tmp_path / "scratch").snapshot_path.exists()


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (ConnectionError("no socket"), "Enable KiCad API"),
        (ApiError("Expected to be able to retrieve at least one board"), "no board to audit"),
    ],
)
def test_unreachable_kicad_is_explained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception, message: str
) -> None:
    monkeypatch.setattr("kipy.KiCad", _fake_kicad(tmp_path, "", [error]))
    with pytest.raises(KiCadLiveError, match=message):
        snapshot_open_board(tmp_path / "scratch")


def _serve_golden_board(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, target: Any) -> Path:
    """Make every live snapshot return a copy of the flawed golden board."""
    project = _project(tmp_path)

    def fake_snapshot(dest: Path, *_: Any, **__: Any) -> LiveBoard:
        for source in project.iterdir():
            if source.is_file():
                shutil.copy(source, dest / source.name)
        return LiveBoard(
            board_path=project / "flawed_board.kicad_pcb",
            snapshot_path=dest / "flawed_board.kicad_pcb",
        )

    monkeypatch.setattr(target, "snapshot_open_board", fake_snapshot)
    return project


def test_cli_live_audit_reports_the_real_board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    cli = importlib.import_module("pcb_inspector.cli.main")
    project = _serve_golden_board(monkeypatch, tmp_path, cli)
    report = tmp_path / "report.json"

    result = runner.invoke(
        cli.app, ["analyze", "--live", "-o", str(report), "-f", "json"]
    )

    assert result.exit_code == 1, result.stdout
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["project_path"] == f"{project / 'flawed_board.kicad_pcb'} (live)"
    assert data["findings"]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["check", "--live", "."], "takes no PROJECT_PATH"),
        (["check"], "Give a PROJECT_PATH, or --live"),
    ],
)
def test_cli_live_usage_errors(args: list[str], message: str) -> None:
    import importlib

    cli = importlib.import_module("pcb_inspector.cli.main")
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 2
    assert message in result.stdout


def test_cli_live_without_kicad_is_a_usage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    cli = importlib.import_module("pcb_inspector.cli.main")

    def unreachable(*_: Any, **__: Any) -> LiveBoard:
        raise KiCadLiveError("KiCad is not reachable over its API")

    monkeypatch.setattr(cli, "snapshot_open_board", unreachable)
    result = runner.invoke(cli.app, ["check", "--live"])
    assert result.exit_code == 2
    assert "not reachable" in result.stdout


def test_mcp_tools_audit_the_live_board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The repair loop's audit step: Konnect edits live, the file stays stale."""
    import pcb_inspector.mcp.tools as tools

    project = _serve_golden_board(monkeypatch, tmp_path, tools)
    shown = f"{project / 'flawed_board.kicad_pcb'} (live)"

    audit = inspect_project_tool(live=True)
    assert audit["project_path"] == shown
    assert audit["passed"] is False

    decoupling = check_decoupling_tool(live=True)
    assert decoupling["pcb_path"] == shown
    assert decoupling["findings"]


def test_mcp_live_failure_is_an_error_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    import pcb_inspector.mcp.tools as tools

    def unreachable(*_: Any, **__: Any) -> LiveBoard:
        raise KiCadLiveError("KiCad is not reachable over its API")

    monkeypatch.setattr(tools, "snapshot_open_board", unreachable)
    out = inspect_project_tool(live=True)
    assert out["passed"] is False
    assert "not reachable" in out["error"]


def test_mcp_needs_a_path_or_live() -> None:
    out = inspect_project_tool()
    assert out["passed"] is False
    assert "live=True" in out["error"]
