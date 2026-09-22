"""Unit tests for VisionReviewRule."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from pcb_inspector.core.config import InspectorConfig
from pcb_inspector.kicad.cli_wrapper import KiCadCli
from pcb_inspector.rules.registry import default_registry
from pcb_inspector.rules.vision_review import VisionReviewRule
from pcb_inspector.vision.client import MockVisionClient

SAMPLE_PCB = """(kicad_pcb (version 20240108) (generator pcbnew)
  (general (thickness 1.6))
  (footprint "Package_QFP:LQFP-48" (layer "F.Cu") (at 50 50 0)
    (property "Reference" "U1")
    (pad "1" smd rect (at -3.5 0) (size 0.3 1.2) (layers "F.Cu") (net 1 "VCC"))
  )
)"""


def test_vision_rule_disabled_by_default(tmp_path: Path) -> None:
    pcb_file = tmp_path / "board.kicad_pcb"
    pcb_file.write_text(SAMPLE_PCB, encoding="utf-8")

    cfg = InspectorConfig()
    assert cfg.enable_vision is False

    rule = VisionReviewRule()
    findings = rule.evaluate(pcb_file, cfg)
    assert findings == []


def test_vision_rule_enabled_mock(tmp_path: Path) -> None:
    pcb_file = tmp_path / "board.kicad_pcb"
    pcb_file.write_text(SAMPLE_PCB, encoding="utf-8")

    cfg = InspectorConfig(enable_vision=True, vision_model="mock")
    rule = VisionReviewRule()
    findings = rule.evaluate(pcb_file, cfg)

    assert len(findings) >= 1
    for f in findings:
        assert f.rule_id == "VISION-AI-001"


def test_vision_rule_missing_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pcb_file = tmp_path / "board.kicad_pcb"
    pcb_file.write_text(SAMPLE_PCB, encoding="utf-8")

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    cfg = InspectorConfig(enable_vision=True, vision_model="gemini-3.8-flash")
    rule = VisionReviewRule()
    findings = rule.evaluate(pcb_file, cfg)

    assert len(findings) == 1
    assert findings[0].id == "VIS-NO-API-KEY"


def test_vision_rule_in_registry() -> None:
    rule = default_registry.get("VISION-AI-001")
    assert rule is not None
    assert isinstance(rule, VisionReviewRule)


# --------------------------------------------------------------------------
# P1-10c: each side is reviewed in its own request
# --------------------------------------------------------------------------


class _RecordingClient(MockVisionClient):
    """Mock client that records how it was called."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[list[Path], str | None]] = []

    def analyze(self, image_paths, board_context=None, view=None):  # type: ignore[no-untyped-def]
        self.calls.append((list(image_paths), view))
        return super().analyze(image_paths, board_context=board_context, view=view)


def test_each_side_is_reviewed_separately(tmp_path: Path) -> None:
    """Both images used to go out under one prompt that said "top"."""
    pcb_file = tmp_path / "board.kicad_pcb"
    pcb_file.write_text(SAMPLE_PCB, encoding="utf-8")
    client = _RecordingClient()

    VisionReviewRule(client=client).evaluate(pcb_file, InspectorConfig(enable_vision=True))

    assert [view for _, view in client.calls] == ["top", "bottom"]
    assert all(len(images) == 1 for images, _ in client.calls)
    assert "top" in client.calls[0][0][0].name
    assert "bottom" in client.calls[1][0][0].name


def test_findings_from_each_side_get_distinct_ids(tmp_path: Path) -> None:
    pcb_file = tmp_path / "board.kicad_pcb"
    pcb_file.write_text(SAMPLE_PCB, encoding="utf-8")

    findings = VisionReviewRule(client=MockVisionClient()).evaluate(
        pcb_file, InspectorConfig(enable_vision=True)
    )

    ids = [f.id for f in findings]
    assert len(ids) == len(set(ids))
    assert {f.raw_data["view"] for f in findings} == {"top", "bottom"}


