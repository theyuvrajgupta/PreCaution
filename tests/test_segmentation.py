"""Tests for app.segmentation — the pre-extraction prose-segmentation pass.

Two tiers, matching the suite:
- offline: needs_segmentation's guard logic, the serialization round-trip, and
  segment_protocol's parse layer against a faked Anthropic response — no network,
  no key, no `costly` marker.
- live: real Anthropic calls proving prose actually segments and a non-procedure
  segments to nothing. Marked `costly`, skipped without ANTHROPIC_API_KEY.
"""

import pytest

from app import segmentation
from app.config import get_settings
from app.segmentation import (
    SegmentationError,
    SegmentationResult,
    needs_segmentation,
    segment_protocol,
)

# The Run A paragraph from the build spec: three sentences, one unbroken block.
_RUN_A_PROSE = (
    "Add 50 mL of household bleach to a beaker in the fume hood. Slowly pour in "
    "100 mL of concentrated hydrochloric acid while stirring. Let the mixture "
    "stand for ten minutes before neutralizing."
)

# A genuine non-procedure: the quarterly-review paragraph must NOT become steps.
_NON_PROTOCOL_PROSE = (
    "The quarterly safety review met on Tuesday to discuss training compliance "
    "and incident reporting. Attendance was strong and the committee agreed to "
    "revisit the badge-access policy next quarter."
)


# --- needs_segmentation: fires only for single-paragraph, multi-sentence prose ---


def test_needs_segmentation_true_for_multi_sentence_paragraph():
    assert needs_segmentation(_RUN_A_PROSE) is True
    assert needs_segmentation(_NON_PROTOCOL_PROSE) is True  # shape-based; the gate is downstream


def test_needs_segmentation_false_for_structured_input():
    numbered = "1. Add acid.\n2. Rinse.\n3. Dispose in the carboy."
    assert needs_segmentation(numbered) is False


def test_needs_segmentation_false_for_one_liner():
    assert needs_segmentation("Rinse the coverslips in acetone.") is False


def test_needs_segmentation_false_for_single_marked_step_and_empty():
    assert needs_segmentation("1. Rinse in acetone then dry under nitrogen.") is False
    assert needs_segmentation("") is False
    assert needs_segmentation("   \n  \n") is False


# --- serialization round-trip ---


def test_as_protocol_text_numbers_steps():
    result = SegmentationResult(steps=["Add acid to water", "Stir", "Dispose in carboy"])
    assert result.as_protocol_text() == "1. Add acid to water\n2. Stir\n3. Dispose in carboy"

    # The serialized shape is itself already-structured, so it will not re-trigger
    # segmentation if it ever flows back through the guard.
    assert needs_segmentation(result.as_protocol_text()) is False


# --- segment_protocol parse layer (faked client, no network) ---


class _FakeBlock:
    type = "tool_use"
    name = "emit_segmentation"

    def __init__(self, payload):
        self.input = payload


class _FakeResponse:
    stop_reason = "tool_use"

    def __init__(self, content):
        self.content = content


class _FakeClient:
    def __init__(self, content):
        self._content = content

    class _Messages:
        def __init__(self, content):
            self._content = content

        def create(self, **_kwargs):
            return _FakeResponse(self._content)

    @property
    def messages(self):
        return self._Messages(self._content)


def test_segment_protocol_parses_tool_call(monkeypatch):
    payload = {"steps": ["Add bleach to a beaker", "Pour in acid", "Let it stand"]}
    monkeypatch.setattr(segmentation, "get_client", lambda: _FakeClient([_FakeBlock(payload)]))

    result = segment_protocol(_RUN_A_PROSE)

    assert result.steps == payload["steps"]


def test_segment_protocol_raises_when_tool_not_called(monkeypatch):
    monkeypatch.setattr(segmentation, "get_client", lambda: _FakeClient([]))  # no tool_use block

    with pytest.raises(SegmentationError):
        segment_protocol(_RUN_A_PROSE)


# --- live tier ---


@pytest.mark.costly
@pytest.mark.skipif(not get_settings().anthropic_api_key, reason="ANTHROPIC_API_KEY not set")
def test_live_segments_prose_into_steps():
    result = segment_protocol(_RUN_A_PROSE)
    # The three sentences describe distinct operations — expect real step structure.
    assert len(result.steps) >= 2


@pytest.mark.costly
@pytest.mark.skipif(not get_settings().anthropic_api_key, reason="ANTHROPIC_API_KEY not set")
def test_live_non_procedure_segments_to_nothing():
    """The non-protocol guard at the segmentation layer: a genuine non-procedure
    returns no steps, so the pipeline falls back to raw text and extraction still
    lands it in the empty state rather than manufacturing steps."""
    result = segment_protocol(_NON_PROTOCOL_PROSE)
    assert result.steps == []
