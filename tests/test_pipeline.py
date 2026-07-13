"""Tests for app.pipeline.run_pipeline / stream_pipeline_events — the Stage 1-4
orchestrator and its streaming counterpart.

Two tiers, matching the pattern used throughout this test suite:
- offline/mocked (extract() and ground_chemical() monkeypatched): no network,
  no API key, no `costly` marker — proves the wiring, not the live calls.
- live, real end-to-end against the locked demo protocol: marked `costly`
  (spends Anthropic budget) — excluded from the default `pytest` run; opt in
  explicitly with `pytest -m costly`.
"""

import json
from pathlib import Path

import pytest

from app import pipeline
from app.config import get_settings
from app.extraction import ExtractionError
from app.models import ChemicalHazardProfile, ExtractionResult
from app.omissions import OmissionDetectionResult
from app.segmentation import SegmentationResult
from test_brief import _full_profile
from test_segmentation import _NON_PROTOCOL_PROSE, _RUN_A_PROSE

# Every offline test below must mock this too, not just extract/ground_chemical —
# omission-detection is a real, separate Anthropic call (app.omissions.detect_omissions),
# and leaving it unmocked in a test outside the `costly` marker means the default
# `pytest` run silently spends real API budget. Empty flags: these tests exercise the
# wiring, not omission-detection's own reasoning (that's tests/test_omissions.py, costly).
def _fake_detect_omissions(result, profiles):
    return OmissionDetectionResult(flags=[])


# Prose segmentation (app.segmentation.segment_protocol) is a third real Anthropic
# call, only reached for single-paragraph prose. These offline tests use structured
# stub text so it is never reached, but mock it anyway to keep the "no paid call in
# the default suite" invariant explicit rather than relying on the guard's threshold.
def _fake_segment(protocol_text):
    return SegmentationResult(steps=[])

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# The six canonical names that appear in tests/fixtures/extraction_response.json.
_GROUP_BY_NAME = {
    "hydrogen peroxide": "Oxidizing Agents, Strong",
    "sulfuric acid": "Acids, Strong Oxidizing",
    "sodium azide": "Azo, Diazo, Azido, Hydrazine, and Azide Compounds",
}
_CID_BY_NAME = {
    "hydrogen peroxide": 784,
    "sulfuric acid": 1118,
    "water": 962,
    "nitrogen": 947,
    "phosphate-buffered saline": 24978514,  # placeholder — offline test only, never queried live
    "sodium azide": 33557,
}


def _fake_ground_chemical(name: str) -> ChemicalHazardProfile:
    return _full_profile(name, _CID_BY_NAME.get(name, 1), _GROUP_BY_NAME.get(name))


def test_run_pipeline_wires_all_stages_offline(monkeypatch):
    fixture = json.loads((FIXTURES / "extraction_response.json").read_text(encoding="utf-8"))
    expected = ExtractionResult.model_validate(fixture)

    monkeypatch.setattr(pipeline, "extract", lambda protocol_text: expected)
    monkeypatch.setattr(pipeline, "ground_chemical", _fake_ground_chemical)
    monkeypatch.setattr(pipeline, "detect_omissions", _fake_detect_omissions)
    monkeypatch.setattr(pipeline, "segment_protocol", _fake_segment)

    result = pipeline.run_pipeline("irrelevant — extract() is mocked")

    assert result.extraction is expected
    assert len(result.profiles) == len({c.canonical_name for c in expected.chemicals})
    assert result.brief.statements
    for statement in result.brief.statements:
        assert statement.source_ref

    # The two known hazard pathways should both come through the full wiring, each as
    # exactly one deduped statement (not one per step it persists through).
    hazards = [s for s in result.brief.statements if s.kind == "interaction_hazard"]
    assert len(hazards) == 2, "piranha + azide/acid should be 2 statements, not one per step occurrence"
    hazard_steps = {n for s in hazards for n in s.step_numbers}
    assert 1 in hazard_steps  # piranha mixing, first appears step 1
    assert 5 in hazard_steps  # waste-stream azide/acid, step 5


