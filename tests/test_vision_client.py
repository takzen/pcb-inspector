"""Unit tests for multimodal vision clients.

Several of the earlier tests here sent an SVG to the Gemini client and passed,
because HTTP was mocked. No hosted provider accepts SVG, so those tests were
confirming a request that would always have been rejected. Images are now PNG
wherever a hosted client is exercised, and a dedicated test asserts SVG is
refused before any request is made.
"""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pcb_inspector.core.exceptions import VisionReviewError
from pcb_inspector.core.models import Finding, FindingCategory, Severity
from pcb_inspector.vision import client as client_mod
from pcb_inspector.vision.client import (
    ClaudeVisionClient,
    FableVisionClient,
    GeminiVisionClient,
    MockVisionClient,
    OpenAIVisionClient,
    create_vision_client,
    image_mime_type,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _png(tmp_path: Path, name: str = "board_top.png") -> Path:
    path = tmp_path / name
    path.write_bytes(PNG_BYTES)
    return path


def _svg(tmp_path: Path, name: str = "board_top.svg") -> Path:
    path = tmp_path / name
    path.write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>', encoding="utf-8")
    return path


def _http_response(payload: dict[str, Any]) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    resp.__enter__.return_value = resp
    return resp


GEMINI_OK = {
    "candidates": [
        {
            "content": {
                "parts": [
                    {
                        "text": json.dumps(
                            {
                                "inspected_view": "top",
                                "findings": [
                                    {
                                        "id": "VIS-GEM-001",
                                        "title": "Gemini finding",
                                        "severity": "WARNING",
                                        "category": "VISION",
                                        "description": "Found via Gemini",
                                    }
                                ],
                            }
                        )
                    }
                ]
            }
        }
    ]
}


# --------------------------------------------------------------------------
# Factory and image sniffing
# --------------------------------------------------------------------------


def test_create_vision_client_factory() -> None:
    assert isinstance(create_vision_client("gemini-3.8-flash"), GeminiVisionClient)
    assert isinstance(create_vision_client("gpt-6-astra"), OpenAIVisionClient)
    assert isinstance(create_vision_client("fable-5"), ClaudeVisionClient)
    assert isinstance(create_vision_client("claude-opus-5"), ClaudeVisionClient)
    assert isinstance(create_vision_client("mock"), MockVisionClient)


def test_fable_name_is_kept_as_an_alias() -> None:
    assert FableVisionClient is ClaudeVisionClient


def test_image_type_is_sniffed_from_bytes(tmp_path: Path) -> None:
    assert image_mime_type(_png(tmp_path)) == "image/png"
    assert image_mime_type(_svg(tmp_path)) == "image/svg+xml"

    disguised = tmp_path / "looks_like.png"
    disguised.write_text("<svg>mocked</svg>", encoding="utf-8")
    assert image_mime_type(disguised) == "image/svg+xml"

    jpeg = tmp_path / "a.jpg"
    jpeg.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 16)
    assert image_mime_type(jpeg) == "image/jpeg"


# --------------------------------------------------------------------------
# P1-10b: unsupported images never reach a provider
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "client",
    [
        GeminiVisionClient(api_key="k"),
        OpenAIVisionClient(api_key="k"),
    ],
    ids=["gemini", "openai"],
)
def test_svg_is_refused_before_any_request(client: Any, tmp_path: Path) -> None:
    with patch("urllib.request.urlopen") as urlopen:
        with pytest.raises(VisionReviewError, match="does not accept image/svg"):
            client.analyze([_svg(tmp_path)])
        urlopen.assert_not_called()


def test_svg_is_refused_by_claude_before_any_request(tmp_path: Path) -> None:
    client = ClaudeVisionClient(api_key="k")
    with patch.object(client, "_execute_request") as execute:
        with pytest.raises(VisionReviewError, match="does not accept image/svg"):
            client.analyze([_svg(tmp_path)])
        execute.assert_not_called()


def test_mock_client_still_accepts_svg(tmp_path: Path) -> None:
    findings = MockVisionClient().analyze([_svg(tmp_path)])
    assert findings and findings[0].id.startswith("VIS-")


# --------------------------------------------------------------------------
# Missing credentials
# --------------------------------------------------------------------------


def test_gemini_client_missing_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="GEMINI_API_KEY is not set"):
        GeminiVisionClient(api_key="").analyze([_png(tmp_path)])


