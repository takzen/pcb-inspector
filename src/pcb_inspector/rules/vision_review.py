"""Multimodal Vision AI inspection rule for PCB layouts."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.core.models import Finding, FindingCategory, Severity
from pcb_inspector.kicad.cli_wrapper import KiCadCli
from pcb_inspector.kicad.pcb_model import PcbBoard, load_pcb_board
from pcb_inspector.rules.base import BaseRule
from pcb_inspector.vision.client import BaseVisionClient, create_vision_client
from pcb_inspector.vision.renderer import BoardRenderer


class VisionReviewRule(BaseRule):
    """Performs multimodal AI visual inspection of PCB layouts (Layer 3).

    Inspects silkscreen polarity markers, Pin 1 indicators, reference designator legibility,
    acid traps, component crowding, and mechanical edge clearances using modern vision models
    (Gemini Flash 3.8, Fable 5, GPT-6 Astra).
    """

    rule_id = "VISION-AI-001"
    name = "Multimodal Visual Review"
    category = FindingCategory.VISION
    default_severity = Severity.WARNING
    description = (
        "Audits PCB layout visuals using frontier multimodal vision models to catch silkscreen flaws, "
        "missing polarity/Pin 1 markings, acute trace acid traps, and mechanical edge collisions."
    )

    def __init__(self, client: BaseVisionClient | None = None) -> None:
        self.client = client

    def evaluate(self, context: Any, config: InspectorConfig) -> list[Finding]:
        # Only run if explicitly enabled via config/CLI
        is_enabled = config.enable_vision
        custom_cfg = config.custom_rules.get(self.rule_id, {})
        if "enabled" in custom_cfg:
            is_enabled = bool(custom_cfg["enabled"])

        if not is_enabled:
            return []

        # Resolve PCB path
        pcb_path: Path | None = None
        board: PcbBoard | None = None

        if isinstance(context, PcbBoard):
            board = context
        elif isinstance(context, (str, Path)):
            p = Path(context)
            if p.is_file() and p.suffix == ".kicad_pcb":
                pcb_path = p
            elif p.is_file():
                candidate = p.with_suffix(".kicad_pcb")
                if candidate.exists():
                    pcb_path = candidate
            elif p.is_dir():
                for cand in p.glob("*.kicad_pcb"):
                    pcb_path = cand
                    break

        if pcb_path is None and board is None:
            return []

        # Load board model if not already provided
        if board is None and pcb_path is not None:
            try:
                board = load_pcb_board(pcb_path)
            except Exception:
                board = None

        # Render PCB views
        cli = KiCadCli(config.kicad_cli_path) if config.kicad_cli_path else None
        renderer = BoardRenderer(kicad_cli=cli)

        try:
            if pcb_path:
                render_map = renderer.render_layers(pcb_path)
            else:
                # Direct render from PcbBoard into temp file
                import tempfile

                temp_dir = Path(tempfile.mkdtemp(prefix="pcb_render_"))
                top_file = temp_dir / "board_top.svg"
                bot_file = temp_dir / "board_bottom.svg"
                assert board is not None
                renderer._render_programmatic_svg(board, top_file, side="top")
                renderer._render_programmatic_svg(board, bot_file, side="bottom")
                render_map = {"top": top_file, "bottom": bot_file}
        except Exception as err:
            return [
                Finding(
                    id="VIS-RENDER-ERR",
                    title="Board Image Rendering Failed",
                    severity=Severity.WARNING,
                    category=FindingCategory.VISION,
                    description=f"Failed to render PCB layers for visual inspection: {err}",
                    rule_id=self.rule_id,
                    rationale="Visual inspection requires valid 2D SVG or PNG renders of the board layers.",
                    recommendation="Ensure the .kicad_pcb file is syntactically valid and readable.",
                )
            ]

        # Context summary for LLM prompt
        board_ctx: dict[str, Any] = {}
        if board:
            board_ctx = {
                "footprints_count": len(board.footprints),
                "tracks_count": len(board.tracks),
                "components": list(board.footprints.keys()),
            }

        # Resolve or instantiate Vision Client
        client = self.client
        if client is None:
            model_name = config.vision_model
            env_var = config.vision_api_key_env
            api_key = os.environ.get(env_var)

            if not api_key and "mock" not in model_name.lower():
                # If vision was requested but no API key is present in environment
                return [
                    Finding(
                        id="VIS-NO-API-KEY",
                        title=f"Vision API Key Missing ({env_var})",
                        severity=Severity.WARNING,
                        category=FindingCategory.VISION,
                        description=(
                            f"Visual inspection was requested with model '{model_name}', but the required "
                            f"environment variable '{env_var}' is not configured."
                        ),
                        rule_id=self.rule_id,
                        rationale="Multimodal vision inspection requires access to an external vision AI provider.",
                        recommendation=(
                            f"Export your API key before running inspection, e.g.: "
                            f"export {env_var}=your_key_here (or set vision_model to 'mock' for offline testing)."
                        ),
                    )
                ]

            client = create_vision_client(
                model=model_name,
                api_key=api_key,
                cache_dir=config.vision_cache_dir,
            )

        # Analyze rendered images
        try:
            image_list = [render_map["top"], render_map["bottom"]]
            findings = client.analyze(image_list, board_context=board_ctx)
            # Ensure rule_id is consistently tagged
            for f in findings:
                f.rule_id = self.rule_id
            return findings
        except Exception as err:
            return [
                Finding(
                    id="VIS-API-CALL-ERR",
                    title="Vision Model Analysis Failed",
                    severity=Severity.WARNING,
                    category=FindingCategory.VISION,
                    description=f"Multimodal vision model query encountered an error: {err}",
                    rule_id=self.rule_id,
                    rationale="External API network timeout, rate limit, or format error.",
                    recommendation="Check your network connection, API key quota, or switch to a different vision model.",
                )
            ]