@pytest.mark.costly
@pytest.mark.skipif(not get_settings().anthropic_api_key, reason="ANTHROPIC_API_KEY not set")
def test_run_pipeline_live_demo_protocol():
    protocol_text = (FIXTURES / "demo_protocol.txt").read_text(encoding="utf-8")

    result = pipeline.run_pipeline(protocol_text)

    assert result.brief.statements
    for statement in result.brief.statements:
        assert statement.source_ref

    hazard_steps = {n for s in result.brief.statements if s.kind == "interaction_hazard" for n in s.step_numbers}
    assert 1 in hazard_steps
    assert 5 in hazard_steps


@pytest.mark.costly
@pytest.mark.skipif(not get_settings().anthropic_api_key, reason="ANTHROPIC_API_KEY not set")
def test_run_pipeline_prose_paragraph_now_extracts():
    """Run A, the permanent regression for the prose bug: bleach + acid written as
    ONE unbroken prose paragraph must now extract, not land in the empty state
    (the prose-segmentation gate). Before segmentation this produced zero
    chemicals."""
    result = pipeline.run_pipeline(_RUN_A_PROSE)

    assert result.extraction.chemicals, "prose paragraph must extract chemicals, not empty out"
    names = {c.canonical_name.lower() for c in result.extraction.chemicals}
    assert any("hypochlorite" in n or "bleach" in n for n in names), names
    assert any("hydrochloric" in n or "hydrogen chloride" in n for n in names), names


@pytest.mark.costly
@pytest.mark.skipif(not get_settings().anthropic_api_key, reason="ANTHROPIC_API_KEY not set")
def test_run_pipeline_non_protocol_prose_stays_empty():
    """Non-protocol guard (gate #4): a genuine non-procedure paragraph must still
    yield zero chemicals so the UI lands it in the empty state. Segmentation must
    not manufacture steps out of arbitrary prose."""
    result = pipeline.run_pipeline(_NON_PROTOCOL_PROSE)

    assert result.extraction.chemicals == []


@pytest.mark.asyncio
async def test_stream_pipeline_events_happy_path(monkeypatch):
    fixture = json.loads((FIXTURES / "extraction_response.json").read_text(encoding="utf-8"))
    expected = ExtractionResult.model_validate(fixture)

    monkeypatch.setattr(pipeline, "extract", lambda protocol_text: expected)
    monkeypatch.setattr(pipeline, "ground_chemical", _fake_ground_chemical)
    monkeypatch.setattr(pipeline, "detect_omissions", _fake_detect_omissions)
    monkeypatch.setattr(pipeline, "segment_protocol", _fake_segment)

    messages = [msg async for msg in pipeline.stream_pipeline_events("irrelevant — extract() is mocked")]

    # Exact sequence: extraction started -> extraction done -> one `chemical` per unique
    # name -> interactions done -> brief done -> result. No line before its event.
    assert messages[0].event == "stage"
    assert messages[0].data == {"stage": "extraction", "status": "started"}

    assert messages[1].event == "stage"
    assert messages[1].data["stage"] == "extraction"
    assert messages[1].data["status"] == "done"
    unique_names = list(dict.fromkeys(c.canonical_name for c in expected.chemicals))
    assert messages[1].data["detail"] == {
        "chemicals": len(expected.chemicals),
        "steps": len(expected.steps),
        "mixtures": len(expected.recognized_mixtures),
        "unresolved": len(expected.unresolved_mentions),
    }

    chemical_msgs = messages[2 : 2 + len(unique_names)]
    assert all(m.event == "chemical" for m in chemical_msgs)
    assert [m.data["name"] for m in chemical_msgs] == unique_names
    by_name = {m.data["name"]: m.data for m in chemical_msgs}
    for m in chemical_msgs:
        assert set(m.data.keys()) == {
            "name",
            "cid",
            "found",
            "missing_sections",
            "chemical_ids",
            "concentration",
            "grounding_error",
            "not_small_molecule",
            "fallback_source",
        }
        assert m.data["chemical_ids"]  # every extracted chemical maps back to at least one id
        assert m.data["grounding_error"] is None  # demo protocol chemicals all ground cleanly
        assert m.data["not_small_molecule"] is False  # none of the demo protocol's chemicals are proteins
        assert m.data["fallback_source"] is None  # all demo protocol chemicals ground via live PubChem
    # Concentration round-trips onto the SSE event exactly as extraction captured it.
    assert by_name["hydrogen peroxide"]["concentration"] == "30%"
    assert by_name["sodium azide"]["concentration"] == "0.02%"
    assert by_name["water"]["concentration"] is None

    rest = messages[2 + len(unique_names) :]
    assert rest[0].event == "stage" and rest[0].data["stage"] == "interactions"
    # Deduped: 2 real hazards (piranha, azide/acid), not one per step occurrence.
    assert rest[0].data["detail"]["hazards_found"] == 2

    # Omission-detection earns its own started/done pair, same as extraction — a real,
    # separate stage, not something that happens for free between interactions and brief.
    assert rest[1].event == "stage" and rest[1].data == {"stage": "omissions", "status": "started"}
    assert rest[2].event == "stage" and rest[2].data["stage"] == "omissions"
    assert rest[2].data["detail"]["flags"] == 0  # _fake_detect_omissions returns no flags

    assert rest[3].event == "stage" and rest[3].data["stage"] == "brief"

    assert rest[4].event == "result"
    hazard_steps = {
        n
        for s in rest[4].data["statements"]
        if s["kind"] == "interaction_hazard"
        for n in s["step_numbers"]
    }
    assert 1 in hazard_steps
    assert 5 in hazard_steps
    assert len(rest) == 5  # nothing after result