def test_openai_client_missing_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENAI_API_KEY is not set"):
        OpenAIVisionClient(api_key="").analyze([_png(tmp_path)])


# --------------------------------------------------------------------------
# Gemini request shape
# --------------------------------------------------------------------------


def test_gemini_client_mocked_request(tmp_path: Path) -> None:
    client = GeminiVisionClient(api_key="fake-key")
    with patch("urllib.request.urlopen", return_value=_http_response(GEMINI_OK)):
        findings = client.analyze([_png(tmp_path)])
    assert [f.id for f in findings] == ["VIS-GEM-001"]


def test_gemini_key_travels_in_a_header_not_the_url(tmp_path: Path) -> None:
    """A key in the query string ends up in proxy and access logs."""
    client = GeminiVisionClient(api_key="secret-key")
    with patch("urllib.request.urlopen", return_value=_http_response(GEMINI_OK)) as urlopen:
        client.analyze([_png(tmp_path)])

    request = urlopen.call_args.args[0]
    assert "secret-key" not in request.full_url
    assert request.get_header("X-goog-api-key") == "secret-key"


def test_gemini_sends_the_image_as_png(tmp_path: Path) -> None:
    client = GeminiVisionClient(api_key="k")
    with patch("urllib.request.urlopen", return_value=_http_response(GEMINI_OK)) as urlopen:
        client.analyze([_png(tmp_path)])

    body = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
    parts = body["contents"][0]["parts"]
    assert parts[1]["inlineData"]["mimeType"] == "image/png"


# --------------------------------------------------------------------------
# P1-10d: retries
# --------------------------------------------------------------------------


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://x", code, "err", {}, io.BytesIO(b'{"error": "busy"}')  # type: ignore[arg-type]
    )


def test_rate_limit_is_retried_then_succeeds(tmp_path: Path) -> None:
    client = GeminiVisionClient(api_key="k")
    client.retry_delays = (0.0, 0.0)
    responses = [_http_error(429), _http_error(503), _http_response(GEMINI_OK)]
    with patch("urllib.request.urlopen", side_effect=responses) as urlopen:
        findings = client.analyze([_png(tmp_path)])
    assert urlopen.call_count == 3
    assert findings[0].id == "VIS-GEM-001"


def test_client_error_is_not_retried(tmp_path: Path) -> None:
    client = GeminiVisionClient(api_key="k")
    client.retry_delays = (0.0, 0.0)
    with patch("urllib.request.urlopen", side_effect=[_http_error(400)]) as urlopen:
        with pytest.raises(VisionReviewError, match=r"\(400\)"):
            client.analyze([_png(tmp_path)])
    assert urlopen.call_count == 1


def test_retries_give_up_after_max_attempts(tmp_path: Path) -> None:
    client = GeminiVisionClient(api_key="k")
    client.retry_delays = (0.0, 0.0)
    errors = [_http_error(503)] * 3
    with patch("urllib.request.urlopen", side_effect=errors) as urlopen:
        with pytest.raises(VisionReviewError, match=r"\(503\)"):
            client.analyze([_png(tmp_path)])
    assert urlopen.call_count == client.max_attempts


# --------------------------------------------------------------------------
# Caching
# --------------------------------------------------------------------------


def test_vision_client_caching(tmp_path: Path) -> None:
    custom_finding = Finding(
        id="VIS-CUST-001",
        title="Custom Mock Finding",
        severity=Severity.WARNING,
        category=FindingCategory.VISION,
        description="Testing cache",
        rule_id="VISION-AI-001",
    )
    cache_dir = tmp_path / "cache"
    img = _svg(tmp_path)

    client = MockVisionClient(mock_findings=[custom_finding], cache_dir=cache_dir)
    assert [f.id for f in client.analyze([img])] == ["VIS-CUST-001"]
    assert len(list(cache_dir.glob("*.json"))) == 1

    client.mock_findings = []
    assert [f.id for f in client.analyze([img])] == ["VIS-CUST-001"]