def test_one_side_failing_keeps_the_other_sides_findings(tmp_path: Path) -> None:
    pcb_file = tmp_path / "board.kicad_pcb"
    pcb_file.write_text(SAMPLE_PCB, encoding="utf-8")

    class _BottomFails(MockVisionClient):
        def analyze(self, image_paths, board_context=None, view=None):  # type: ignore[no-untyped-def]
            if view == "bottom":
                raise RuntimeError("provider timed out")
            return super().analyze(image_paths, board_context=board_context, view=view)

    findings = VisionReviewRule(client=_BottomFails()).evaluate(
        pcb_file, InspectorConfig(enable_vision=True)
    )

    ids = [f.id for f in findings]
    assert "VIS-API-CALL-ERR-BOTTOM" in ids
    assert any(i.endswith("-TOP") and not i.startswith("VIS-API") for i in ids)


# --------------------------------------------------------------------------
# API key variable follows the chosen provider
# --------------------------------------------------------------------------


def test_openai_model_uses_openai_key_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An OpenAI model used to be refused unless GEMINI_API_KEY was set."""
    pcb_file = tmp_path / "board.kicad_pcb"
    pcb_file.write_text(SAMPLE_PCB, encoding="utf-8")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    findings = VisionReviewRule().evaluate(
        pcb_file,
        InspectorConfig(enable_vision=True, vision_model="gpt-6-astra", kicad_cli_path="missing"),
    )

    assert "VIS-NO-API-KEY" not in [f.id for f in findings]


def test_missing_openai_key_names_the_right_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pcb_file = tmp_path / "board.kicad_pcb"
    pcb_file.write_text(SAMPLE_PCB, encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    findings = VisionReviewRule().evaluate(
        pcb_file, InspectorConfig(enable_vision=True, vision_model="gpt-6-astra")
    )

    assert findings[0].id == "VIS-NO-API-KEY"
    assert "OPENAI_API_KEY" in findings[0].title


def test_explicit_key_variable_still_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pcb_file = tmp_path / "board.kicad_pcb"
    pcb_file.write_text(SAMPLE_PCB, encoding="utf-8")
    monkeypatch.delenv("MY_KEY", raising=False)

    findings = VisionReviewRule().evaluate(
        pcb_file,
        InspectorConfig(enable_vision=True, vision_model="gpt-6-astra", vision_api_key_env="MY_KEY"),
    )

    assert "MY_KEY" in findings[0].title


def test_claude_is_not_blocked_on_an_unset_key_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Anthropic SDK also resolves auth tokens and login profiles."""
    pcb_file = tmp_path / "board.kicad_pcb"
    pcb_file.write_text(SAMPLE_PCB, encoding="utf-8")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    findings = VisionReviewRule().evaluate(
        pcb_file,
        InspectorConfig(enable_vision=True, vision_model="fable-5", kicad_cli_path="missing"),
    )

    assert "VIS-NO-API-KEY" not in [f.id for f in findings]


def test_hosted_model_with_only_an_svg_render_fails_clearly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without kicad-cli there is no PNG, and the SVG must not be sent."""
    pcb_file = tmp_path / "board.kicad_pcb"
    pcb_file.write_text(SAMPLE_PCB, encoding="utf-8")
    monkeypatch.setenv("GEMINI_API_KEY", "k")

    with patch("urllib.request.urlopen") as urlopen, patch.object(
        KiCadCli, "find_executable", return_value=None
    ):
        findings = VisionReviewRule().evaluate(
            pcb_file, InspectorConfig(enable_vision=True, vision_model="gemini-3.8-flash")
        )

    urlopen.assert_not_called()
    assert {f.id for f in findings} == {"VIS-API-CALL-ERR-TOP", "VIS-API-CALL-ERR-BOTTOM"}
    assert "image/svg+xml" in findings[0].description
