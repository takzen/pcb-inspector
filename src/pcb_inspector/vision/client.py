"""Multimodal vision clients.

Supported providers:
- Gemini (Google Generative Language API), over raw HTTP
- OpenAI chat completions, over raw HTTP
- Claude (Anthropic Messages API), through the official ``anthropic`` SDK,
  installed with the optional ``pcb-inspector[claude]`` extra
- MockVisionClient, offline, for tests and local validation

Every hosted provider accepts only raster images. The board renderer used to
produce SVG exclusively, so every real request was rejected by the provider and
Layer 3 worked with the mock client alone. Images are now checked by their
actual bytes before any request is sent.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar

from pcb_inspector import __version__
from pcb_inspector.core.exceptions import VisionReviewError
from pcb_inspector.core.models import Finding
from pcb_inspector.vision.prompts import (
    SYSTEM_PROMPT_VISION,
    build_vision_prompt,
    parse_vision_response,
)

logger = logging.getLogger(__name__)

#: Image formats every hosted vision API used here accepts. SVG is on none of
#: their lists.
PROVIDER_IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})

#: Every format the offline mock client accepts, SVG included.
ANY_IMAGE_MIME_TYPES = PROVIDER_IMAGE_MIME_TYPES | {"image/svg+xml"}

#: Seconds allowed for one HTTP round trip to a provider.
REQUEST_TIMEOUT_SECONDS = 90


def image_mime_type(path: Path) -> str:
    """Identify an image by its leading bytes, not its file extension.

    Sniffing matters here: the renderer's own mock tests write SVG text into a
    ``.png`` path, and a provider would reject such a file just as surely as a
    real SVG.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(512)
    except OSError:
        return "application/octet-stream"

    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    stripped = head.lstrip()
    if stripped.startswith(b"<") and (b"<svg" in head or stripped.startswith(b"<?xml")):
        return "image/svg+xml"
    return "application/octet-stream"


def _encode_image_base64(path: Path) -> tuple[str, str]:
    """Read an image file and return (mime_type, base64_str)."""
    encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
    return image_mime_type(path), encoded