def test_cache_key_covers_the_system_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Editing the system prompt must not keep serving answers made under the old one."""
    client = MockVisionClient()
    img = _svg(tmp_path)
    before = client._compute_cache_key("prompt", [img], "top")

    monkeypatch.setattr(client_mod, "SYSTEM_PROMPT_VISION", "a different system prompt")
    after = client._compute_cache_key("prompt", [img], "top")

    assert before != after


def test_cache_key_separates_views(tmp_path: Path) -> None:
    client = MockVisionClient()
    img = _svg(tmp_path)
    assert client._compute_cache_key("p", [img], "top") != client._compute_cache_key(
        "p", [img], "bottom"
    )


# --------------------------------------------------------------------------
# Claude
# --------------------------------------------------------------------------


def _claude_response(text: str, stop_reason: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(
        stop_reason=stop_reason,
        stop_details=SimpleNamespace(category="cyber") if stop_reason == "refusal" else None,
        content=[SimpleNamespace(type="text", text=text)] if text else [],
    )


@pytest.fixture
def fake_anthropic(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Stand in for the anthropic SDK so no network or key is needed."""
    import anthropic

    sdk_client = MagicMock()
    monkeypatch.setattr(anthropic, "Anthropic", MagicMock(return_value=sdk_client))
    return sdk_client


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("fable-5", "claude-fable-5"),
        ("fable-5.1", "claude-fable-5-1"),
        ("fable", "claude-fable-5-1"),
        ("claude-opus-5", "claude-opus-5"),
    ],
)
def test_claude_model_names_resolve_to_anthropic_ids(configured: str, expected: str) -> None:
    assert ClaudeVisionClient(model_name=configured).anthropic_model == expected


def test_claude_request_shape(tmp_path: Path, fake_anthropic: MagicMock) -> None:
    payload = json.dumps({"findings": [{"id": "VIS-CL-001", "title": "t", "description": "d"}]})
    fake_anthropic.beta.messages.create.return_value = _claude_response(payload)

    findings = ClaudeVisionClient(model_name="fable-5").analyze([_png(tmp_path)], view="top")

    assert [f.id for f in findings] == ["VIS-CL-001"]
    kwargs = fake_anthropic.beta.messages.create.call_args.kwargs
    assert kwargs["model"] == "claude-fable-5"
    assert kwargs["system"] == client_mod.SYSTEM_PROMPT_VISION
    # Fable and Opus 5 reject sampling parameters outright.
    assert "temperature" not in kwargs
    assert "thinking" not in kwargs
    # Declined reviews re-run server-side rather than failing the layer.
    assert kwargs["fallbacks"] == "default"
    assert kwargs["betas"] == ["server-side-fallback-2026-07-01"]

    content = kwargs["messages"][0]["content"]
    assert content[0]["type"] == "image"
    assert content[0]["source"]["media_type"] == "image/png"
    assert content[-1]["type"] == "text"
    assert "TOP" in content[-1]["text"]


def test_claude_fallbacks_are_only_sent_where_supported(
    tmp_path: Path, fake_anthropic: MagicMock
) -> None:
    fake_anthropic.beta.messages.create.return_value = _claude_response('{"findings": []}')
    ClaudeVisionClient(model_name="claude-sonnet-5").analyze([_png(tmp_path)])

    kwargs = fake_anthropic.beta.messages.create.call_args.kwargs
    assert "fallbacks" not in kwargs
    assert "betas" not in kwargs


def test_claude_refusal_is_reported_not_parsed(tmp_path: Path, fake_anthropic: MagicMock) -> None:
    """A refusal is an HTTP 200; reading its content as findings would be wrong."""
    fake_anthropic.beta.messages.create.return_value = _claude_response("", stop_reason="refusal")

    with pytest.raises(VisionReviewError, match="declined.*cyber"):
        ClaudeVisionClient(model_name="fable-5").analyze([_png(tmp_path)])


def test_claude_api_errors_become_vision_errors(tmp_path: Path, fake_anthropic: MagicMock) -> None:
    import anthropic

    fake_anthropic.beta.messages.create.side_effect = anthropic.APIConnectionError(
        request=MagicMock()
    )
    with pytest.raises(VisionReviewError, match="Claude API request failed"):
        ClaudeVisionClient(model_name="fable-5").analyze([_png(tmp_path)])


def test_claude_without_the_sdk_explains_how_to_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import builtins

    real_import = builtins.__import__

    def no_anthropic(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "anthropic":
            raise ImportError("No module named 'anthropic'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_anthropic)
    with pytest.raises(VisionReviewError, match=r"pcb-inspector\[claude\]"):
        ClaudeVisionClient(model_name="fable-5").analyze([_png(tmp_path)])
