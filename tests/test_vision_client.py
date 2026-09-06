"""Unit tests for Multimodal Vision clients."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pcb_inspector.core.models import Finding, FindingCategory, Severity
from pcb_inspector.vision.client import (
    FableVisionClient,
    GeminiVisionClient,
    MockVisionClient,
    OpenAIVisionClient,
    create_vision_client,
)


def test_create_vision_client_factory() -> None:
    c1 = create_vision_client("gemini-3.8-flash")
    assert isinstance(c1, GeminiVisionClient)

    c2 = create_vision_client("gpt-6-astra")
    assert isinstance(c2, OpenAIVisionClient)

    c3 = create_vision_client("fable-5")
    assert isinstance(c3, FableVisionClient)

    c4 = create_vision_client("mock")
    assert isinstance(c4, MockVisionClient)


def test_mock_vision_client(tmp_path: Path) -> None:
    img = tmp_path / "board.svg"
    img.write_text("<svg></svg>", encoding="utf-8")

    client = MockVisionClient()
    findings = client.analyze([img])
    assert len(findings) >= 1
    assert findings[0].id.startswith("VIS-")


def test_vision_client_caching(tmp_path: Path) -> None:
    img = tmp_path / "board.svg"
    img.write_text("<svg></svg>", encoding="utf-8")
    cache_dir = tmp_path / "cache"

    custom_finding = Finding(
        id="VIS-CUST-001",
        title="Custom Mock Finding",
        severity=Severity.WARNING,
        category=FindingCategory.VISION,
        description="Testing cache",
        rule_id="VISION-AI-001",
    )

    client = MockVisionClient(mock_findings=[custom_finding], cache_dir=cache_dir)
    findings1 = client.analyze([img])
    assert len(findings1) == 1
    assert findings1[0].id == "VIS-CUST-001"

    # Verify cache file was written
    cache_files = list(cache_dir.glob("*.json"))
    assert len(cache_files) == 1

    # Call again with different mock_findings on client - should return cached findings!
    client.mock_findings = []
    findings2 = client.analyze([img])
    assert len(findings2) == 1
    assert findings2[0].id == "VIS-CUST-001"


def test_gemini_client_missing_key(tmp_path: Path) -> None:
    img = tmp_path / "board.svg"
    img.write_text("<svg></svg>", encoding="utf-8")

    client = GeminiVisionClient(api_key="")
    with pytest.raises(ValueError, match="GEMINI_API_KEY is not set"):
        client.analyze([img])


def test_openai_client_missing_key(tmp_path: Path) -> None:
    img = tmp_path / "board.svg"
    img.write_text("<svg></svg>", encoding="utf-8")

    client = OpenAIVisionClient(api_key="")
    with pytest.raises(ValueError, match="OPENAI_API_KEY is not set"):
        client.analyze([img])


def test_fable_client_missing_key(tmp_path: Path) -> None:
    img = tmp_path / "board.svg"
    img.write_text("<svg></svg>", encoding="utf-8")

    client = FableVisionClient(api_key="")
    with pytest.raises(ValueError, match="FABLE_API_KEY is not set"):
        client.analyze([img])


def test_gemini_client_mocked_request(tmp_path: Path) -> None:
    img = tmp_path / "board.svg"
    img.write_text("<svg></svg>", encoding="utf-8")

    client = GeminiVisionClient(api_key="fake-key")

    mock_resp_json = {
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
                                            "description": "Found via Gemini Flash 3.8",
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

    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps(mock_resp_json).encode("utf-8")
    mock_response.__enter__.return_value = mock_response

    with patch("urllib.request.urlopen", return_value=mock_response):
        findings = client.analyze([img])
        assert len(findings) == 1
        assert findings[0].id == "VIS-GEM-001"
        assert findings[0].description == "Found via Gemini Flash 3.8"