class BaseVisionClient(ABC):
    """Abstract interface for multimodal vision model clients."""

    #: Standard environment variable holding this provider's API key.
    api_key_env: ClassVar[str | None] = None
    #: Image formats this provider will accept.
    accepted_mime_types: ClassVar[frozenset[str]] = PROVIDER_IMAGE_MIME_TYPES
    #: Attempts per request for transient failures (429, 5xx, network errors).
    max_attempts: int = 3
    #: Seconds to wait before each retry; tests set these to zero.
    retry_delays: tuple[float, ...] = (2.0, 8.0)

    def __init__(
        self,
        model_name: str,
        api_key: str | None = None,
        cache_dir: Path | str | None = None,
    ) -> None:
        self.model_name = model_name
        self.api_key = api_key
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def provider(self) -> str:
        return type(self).__name__.replace("VisionClient", "")

    def check_images(self, image_paths: list[Path]) -> None:
        """Refuse images this provider cannot accept, before any request is made.

        Raises:
            VisionReviewError: Naming the rejected file and format.
        """
        for path in image_paths:
            mime = image_mime_type(path)
            if mime not in self.accepted_mime_types:
                accepted = ", ".join(sorted(self.accepted_mime_types))
                raise VisionReviewError(
                    f"{self.provider} does not accept {mime} images ({path.name}); it takes "
                    f"{accepted}. Hosted vision needs a raster render from kicad-cli: install "
                    f"KiCad 9+ or set kicad_cli_path, or use the 'mock' model offline."
                )

    def _compute_cache_key(self, prompt: str, image_paths: list[Path], view: str) -> str:
        """Deterministic key covering everything that shapes the model's answer.

        The system prompt and tool version are part of it. Previously only the
        user prompt and images were hashed, so editing the system prompt kept
        serving answers produced under the old instructions.
        """
        hasher = hashlib.sha256()
        for part in (__version__, self.model_name, view, SYSTEM_PROMPT_VISION, prompt):
            hasher.update(part.encode("utf-8"))
            hasher.update(b"\x00")
        for path in sorted(image_paths):
            if path.exists():
                hasher.update(path.read_bytes())
        return hasher.hexdigest()

    def analyze(
        self,
        image_paths: list[Path],
        board_context: dict[str, Any] | None = None,
        view: str | None = None,
    ) -> list[Finding]:
        """Review images of one side of the board.

        Callers should pass a single view per call. The prompt names the view it
        is about, and sending both sides under a prompt that said "top" -- as
        this used to -- attributed bottom-side findings to the top.

        Raises:
            VisionReviewError: For unacceptable images or a failed request.
        """
        if not image_paths:
            return []

        self.check_images(image_paths)
        view_name = view or ("bottom" if "bottom" in image_paths[0].name.lower() else "top")
        prompt = build_vision_prompt(view_name=view_name, board_context=board_context)

        cache_file: Path | None = None
        if self.cache_dir:
            cache_file = self.cache_dir / f"{self._compute_cache_key(prompt, image_paths, view_name)}.json"
            if cache_file.exists():
                try:
                    return parse_vision_response(cache_file.read_text(encoding="utf-8"))
                except (OSError, ValueError) as err:
                    logger.debug("Ignoring unreadable vision cache entry %s: %s", cache_file, err)

        raw_response = self._execute_request(prompt, image_paths)

        if cache_file and raw_response:
            try:
                cache_file.write_text(raw_response, encoding="utf-8")
            except OSError as err:
                logger.debug("Could not write vision cache entry %s: %s", cache_file, err)

        return parse_vision_response(raw_response)

    @abstractmethod
    def _execute_request(self, prompt: str, image_paths: list[Path]) -> str:
        """Submit the images and prompt to the model and return its raw text."""
        raise NotImplementedError

    def _post_json(
        self, url: str, payload: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        """POST JSON with retries on rate limits, server errors and network faults.

        Raises:
            VisionReviewError: On a non-retryable status or once retries run out.
        """
        data = json.dumps(payload).encode("utf-8")
        request_headers = {"Content-Type": "application/json", **headers}

        for attempt in range(self.max_attempts):
            last = attempt == self.max_attempts - 1
            request = urllib.request.Request(url, data=data, headers=request_headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
                    decoded: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
                    return decoded
            except urllib.error.HTTPError as err:
                body = err.read().decode("utf-8", errors="replace")
                retryable = err.code == 429 or err.code >= 500
                if retryable and not last:
                    self._backoff(attempt, err.headers.get("Retry-After") if err.headers else None)
                    continue
                raise VisionReviewError(
                    f"{self.provider} API request failed ({err.code}): {body[:500]}"
                ) from err
            except urllib.error.URLError as err:
                if not last:
                    self._backoff(attempt, None)
                    continue
                raise VisionReviewError(f"{self.provider} API unreachable: {err.reason}") from err

        raise VisionReviewError(f"{self.provider} API request failed after retries")

    def _backoff(self, attempt: int, retry_after: str | None) -> None:
        delay = self.retry_delays[min(attempt, len(self.retry_delays) - 1)] if self.retry_delays else 0.0
        if retry_after:
            try:
                delay = max(delay, min(float(retry_after), 30.0))
            except ValueError:
                pass
        logger.info("%s request failed; retrying in %.1fs", self.provider, delay)
        if delay > 0:
            time.sleep(delay)


class GeminiVisionClient(BaseVisionClient):
    """Gemini, over the Generative Language REST API."""

    api_key_env = "GEMINI_API_KEY"

    def __init__(
        self,
        model_name: str = "gemini-3.8-flash",
        api_key: str | None = None,
        cache_dir: Path | str | None = None,
    ) -> None:
        key = api_key or os.environ.get(self.api_key_env or "", "")
        super().__init__(model_name=model_name, api_key=key, cache_dir=cache_dir)

    @property
    def generation(self) -> int:
        """Major Gemini version in the model name; unknown names count as current."""
        match = re.match(r"gemini-(\d+)", self.model_name.strip().lower())
        return int(match.group(1)) if match else 3

    def _execute_request(self, prompt: str, image_paths: list[Path]) -> str:
        if not self.api_key:
            raise ValueError(
                "GEMINI_API_KEY is not set. Please set the GEMINI_API_KEY environment variable."
            )

        parts: list[dict[str, Any]] = [{"text": prompt}]
        for img_path in image_paths:
            mime, b64 = _encode_image_base64(img_path)
            parts.append({"inlineData": {"mimeType": mime, "data": b64}})

        generation_config: dict[str, Any] = {"responseMimeType": "application/json"}
        if self.generation < 3:
            # Gemini 3 and later are tuned for their default sampling; Google
            # advises against lowering the temperature, which can make them loop.
            generation_config["temperature"] = 0.1

        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT_VISION}]},
            "generationConfig": generation_config,
        }

        # The configured name is sent as-is. It used to be rewritten from
        # gemini-3.8-flash to gemini-2.0-flash, so the default configuration
        # never reached the model it named.
        # The key travels in a header. It used to sit in the query string,
        # where it lands in proxy logs, access logs and error messages.
        result = self._post_json(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent",
            payload,
            {"x-goog-api-key": self.api_key},
        )
        candidates = result.get("candidates", [])
        if candidates:
            content_parts = candidates[0].get("content", {}).get("parts", [])
            if content_parts:
                return str(content_parts[0].get("text", ""))
        return ""


