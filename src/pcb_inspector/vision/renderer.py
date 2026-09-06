"""Board rendering pipeline for multimodal vision inspections.

Generates 2D SVG vector renders of top and bottom PCB layers either via
`kicad-cli pcb export svg` or through an internal SVG vector compositor.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from pcb_inspector.kicad.cli_wrapper import KiCadCli
from pcb_inspector.kicad.pcb_model import PcbBoard, load_pcb_board


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
        """Render front and back views of a PCB into SVG files.

        Returns a dictionary mapping view names ('top', 'bottom') to file paths.
        """
        path = Path(pcb_path)
        if not path.exists():
            raise FileNotFoundError(f"PCB file not found: {path}")

        out_dir = Path(output_dir) if output_dir else Path(tempfile.mkdtemp(prefix="pcb_render_"))
        out_dir.mkdir(parents=True, exist_ok=True)

        top_out = out_dir / f"{path.stem}_top.svg"
        bot_out = out_dir / f"{path.stem}_bottom.svg"

        # Attempt kicad-cli export first if binary is available
        cli_success = False
        if self.kicad_cli and self.kicad_cli.is_available():
            try:
                self._render_with_cli(path, top_out, side="top")
                self._render_with_cli(path, bot_out, side="bottom")
                if top_out.exists() and bot_out.exists() and top_out.stat().st_size > 0:
                    cli_success = True
            except Exception:
                cli_success = False

        if not cli_success:
            # Fallback to internal programmatic vector renderer
            board = load_pcb_board(path)
            self._render_programmatic_svg(board, top_out, side="top")
            self._render_programmatic_svg(board, bot_out, side="bottom")

        return {"top": top_out, "bottom": bot_out}

    def _render_with_cli(self, pcb_path: Path, output_file: Path, side: str = "top") -> None:
        """Render a composite layer SVG using kicad-cli."""
        if not self.kicad_cli:
            raise RuntimeError("KiCad CLI not configured")

        layers = "F.Cu,F.Silkscreen,Edge.Cuts" if side == "top" else "B.Cu,B.Silkscreen,Edge.Cuts"
        cmd = [
            str(self.kicad_cli.executable),
            "pcb",
            "export",
            "svg",
            "--layers",
            layers,
            "--page-size-mode",
            "2",
            "--exclude-drawing-sheet",
            "--mode-single",
            "--output",
            str(output_file),
        ]
        if side == "bottom":
            cmd.append("--mirror")
        cmd.append(str(pcb_path))

        subprocess.run(cmd, capture_output=True, text=True, check=True)

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

                # Pin 1 indicator if IC
                if fp.is_ic and fp.pads:
                    pad1 = fp.get_pad("1") or fp.pads[0]
                    # Draw a small silkscreen dot next to Pin 1
                    dot_x = pad1.at_x - (1.0 if pad1.at_x <= fp.at_x else -1.0)
                    dot_y = pad1.at_y
                    svg_parts.append(
                        f'<circle cx="{dot_x:.2f}" cy="{dot_y:.2f}" r="0.3" fill="#ffffff" />'
                    )

        svg_parts.append("</svg>")
        output_file.write_text("\n".join(svg_parts), encoding="utf-8")
