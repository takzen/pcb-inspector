"""Multimodal Vision AI inspection rule for PCB layouts."""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.core.models import Finding, FindingCategory, Severity
from pcb_inspector.kicad.cli_wrapper import KiCadCli
from pcb_inspector.kicad.pcb_model import PcbBoard
from pcb_inspector.rules.base import BaseRule
from pcb_inspector.rules.board_loader import find_pcb_file, resolve_board
from pcb_inspector.vision.client import (
    BaseVisionClient,
    ClaudeVisionClient,
    MockVisionClient,
    create_vision_client,
)
from pcb_inspector.vision.renderer import BoardRenderer

logger = logging.getLogger(__name__)

#: Board sides reviewed, each in its own request.
VIEWS = ("top", "bottom")


class VisionReviewRule(BaseRule):
    """Performs multimodal AI visual inspection of PCB layouts (Layer 3).

    Inspects silkscreen polarity markers, Pin 1 indicators, reference designator legibility,
    acid traps, component crowding, and mechanical edge clearances using hosted vision models
    (Gemini, OpenAI, or Claude).
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

    def is_enabled(self, config: InspectorConfig) -> bool:
        """Vision additionally requires the global enable_vision switch.

        ``custom_rules.VISION-AI-001.enabled`` may override it in either
        direction, matching the behaviour documented in the example config.
        """
        override = self.settings(config).get("enabled")
        if override is not None:
            return bool(override)
        return config.enable_vision

    def evaluate(self, context: Any, config: InspectorConfig) -> list[Finding]:
        # Checked here as well as in the registry, since library callers and
        # tests invoke the rule directly.
        if not self.is_enabled(config):
            return []

        pcb_path = find_pcb_file(context)
        board: PcbBoard | None = context if isinstance(context, PcbBoard) else None
        if pcb_path is None and board is None:
            return []

        # Settle the client before rendering: a missing key should not cost a
        # raytrace of both sides first.
        client = self.client
        if client is None:
            client_or_finding = self._build_client(config)
            if isinstance(client_or_finding, Finding):
                return [client_or_finding]
            client = client_or_finding

        if board is None:
            try:
                board = resolve_board(context)
            except Exception as err:
                logger.debug("Board model unavailable for vision context: %s", err)

        try:
            render_map = self._render(pcb_path, board, config)
        except Exception as err:
            return [self._render_failure(err)]

        board_ctx: dict[str, Any] = {}
        if board:
            board_ctx = {
                "footprints_count": len(board.footprints),
                "tracks_count": len(board.tracks),
                "components": list(board.footprints.keys()),
            }

        findings: list[Finding] = []
        for view in VIEWS:
            image = render_map.get(view)
            if image is None:
                continue
            try:
                view_findings = client.analyze([image], board_context=board_ctx, view=view)
            except Exception as err:
                # Keep what the other side produced; report this side's failure.
                findings.append(self._call_failure(view, err))
                continue
            for f in view_findings:
                f.rule_id = self.rule_id
                # Both sides are reviewed independently and a model may reuse an
                # ID across them, so the view is made part of the ID.
                f.id = f"{f.id}-{view.upper()}"
                f.raw_data.setdefault("view", view)
                findings.append(f)
        return findings

    def _build_client(self, config: InspectorConfig) -> BaseVisionClient | Finding:
        """Create the configured client, or a finding explaining why it cannot be.

        The API key variable is the chosen provider's own unless overridden.
        It used to be a single setting defaulting to GEMINI_API_KEY for every
        provider, so an OpenAI model with only OPENAI_API_KEY set was refused.
        """
        model_name = config.vision_model
        client = create_vision_client(model=model_name, cache_dir=config.vision_cache_dir)
        if isinstance(client, MockVisionClient):
            return client

        env_var = config.vision_api_key_env or client.api_key_env
        api_key = os.environ.get(env_var) if env_var else None

        # The Anthropic SDK also accepts an auth token or an `ant auth login`
        # profile, so an unset variable does not prove there are no credentials.
        if not api_key and not isinstance(client, ClaudeVisionClient):
            return Finding(
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

        return create_vision_client(
            model=model_name, api_key=api_key, cache_dir=config.vision_cache_dir
        )

    @staticmethod
    def _render(
        pcb_path: Path | None, board: PcbBoard | None, config: InspectorConfig
    ) -> dict[str, Path]:
        cli = KiCadCli(config.kicad_cli_path) if config.kicad_cli_path else None
        renderer = BoardRenderer(kicad_cli=cli)
        if pcb_path is not None:
            return renderer.render_layers(pcb_path)

        # An in-memory board has no file for kicad-cli to render, so only the
        # offline SVG is possible; hosted clients will decline it with a clear
        # message rather than send it.
        assert board is not None
        temp_dir = Path(tempfile.mkdtemp(prefix="pcb_render_"))
        renders = {view: temp_dir / f"board_{view}.svg" for view in VIEWS}
        for view, out in renders.items():
            renderer._render_programmatic_svg(board, out, side=view)
        return renders

    def _render_failure(self, err: Exception) -> Finding:
        return Finding(
            id="VIS-RENDER-ERR",
            title="Board Image Rendering Failed",
            severity=Severity.WARNING,
            category=FindingCategory.VISION,
            description=f"Failed to render PCB layers for visual inspection: {err}",
            rule_id=self.rule_id,
            rationale="Visual inspection requires a render of each side of the board.",
            recommendation="Ensure the .kicad_pcb file is syntactically valid and readable.",
        )

    def _call_failure(self, view: str, err: Exception) -> Finding:
        return Finding(
            id=f"VIS-API-CALL-ERR-{view.upper()}",
            title=f"Vision Model Analysis Failed ({view} side)",
            severity=Severity.WARNING,
            category=FindingCategory.VISION,
            description=f"Multimodal vision review of the {view} side encountered an error: {err}",
            rule_id=self.rule_id,
            rationale="External API network timeout, rate limit, refusal, or unsupported input.",
            recommendation=(
                "Check your network connection and API key quota, confirm kicad-cli is "
                "installed so the board can be rendered to PNG, or switch vision model."
            ),
            raw_data={"view": view, "error": str(err)},
        )