class OpenAIVisionClient(BaseVisionClient):
    """OpenAI chat completions with image input."""

    api_key_env = "OPENAI_API_KEY"

    def __init__(
        self,
        model_name: str = "gpt-6-astra",
        api_key: str | None = None,
        cache_dir: Path | str | None = None,
    ) -> None:
        key = api_key or os.environ.get(self.api_key_env or "", "")
        super().__init__(model_name=model_name, api_key=key, cache_dir=cache_dir)

    def _execute_request(self, prompt: str, image_paths: list[Path]) -> str:
        if not self.api_key:
            raise ValueError(
                "OPENAI_API_KEY is not set. Please set the OPENAI_API_KEY environment variable."
            )

        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for img_path in image_paths:
            mime, b64 = _encode_image_base64(img_path)
            content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})

        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT_VISION},
                {"role": "user", "content": content},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        }

        result = self._post_json(
            "https://api.openai.com/v1/chat/completions",
            payload,
            {"Authorization": f"Bearer {self.api_key}"},
        )
        choices = result.get("choices", [])
        if choices:
            return str(choices[0].get("message", {}).get("content", ""))
        return ""


class ClaudeVisionClient(BaseVisionClient):
    """Claude, through the Anthropic Messages API.

    This used to be a "Fable" client posting to https://api.fable.ai, an
    endpoint that does not exist; Claude Fable is an Anthropic model served by
    the Messages API. It now goes through the official SDK, which also supplies
    retries for rate limits and server errors.
    """

    api_key_env = "ANTHROPIC_API_KEY"

    #: Project identifiers mapped to Anthropic model IDs. A name that already
    #: starts with "claude-" is passed through unchanged.
    MODEL_ALIASES: ClassVar[dict[str, str]] = {
        "fable": "claude-fable-5-1",
        "fable-5": "claude-fable-5",
        "fable-5.1": "claude-fable-5-1",
        "fable-5-1": "claude-fable-5-1",
    }

    #: Models whose safety classifiers can decline a request. On these, a
    #: declined review is re-run server-side on Anthropic's recommended
    #: fallback model instead of failing the layer.
    FALLBACK_MODELS: ClassVar[frozenset[str]] = frozenset(
        {"claude-fable-5", "claude-fable-5-1", "claude-opus-5"}
    )

    #: Room for the JSON findings list; generous so a crowded board is not cut off.
    MAX_TOKENS = 16000

    def __init__(
        self,
        model_name: str = "fable-5",
        api_key: str | None = None,
        cache_dir: Path | str | None = None,
    ) -> None:
        # No environment lookup here: the SDK resolves ANTHROPIC_API_KEY, an
        # auth token, or an `ant auth login` profile on its own.
        super().__init__(model_name=model_name, api_key=api_key or None, cache_dir=cache_dir)

    @property
    def provider(self) -> str:
        return "Claude"

    @property
    def anthropic_model(self) -> str:
        """The Anthropic model ID this client's configured name refers to."""
        name = self.model_name.strip()
        if name.lower().startswith("claude-"):
            return name
        return self.MODEL_ALIASES.get(name.lower(), name)

    def _execute_request(self, prompt: str, image_paths: list[Path]) -> str:
        try:
            import anthropic
        except ImportError as err:
            raise VisionReviewError(
                "The Claude vision client needs the 'anthropic' package. "
                "Install it with: pip install 'pcb-inspector[claude]'"
            ) from err

        client = anthropic.Anthropic(api_key=self.api_key) if self.api_key else anthropic.Anthropic()

        content: list[Any] = []
        for img_path in image_paths:
            mime, b64 = _encode_image_base64(img_path)
            content.append(
                {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}}
            )
        content.append({"type": "text", "text": prompt})

        model = self.anthropic_model
        # No temperature and no thinking configuration: Claude Fable and Opus 5
        # reject sampling parameters with a 400, and run adaptive thinking by
        # default.
        request: dict[str, Any] = {
            "model": model,
            "max_tokens": self.MAX_TOKENS,
            "system": SYSTEM_PROMPT_VISION,
            "messages": [{"role": "user", "content": content}],
        }
        if model in self.FALLBACK_MODELS:
            request["betas"] = ["server-side-fallback-2026-07-01"]
            request["fallbacks"] = "default"

        try:
            response = client.beta.messages.create(**request)
        except anthropic.APIError as err:
            raise VisionReviewError(f"Claude API request failed: {err}") from err

        # A declined request is an HTTP 200 whose content may be empty or
        # partial, so the stop reason is checked before any content is read.
        if response.stop_reason == "refusal":
            details = response.stop_details
            category = getattr(details, "category", None) if details else None
            raise VisionReviewError(
                f"{model} declined to review the board image (category: {category or 'unspecified'})."
            )

        return "".join(
            getattr(block, "text", "") for block in response.content if block.type == "text"
        )


