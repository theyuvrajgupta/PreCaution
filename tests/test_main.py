"""Tests for app.main's FastAPI endpoints: /extract (pre-existing, untested until
now), /brief, and /brief/stream.

Uses FastAPI's TestClient (httpx-backed) against the real app, with the
underlying Stage 1/2 functions (app.pipeline.extract / app.pipeline.ground_chemical)
monkeypatched — same pattern as tests/test_pipeline.py. This exercises the real
endpoint code (request handling, error mapping, SSE wire-formatting) end-to-end,
not just the pipeline functions in isolation.

Offline/mocked by default (no `costly` marker); one live end-to-end test at the
bottom is marked `@pytest.mark.costly` and skipped unless ANTHROPIC_API_KEY is set.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import pipeline
from app.config import get_settings
from app.extraction import ExtractionError
from app.interaction_matrix import lookup_verdict
from app.main import app
from app.models import ChemicalHazardProfile, ExtractionResult
from app.omissions import OmissionDetectionResult
from app.segmentation import SegmentationResult
from test_brief import _full_profile

FIXTURES = Path(__file__).resolve().parent / "fixtures"

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
    "phosphate-buffered saline": 24978514,
    "sodium azide": 33557,
}


def _fake_ground_chemical(name: str) -> ChemicalHazardProfile:
    return _full_profile(name, _CID_BY_NAME.get(name, 1), _GROUP_BY_NAME.get(name))


# Same reasoning as tests/test_pipeline.py's copy of this: omission-detection is a real,
# separate Anthropic call — every offline test below must mock it too, or the default
# `pytest` run silently spends real API budget.
def _fake_detect_omissions(result, profiles):
    return OmissionDetectionResult(flags=[])


# See test_pipeline.py's copy: segmentation is a real Anthropic call reached only for
# single-paragraph prose. These tests use stub text that never reaches it, but mock it
# anyway to keep the default suite provably free of paid calls.
def _fake_segment(protocol_text):
    return SegmentationResult(steps=[])


def _demo_fixture_result() -> ExtractionResult:
    fixture = json.loads((FIXTURES / "extraction_response.json").read_text(encoding="utf-8"))
    return ExtractionResult.model_validate(fixture)


def _parse_sse_frames(text: str) -> list[tuple[str, dict]]:
    """Split a raw SSE response body into (event, data) pairs, validating that
    every frame is well-formed (event line, then a data line whose payload is
    valid JSON) along the way."""
    frames = []
    for block in text.strip("\n").split("\n\n"):
        if not block.strip():
            continue
        lines = block.split("\n")
        assert len(lines) == 2, f"malformed SSE frame (expected exactly 2 lines): {block!r}"
        assert lines[0].startswith("event: "), f"malformed SSE frame (no event line): {block!r}"
        assert lines[1].startswith("data: "), f"malformed SSE frame (no data line): {block!r}"
        event = lines[0][len("event: ") :]
        data = json.loads(lines[1][len("data: ") :])  # must be valid JSON — proves no frame corruption
        frames.append((event, data))
    return frames


def test_interaction_matrix_endpoint_matches_the_real_table():
    """The in-app interaction-table panel must render the SAME data
    app.interactions.find_step_interactions actually looks verdicts up in — this
    spot-checks one known entry (piranha solution's pair) against the module the
    engine calls directly, not against a copy."""
    client = TestClient(app)
    response = client.get("/interaction-matrix")

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert body  # non-empty — the seed set is small but never zero

    expected = lookup_verdict("Oxidizing Agents, Strong", "Acids, Strong Oxidizing")
    assert expected is not None
    match = next(
        (
            v
            for v in body
            if {v["group_a"], v["group_b"]} == {"Oxidizing Agents, Strong", "Acids, Strong Oxidizing"}
        ),
        None,
    )
    assert match is not None
    assert match["categories"] == expected.categories
    assert match["source"]["url"] == expected.source.url


def test_brief_endpoint_success(monkeypatch):
    expected = _demo_fixture_result()
    monkeypatch.setattr(pipeline, "extract", lambda protocol_text: expected)
    monkeypatch.setattr(pipeline, "ground_chemical", _fake_ground_chemical)
    monkeypatch.setattr(pipeline, "detect_omissions", _fake_detect_omissions)
    monkeypatch.setattr(pipeline, "segment_protocol", _fake_segment)

    client = TestClient(app)
    response = client.post("/brief", json={"protocol_text": "irrelevant — extract() is mocked"})

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"extraction", "profiles", "findings", "omissions", "brief"}
    assert body["omissions"] == {"flags": []}
    assert body["brief"]["statements"]
    assert all(s["source_ref"] for s in body["brief"]["statements"])


def test_brief_endpoint_extraction_error(monkeypatch):
    def _raise(protocol_text):
        raise ExtractionError("model did not call the tool")

    monkeypatch.setattr(pipeline, "extract", _raise)

    client = TestClient(app)
    response = client.post("/brief", json={"protocol_text": "irrelevant"})

    assert response.status_code == 502
    assert "model did not call the tool" in response.json()["detail"]


def test_brief_stream_emits_wellformed_sse(monkeypatch):
    expected = _demo_fixture_result()
    monkeypatch.setattr(pipeline, "extract", lambda protocol_text: expected)
    monkeypatch.setattr(pipeline, "ground_chemical", _fake_ground_chemical)
    monkeypatch.setattr(pipeline, "detect_omissions", _fake_detect_omissions)
    monkeypatch.setattr(pipeline, "segment_protocol", _fake_segment)

    client = TestClient(app)
    response = client.post("/brief/stream", json={"protocol_text": "irrelevant"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    frames = _parse_sse_frames(response.text)
    assert frames  # non-empty
    assert frames[0] == ("stage", {"stage": "extraction", "status": "started"})
    assert frames[-1][0] == "result"
    assert frames[-1][1]["statements"]

    hazard_steps = {
        n
        for s in frames[-1][1]["statements"]
        if s["kind"] == "interaction_hazard"
        for n in s["step_numbers"]
    }
    assert 1 in hazard_steps
    assert 5 in hazard_steps


def test_brief_stream_error_frame_is_wellformed(monkeypatch):
    """A per-chemical grounding outage must not corrupt the SSE stream — the error
    frame parses as valid JSON like any other, and the stream still reaches `result`."""
    expected = _demo_fixture_result()

    def _flaky_ground_chemical(name: str) -> ChemicalHazardProfile:
        if name == "sulfuric acid":
            return ChemicalHazardProfile(query_name=name, found=False, grounding_error="PubChem unreachable")
        return _fake_ground_chemical(name)

    monkeypatch.setattr(pipeline, "extract", lambda protocol_text: expected)
    monkeypatch.setattr(pipeline, "ground_chemical", _flaky_ground_chemical)
    monkeypatch.setattr(pipeline, "detect_omissions", _fake_detect_omissions)
    monkeypatch.setattr(pipeline, "segment_protocol", _fake_segment)

    client = TestClient(app)
    response = client.post("/brief/stream", json={"protocol_text": "irrelevant"})

    assert response.status_code == 200
    frames = _parse_sse_frames(response.text)  # raises if any frame is malformed

    error_frames = [f for f in frames if f[0] == "error" and f[1].get("stage") == "grounding"]
    assert len(error_frames) == 1
    assert error_frames[0][1]["recoverable"] is True

    assert frames[-1][0] == "result"
    assert frames[-1][1]["incomplete"] is True
    assert frames[-1][1]["incomplete_chemicals"] == ["sulfuric acid"]


@pytest.mark.costly
@pytest.mark.skipif(not get_settings().anthropic_api_key, reason="ANTHROPIC_API_KEY not set")
def test_brief_stream_live_demo_protocol():
    protocol_text = (FIXTURES / "demo_protocol.txt").read_text(encoding="utf-8")

    client = TestClient(app)
    response = client.post("/brief/stream", json={"protocol_text": protocol_text})

    assert response.status_code == 200
    frames = _parse_sse_frames(response.text)
    assert frames[-1][0] == "result"

    hazard_steps = {
        n
        for s in frames[-1][1]["statements"]
        if s["kind"] == "interaction_hazard"
        for n in s["step_numbers"]
    }
    assert 1 in hazard_steps
    assert 5 in hazard_steps