@pytest.mark.asyncio
async def test_stream_pipeline_events_extraction_error(monkeypatch):
    def _raise(protocol_text):
        raise ExtractionError("model did not call the tool")

    monkeypatch.setattr(pipeline, "extract", _raise)

    messages = [msg async for msg in pipeline.stream_pipeline_events("irrelevant")]

    # Unrecoverable: exactly the started + error messages, nothing else — no result event.
    assert [m.event for m in messages] == ["stage", "error"]
    assert messages[1].data == {
        "stage": "extraction",
        "message": "model did not call the tool",
        "recoverable": False,
    }


@pytest.mark.asyncio
async def test_stream_pipeline_events_grounding_outage_still_completes(monkeypatch):
    """The headline behavioral guarantee: a per-chemical grounding failure is recoverable —
    the stream still reaches `result` with an incomplete-but-real brief, not a dead stream."""
    fixture = json.loads((FIXTURES / "extraction_response.json").read_text(encoding="utf-8"))
    expected = ExtractionResult.model_validate(fixture)

    def _flaky_ground_chemical(name: str) -> ChemicalHazardProfile:
        if name == "sulfuric acid":
            return ChemicalHazardProfile(query_name=name, found=False, grounding_error="PubChem unreachable")
        return _fake_ground_chemical(name)

    monkeypatch.setattr(pipeline, "extract", lambda protocol_text: expected)
    monkeypatch.setattr(pipeline, "ground_chemical", _flaky_ground_chemical)
    monkeypatch.setattr(pipeline, "detect_omissions", _fake_detect_omissions)
    monkeypatch.setattr(pipeline, "segment_protocol", _fake_segment)

    messages = [msg async for msg in pipeline.stream_pipeline_events("irrelevant")]

    chemical_msgs = [m for m in messages if m.event == "chemical"]
    assert any(m.data["name"] == "sulfuric acid" and m.data["found"] is False for m in chemical_msgs)

    grounding_errors = [
        m for m in messages if m.event == "error" and m.data.get("stage") == "grounding"
    ]
    assert len(grounding_errors) == 1
    assert grounding_errors[0].data["recoverable"] is True

    # The headline guarantee: a result STILL fires, and it's flagged incomplete.
    assert messages[-1].event == "result"
    assert messages[-1].data["incomplete"] is True
    assert messages[-1].data["incomplete_chemicals"] == ["sulfuric acid"]