#: Former name, kept so existing imports and configuration keep working.
FableVisionClient = ClaudeVisionClient


class MockVisionClient(BaseVisionClient):
    """Deterministic offline mock client for testing and verification."""

    accepted_mime_types = ANY_IMAGE_MIME_TYPES

    def __init__(
        self,
        model_name: str = "mock-vision",
        mock_findings: list[Finding] | None = None,
        cache_dir: Path | str | None = None,
    ) -> None:
        super().__init__(model_name=model_name, api_key="mock-key", cache_dir=cache_dir)
        self.mock_findings = mock_findings

    def _execute_request(self, prompt: str, image_paths: list[Path]) -> str:
        if self.mock_findings is not None:
            items = []
            for f in self.mock_findings:
                item: dict[str, Any] = {
                    "id": f.id,
                    "title": f.title,
                    "severity": f.severity.value,
                    "category": f.category.value,
                    "components": f.components,
                    "description": f.description,
                    "rationale": f.rationale,
                    "recommendation": f.recommendation,
                }
                if f.coordinates:
                    item["coordinates"] = {
                        "x": f.coordinates[0].x,
                        "y": f.coordinates[0].y,
                        "layer": f.coordinates[0].layer,
                    }
                items.append(item)
            return json.dumps({"inspected_view": "top", "findings": items})

        # Default synthetic mock findings representing common visual flaws
        return json.dumps(
            {
                "inspected_view": "top",
                "summary": "Mock visual inspection completed successfully.",
                "findings": [
                    {
                        "id": "VIS-SILK-001",
                        "title": "Missing Pin 1 orientation indicator",
                        "severity": "WARNING",
                        "category": "SILKSCREEN",
                        "components": ["U1"],
                        "coordinates": {"x": 50.0, "y": 50.0, "layer": "F.Silkscreen"},
                        "description": "Footprint U1 has no visible Pin 1 dot or bevel marker on silkscreen.",
                        "rationale": "Operators and pick-and-place optical cameras cannot verify IC polarity before soldering.",
                        "recommendation": "Add a distinct silkscreen dot adjacent to Pin 1.",
                    },
                    {
                        "id": "VIS-ROUT-001",
                        "title": "Acute acid trap routing angle",
                        "severity": "WARNING",
                        "category": "VISION",
                        "components": ["C1"],
                        "coordinates": {"x": 52.0, "y": 50.0, "layer": "F.Cu"},
                        "description": "Trace enters pad with an angle sharper than 90 degrees.",
                        "rationale": "Etching solution can accumulate in acute corners causing trace over-etching or acid traps.",
                        "recommendation": "Route trace with 45-degree chamfers entering pad perpendicularly.",
                    },
                ],
            }
        )


def create_vision_client(
    model: str = "gemini-3.8-flash",
    api_key: str | None = None,
    cache_dir: Path | str | None = None,
) -> BaseVisionClient:
    """Factory creating the vision client for a model name."""
    m = model.lower().strip()
    if "mock" in m:
        return MockVisionClient(model_name=model, cache_dir=cache_dir)
    if "gemini" in m:
        return GeminiVisionClient(model_name=model, api_key=api_key, cache_dir=cache_dir)
    if "gpt" in m or "astra" in m or "openai" in m:
        return OpenAIVisionClient(model_name=model, api_key=api_key, cache_dir=cache_dir)
    if "fable" in m or "claude" in m:
        return ClaudeVisionClient(model_name=model, api_key=api_key, cache_dir=cache_dir)
    return GeminiVisionClient(model_name=model, api_key=api_key, cache_dir=cache_dir)
