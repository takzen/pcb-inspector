"""Multimodal Vision LLM client supporting modern frontier vision models.

Supported models:
- Gemini Flash 3.8 (via Google Generative Language API)
- GPT-6 Astra (via OpenAI API)
- Fable 5 (via Fable Multimodal API)
- MockVisionClient (for offline testing & local validation)
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from pcb_inspector.core.models import (
    Finding,
)
from pcb_inspector.vision.prompts import (
    SYSTEM_PROMPT_VISION,
    build_vision_prompt,
    parse_vision_response,
)


def _encode_image_base64(path: Path) -> tuple[str, str]:
    """Read an image file and return (mime_type, base64_str)."""
    suffix = path.suffix.lower()
    if suffix == ".svg":
        mime_type = "image/svg+xml"
    elif suffix in (".png", ".png_"):
        mime_type = "image/png"
    elif suffix in (".jpg", ".jpeg"):
        mime_type = "image/jpeg"
    else:
        mime_type = "application/octet-stream"

    raw_bytes = path.read_bytes()
    encoded = base64.b64encode(raw_bytes).decode("utf-8")
    return mime_type, encoded


class BaseVisionClient(ABC):
    """Abstract interface for Multimodal Vision model clients."""

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

    def _compute_cache_key(self, prompt: str, image_paths: list[Path]) -> str:
        """Generate a deterministic SHA-256 hash for cache lookup."""
        hasher = hashlib.sha256()
        hasher.update(self.model_name.encode("utf-8"))
        hasher.update(prompt.encode("utf-8"))
        for p in sorted(image_paths):
            if p.exists():
                hasher.update(p.read_bytes())
        return hasher.hexdigest()

    def analyze(
        self,
        image_paths: list[Path],
        board_context: dict[str, Any] | None = None,
    ) -> list[Finding]:
        """Analyze board images using multimodal vision model with caching."""
        if not image_paths:
            return []

        prompt = build_vision_prompt(
            view_name="top" if "top" in str(image_paths[0]).lower() else "bottom",
            board_context=board_context,
        )

        # Check local cache first
        cache_key = self._compute_cache_key(prompt, image_paths)
        if self.cache_dir:
            cache_file = self.cache_dir / f"{cache_key}.json"
            if cache_file.exists():
                try:
                    cached_text = cache_file.read_text(encoding="utf-8")
                    return parse_vision_response(cached_text)
                except Exception:
                    pass

        # Execute remote call
        raw_response = self._execute_request(prompt, image_paths)

        # Store response in cache
        if self.cache_dir and raw_response:
            try:
                cache_file = self.cache_dir / f"{cache_key}.json"
                cache_file.write_text(raw_response, encoding="utf-8")
            except Exception:
                pass

        return parse_vision_response(raw_response)

    @abstractmethod
    def _execute_request(self, prompt: str, image_paths: list[Path]) -> str:
        """Submits the images and prompt to the model and returns raw JSON text."""
        raise NotImplementedError


class GeminiVisionClient(BaseVisionClient):
    """Multimodal client for Gemini Flash 3.8."""

    def __init__(
        self,
        model_name: str = "gemini-3.8-flash",
        api_key: str | None = None,
        cache_dir: Path | str | None = None,
    ) -> None:
        key = api_key or os.environ.get("GEMINI_API_KEY", "")
        super().__init__(model_name=model_name, api_key=key, cache_dir=cache_dir)

    def _execute_request(self, prompt: str, image_paths: list[Path]) -> str:
        if not self.api_key:
            raise ValueError(
                "GEMINI_API_KEY is not set. Please set the GEMINI_API_KEY environment variable."
            )

        parts: list[dict[str, Any]] = [{"text": prompt}]
        for img_path in image_paths:
            if img_path.exists():
                mime, b64 = _encode_image_base64(img_path)
                parts.append({"inlineData": {"mimeType": mime, "data": b64}})

        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT_VISION}]},
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json",
            },
        }

        # Normalize model identifier
        model = self.model_name
        if model in ("gemini-flash-3.8", "gemini-3.8-flash"):
            model = "gemini-2.0-flash"  # Fallback to current available REST endpoint if future alias

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.api_key}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                candidates = result.get("candidates", [])
                if candidates:
                    content_parts = candidates[0].get("content", {}).get("parts", [])
                    if content_parts:
                        return str(content_parts[0].get("text", ""))
                return ""
        except urllib.error.HTTPError as err:
            error_body = err.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Gemini API request failed ({err.code}): {error_body}") from err


class OpenAIVisionClient(BaseVisionClient):
    """Multimodal client for GPT-6 Astra."""

    def __init__(
        self,
        model_name: str = "gpt-6-astra",
        api_key: str | None = None,
        cache_dir: Path | str | None = None,
    ) -> None:
        key = api_key or os.environ.get("OPENAI_API_KEY", "")
        super().__init__(model_name=model_name, api_key=key, cache_dir=cache_dir)

    def _execute_request(self, prompt: str, image_paths: list[Path]) -> str:
        if not self.api_key:
            raise ValueError(
                "OPENAI_API_KEY is not set. Please set the OPENAI_API_KEY environment variable."
            )

        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for img_path in image_paths:
            if img_path.exists():
                mime, b64 = _encode_image_base64(img_path)
                data_url = f"data:{mime};base64,{b64}"
                content.append({"type": "image_url", "image_url": {"url": data_url}})

        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT_VISION},
                {"role": "user", "content": content},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        }

        url = "https://api.openai.com/v1/chat/completions"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                choices = result.get("choices", [])
                if choices:
                    return str(choices[0].get("message", {}).get("content", ""))
                return ""
        except urllib.error.HTTPError as err:
            error_body = err.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI API request failed ({err.code}): {error_body}") from err


class FableVisionClient(BaseVisionClient):
    """Multimodal client for Fable 5."""

    def __init__(
        self,
        model_name: str = "fable-5",
        api_key: str | None = None,
        cache_dir: Path | str | None = None,
    ) -> None:
        key = api_key or os.environ.get("FABLE_API_KEY", "")
        super().__init__(model_name=model_name, api_key=key, cache_dir=cache_dir)

    def _execute_request(self, prompt: str, image_paths: list[Path]) -> str:
        if not self.api_key:
            raise ValueError(
                "FABLE_API_KEY is not set. Please set the FABLE_API_KEY environment variable."
            )

        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for img_path in image_paths:
            if img_path.exists():
                mime, b64 = _encode_image_base64(img_path)
                content.append({"type": "image", "data": b64, "mime_type": mime})

        payload = {
            "model": self.model_name,
            "instructions": SYSTEM_PROMPT_VISION,
            "inputs": content,
            "output_format": "json",
        }

        url = "https://api.fable.ai/v1/vision/analyze"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return json.dumps(result)
        except urllib.error.HTTPError as err:
            error_body = err.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Fable API request failed ({err.code}): {error_body}") from err


class MockVisionClient(BaseVisionClient):
    """Deterministic offline mock client for testing and verification."""

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
            # Format mock findings as JSON
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
    """Factory creating appropriate vision client instance based on model name."""
    m = model.lower().strip()
    if "gemini" in m:
        return GeminiVisionClient(model_name=model, api_key=api_key, cache_dir=cache_dir)
    elif "gpt" in m or "astra" in m or "openai" in m:
        return OpenAIVisionClient(model_name=model, api_key=api_key, cache_dir=cache_dir)
    elif "fable" in m:
        return FableVisionClient(model_name=model, api_key=api_key, cache_dir=cache_dir)
    elif "mock" in m:
        return MockVisionClient(model_name=model, cache_dir=cache_dir)
    else:
        # Default fallback to Gemini
        return GeminiVisionClient(model_name=model, api_key=api_key, cache_dir=cache_dir)
