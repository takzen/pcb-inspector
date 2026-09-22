"""Audit the board open in a running KiCad, unsaved edits included.

KiCad 10 serves an IPC API on a local socket. Tools that edit a board through
it, such as Konnect, change the design in the editor and leave the file on
disk as it was until someone saves. Auditing that file would review the board
as it was, not as it is, so a repair loop would never see its own repairs.

The open board is fetched as board-file text and written into a scratch copy
of its project, beside the files kicad-cli reads for DRC, ERC and schematic
parity. The rest of the pipeline then runs on that copy unchanged.
"""

from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pcb_inspector.core.exceptions import KiCadLiveError

if TYPE_CHECKING:
    from kipy.board import Board

logger = logging.getLogger(__name__)

#: Project files copied beside the snapshot. Library tables and project-local
#: libraries matter: without them DRC reports every custom footprint as
#: missing from its library, which a real project audit showed.
PROJECT_FILE_PATTERNS = (
    "*.kicad_pro",
    "*.kicad_sch",
    "*.kicad_dru",
    "*.kicad_sym",
    "fp-lib-table",
    "sym-lib-table",
    ".pcb-inspector.yaml",
    "rules.yaml",
)

#: KiCad answers "busy" while a dialog or an interactive tool is active.
BUSY_RETRY_SECONDS = 10.0
BUSY_POLL_SECONDS = 0.5


@dataclass(frozen=True)
class LiveBoard:
    """A snapshot of the board open in KiCad."""

    #: The board file KiCad has open. Its contents on disk may be stale.
    board_path: Path
    #: The snapshot written into the scratch project.
    snapshot_path: Path

    @property
    def audit_target(self) -> Path:
        """What to audit: the scratch project file when there is one, else the board."""
        project = self.snapshot_path.with_suffix(".kicad_pro")
        return project if project.exists() else self.snapshot_path


def _open_board(socket_path: str | None, timeout_ms: int) -> tuple[Board, str]:
    """Connect to KiCad and fetch the open board and its current contents.

    Raises:
        KiCadLiveError: If kicad-python is missing or KiCad cannot provide a board.
    """
    try:
        from kipy import KiCad
        from kipy.errors import ApiError, ConnectionError
        from kipy.proto.common import ApiStatusCode
    except ImportError as err:
        raise KiCadLiveError(
            "Auditing the board open in KiCad needs the 'kicad-python' package. "
            "Install it with: pip install 'pcb-inspector[live]'"
        ) from err

    deadline = time.monotonic() + BUSY_RETRY_SECONDS
    while True:
        try:
            board = KiCad(socket_path=socket_path, timeout_ms=timeout_ms).get_board()
            return board, board.get_as_string()
        except ConnectionError as err:
            raise KiCadLiveError(
                f"KiCad is not reachable over its API ({err}). Start KiCad with the board "
                "open and enable Preferences > Plugins > Enable KiCad API."
            ) from err
        except ApiError as err:
            if err.code == ApiStatusCode.AS_BUSY and time.monotonic() < deadline:
                time.sleep(BUSY_POLL_SECONDS)
                continue
            if err.code == ApiStatusCode.AS_BUSY:
                raise KiCadLiveError(
                    "KiCad stayed busy; close any open dialog or finish the active tool "
                    "in the PCB editor and try again."
                ) from err
            raise KiCadLiveError(f"KiCad has no board to audit: {err}") from err


def snapshot_open_board(
    dest_dir: Path, socket_path: str | None = None, timeout_ms: int = 10000
) -> LiveBoard:
    """Write the board open in KiCad, and its project files, into ``dest_dir``.

    Raises:
        KiCadLiveError: If the board cannot be fetched or its project cannot be read.
    """
    board, contents = _open_board(socket_path, timeout_ms)
    document = board.document
    project_dir = Path(document.project.path)
    board_path = project_dir / document.board_filename
    if not project_dir.is_dir():
        raise KiCadLiveError(f"KiCad reports a project folder that does not exist: {project_dir}")

    dest_dir.mkdir(parents=True, exist_ok=True)
    for pattern in PROJECT_FILE_PATTERNS:
        for source in project_dir.glob(pattern):
            if source.is_file():
                shutil.copy2(source, dest_dir / source.name)
    for library in project_dir.glob("*.pretty"):
        if library.is_dir():
            shutil.copytree(library, dest_dir / library.name, dirs_exist_ok=True)

    snapshot = dest_dir / document.board_filename
    snapshot.write_text(contents, encoding="utf-8")
    logger.info("Snapshot of %s taken from the running KiCad", board_path)
    return LiveBoard(board_path=board_path, snapshot_path=snapshot)
