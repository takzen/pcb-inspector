"""Board rendering pipeline for multimodal vision inspections.

With kicad-cli available, each side is raytraced to PNG by ``kicad-cli pcb
render``: the real board as KiCad draws it, silkscreen, component bodies and
all. That is the only input a hosted vision model is given.

Without kicad-cli, an internal SVG compositor draws copper, pads and reference
designators. It carries no silkscreen, so it cannot answer the questions the
vision prompt asks about polarity marks and Pin 1 indicators, and it is SVG,
which neither Gemini nor OpenAI accepts. It is kept for the offline mock
client only.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from pcb_inspector.core.exceptions import KiCadCliExecutionError
from pcb_inspector.kicad.cli_wrapper import KiCadCli
from pcb_inspector.kicad.pcb_model import PcbBoard, load_pcb_board

logger = logging.getLogger(__name__)

#: Raster size for vision renders. Large enough to read a 0402 refdes, small
#: enough to stay well inside provider image limits.
RENDER_WIDTH = 1600
RENDER_HEIGHT = 1200

#: kicad-cli raytraces, which takes a few seconds per side on a dense board.
RENDER_TIMEOUT_SECONDS = 180


class BoardRenderer:
    """Renders PCB layouts into visual graphics (SVG/PNG) suitable for Multimodal LLMs."""

    def __init__(
        self, kicad_cli: KiCadCli | None = None, auto_detect: bool = True
    ) -> None:
        self.kicad_cli: KiCadCli | None
        if kicad_cli is not None:
            self.kicad_cli = kicad_cli
        elif auto_detect:
            self.kicad_cli = KiCadCli.detect()
        else:
            self.kicad_cli = None

    def render_layers(
        self,
        pcb_path: Path | str,
        output_dir: Path | str | None = None,
    ) -> dict[str, Path]:
        """Render the top and bottom of a board.

        Returns a mapping of view name ('top', 'bottom') to file. The files are
        PNG when kicad-cli rendered them and SVG when the offline fallback did;
        callers that talk to a hosted model must check which they got, since
        only the PNG is accepted there.
        """
        path = Path(pcb_path)
        if not path.exists():
            raise FileNotFoundError(f"PCB file not found: {path}")

        out_dir = Path(output_dir) if output_dir else Path(tempfile.mkdtemp(prefix="pcb_render_"))
        out_dir.mkdir(parents=True, exist_ok=True)

        if self.kicad_cli and self.kicad_cli.is_available():
            renders = {
                "top": out_dir / f"{path.stem}_top.png",
                "bottom": out_dir / f"{path.stem}_bottom.png",
            }
            try:
                for side, out in renders.items():
                    self._render_with_cli(path, out, side=side)
                if all(out.exists() and out.stat().st_size > 0 for out in renders.values()):
                    return renders
                logger.warning("kicad-cli render produced no image; using the offline renderer.")
            except KiCadCliExecutionError as err:
                logger.warning("kicad-cli render failed (%s); using the offline renderer.", err)

        # Offline fallback: SVG, usable by the mock client only.
        board = load_pcb_board(path)
        renders = {
            "top": out_dir / f"{path.stem}_top.svg",
            "bottom": out_dir / f"{path.stem}_bottom.svg",
        }
        for side, out in renders.items():
            self._render_programmatic_svg(board, out, side=side)
        return renders

    def _render_with_cli(self, pcb_path: Path, output_file: Path, side: str = "top") -> None:
        """Raytrace one side of the board to PNG with ``kicad-cli pcb render``.

        Goes through KiCadCli's runner, so it inherits the timeout and exit-code
        checks. It previously called subprocess.run directly with neither, so a
        stalled render hung the audit.

        Raises:
            KiCadCliExecutionError: If kicad-cli fails, times out, or is missing.
        """
        if not self.kicad_cli:
            raise KiCadCliExecutionError("kicad-cli is not configured")

        cmd = [
            str(self.kicad_cli.executable),
            "pcb",
            "render",
            "--side",
            side,
            "--width",
            str(RENDER_WIDTH),
            "--height",
            str(RENDER_HEIGHT),
            "--quality",
            "basic",
            "--output",
            str(output_file),
            str(pcb_path),
        ]
        action = f"3D render ({side}) of {pcb_path.name}"
        res = self.kicad_cli._run(cmd, action=action, timeout=RENDER_TIMEOUT_SECONDS)
        self.kicad_cli._check_returncode(res, action)

    def _render_programmatic_svg(
        self,
        board: PcbBoard,
        output_file: Path,
        side: str = "top",
    ) -> None:
        """Generate a high-fidelity SVG render from parsed PcbBoard data."""
        # Calculate bounding box from footprints, tracks, and zones
        min_x, min_y = 0.0, 0.0
        max_x, max_y = 100.0, 100.0

        all_x: list[float] = []
        all_y: list[float] = []

        for fp in board.footprints.values():
            all_x.append(fp.at_x)
            all_y.append(fp.at_y)
            for pad in fp.pads:
                all_x.append(pad.at_x)
                all_y.append(pad.at_y)

        for track in board.tracks:
            all_x.extend([track.start_x, track.end_x])
            all_y.extend([track.start_y, track.end_y])

        for zone in board.zones:
            for pt in zone.points:
                all_x.append(pt[0])
                all_y.append(pt[1])

        if all_x and all_y:
            padding = 10.0
            min_x = max(0.0, min(all_x) - padding)
            min_y = max(0.0, min(all_y) - padding)
            max_x = max(all_x) + padding
            max_y = max(all_y) + padding

        width = max(20.0, max_x - min_x)
        height = max(20.0, max_y - min_y)

        target_layer = "F.Cu" if side == "top" else "B.Cu"

        svg_parts: list[str] = [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{min_x:.1f} {min_y:.1f} {width:.1f} {height:.1f}" '
            f'width="{width * 5:.1f}" height="{height * 5:.1f}">',
            '<defs>',
            '  <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">',
            '    <feGaussianBlur stdDeviation="0.4" result="blur" />',
            '    <feMerge>',
            '      <feMergeNode in="blur" />',
            '      <feMergeNode in="SourceGraphic" />',
            '    </feMerge>',
            '  </filter>',
            '</defs>',
            '<!-- PCB Substrate -->',
            f'<rect x="{min_x:.1f}" y="{min_y:.1f}" width="{width:.1f}" height="{height:.1f}" '
            'fill="#19281f" stroke="#2a4533" stroke-width="0.5" rx="2" />',
        ]

        # Render copper zones
        for zone in board.zones:
            if zone.layer == target_layer and zone.points:
                points_str = " ".join(f"{x:.2f},{y:.2f}" for x, y in zone.points)
                svg_parts.append(
                    f'<polygon points="{points_str}" fill="#245037" opacity="0.6" stroke="#2f6c48" stroke-width="0.2" />'
                )

        # Render tracks
        for track in board.tracks:
            if track.layer == target_layer:
                svg_parts.append(
                    f'<line x1="{track.start_x:.2f}" y1="{track.start_y:.2f}" '
                    f'x2="{track.end_x:.2f}" y2="{track.end_y:.2f}" '
                    f'stroke="#c8963e" stroke-width="{track.width:.2f}" stroke-linecap="round" />'
                )

        # Render vias
        for via in board.vias:
            svg_parts.append(
                f'<circle cx="{via.x:.2f}" cy="{via.y:.2f}" r="{via.size / 2.0:.2f}" '
                'fill="#d4af37" stroke="#8c6d1f" stroke-width="0.1" />'
            )
            svg_parts.append(
                f'<circle cx="{via.x:.2f}" cy="{via.y:.2f}" r="{via.drill / 2.0:.2f}" fill="#19281f" />'
            )

        # Render footprints & pads
        for fp in board.footprints.values():
            if fp.layer == target_layer:
                # Component silkscreen courtyard outline
                svg_parts.append(f'<!-- Component {fp.refdes} -->')
                # Render pads
                for pad in fp.pads:
                    if target_layer in pad.layers:
                        # Pad copper + tinning
                        svg_parts.append(
                            f'<rect x="{pad.at_x - pad.size_w / 2.0:.2f}" '
                            f'y="{pad.at_y - pad.size_h / 2.0:.2f}" '
                            f'width="{pad.size_w:.2f}" height="{pad.size_h:.2f}" '
                            'fill="#d8d8d8" stroke="#c8963e" stroke-width="0.1" rx="0.1" />'
                        )

                # Reference Designator text
                svg_parts.append(
                    f'<text x="{fp.at_x:.2f}" y="{fp.at_y - 1.5:.2f}" '
                    'fill="#ffffff" font-family="monospace" font-size="1.2" font-weight="bold" '
                    f'text-anchor="middle">{fp.refdes}</text>'
                )

                # No Pin 1 marker is drawn. This renderer reads no silkscreen,
                # so any marker would be invented. It used to draw a dot beside
                # pad 1 of every IC unconditionally -- fabricating exactly the
                # evidence the vision prompt asks the model to look for.

        svg_parts.append("</svg>")
        output_file.write_text("\n".join(svg_parts), encoding="utf-8")
