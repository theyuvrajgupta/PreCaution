"""Tests for PubChem grounding.

Two tiers, same pattern as test_extraction.py:
- Offline parser tests: monkeypatch the network fetch and feed in JSON
  responses actually captured from PubChem on 2026-07-09 (tests/fixtures/pubchem/).
  Fast, deterministic, guards the parsing logic independent of the network.
- Live integration test: real calls against the public PubChem API (no key
  required). Skipped automatically if the network is unreachable.
"""

import json
from pathlib import Path

import httpx
import pytest

from app import pubchem

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pubchem"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_parse_ghs_classification_offline(monkeypatch):
    fixture = _load("ghs_784_hydrogen_peroxide.json")
    monkeypatch.setattr(pubchem, "_fetch_heading", lambda cid, heading: fixture)

    ghs = pubchem.get_ghs_classification(784)

    assert ghs is not None
    assert ghs.signal_word == "Danger"
    assert "Corrosive" in ghs.pictograms
    assert any(h.startswith("H314") for h in ghs.hazard_statements)
    # Cites the specific primary source behind the first/primary classification
    # group (PubChem shows one classification by default; we match that), not
    # a generic PubChem placeholder.
    assert ghs.source.source_name
    assert ghs.source.url


def test_parse_reactive_group_offline_hydrogen_peroxide(monkeypatch):
    fixture = _load("reactive_group_784_hydrogen_peroxide.json")
    monkeypatch.setattr(pubchem, "_fetch_heading", lambda cid, heading: fixture)

    groups = pubchem.get_reactive_groups(784)

    names = {g.group_name for g in groups}
    assert "Oxidizing Agents, Strong" in names
    assert all(g.source.source_name == "CAMEO Chemicals" for g in groups)
    assert all(g.source.url and "cameochemicals.noaa.gov" in g.source.url for g in groups)


def test_parse_reactive_group_offline_sulfuric_acid(monkeypatch):
    fixture = _load("reactive_group_1118_sulfuric_acid.json")
    monkeypatch.setattr(pubchem, "_fetch_heading", lambda cid, heading: fixture)

    groups = pubchem.get_reactive_groups(1118)

    assert {g.group_name for g in groups} == {"Acids, Strong Oxidizing"}


def test_parse_reactive_group_offline_sodium_azide(monkeypatch):
    fixture = _load("reactive_group_33557_sodium_azide.json")
    monkeypatch.setattr(pubchem, "_fetch_heading", lambda cid, heading: fixture)

    groups = pubchem.get_reactive_groups(33557)

    assert {g.group_name for g in groups} == {"Azo, Diazo, Azido, Hydrazine, and Azide Compounds"}


def test_missing_heading_returns_none(monkeypatch):
    monkeypatch.setattr(pubchem, "_fetch_heading", lambda cid, heading: None)

    assert pubchem.get_ghs_classification(1) is None
    assert pubchem.get_reactive_groups(1) == []
    assert pubchem.get_safety_note(1, "First Aid Measures") is None


def test_get_json_retries_on_transient_error_then_succeeds(monkeypatch, tmp_path):
    monkeypatch.setattr(pubchem._cache, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(pubchem, "_throttle", lambda: None)  # skip real sleeps in the test
    monkeypatch.setattr(pubchem.time, "sleep", lambda _seconds: None)

    calls = {"n": 0}

    class _FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": True}

    def _flaky_get(url, params=None, timeout=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("simulated transient network failure", request=None)
        return _FakeResponse()

    monkeypatch.setattr(httpx, "get", _flaky_get)

    result = pubchem._get_json("https://example.invalid/x")

    assert result == {"ok": True}
    assert calls["n"] == 3  # failed twice, succeeded on the third attempt


def test_get_json_raises_after_exhausting_retries(monkeypatch, tmp_path):
    monkeypatch.setattr(pubchem._cache, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(pubchem, "_throttle", lambda: None)
    monkeypatch.setattr(pubchem.time, "sleep", lambda _seconds: None)

    def _always_fails(url, params=None, timeout=None):
        raise httpx.ConnectError("simulated persistent network failure", request=None)

    monkeypatch.setattr(httpx, "get", _always_fails)

    with pytest.raises(RuntimeError, match="failed after"):
        pubchem._get_json("https://example.invalid/x")


def _network_available() -> bool:
    try:
        httpx.get("https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/water/cids/JSON", timeout=5)
        return True
    except httpx.HTTPError:
        return False


@pytest.mark.skipif(not _network_available(), reason="No network access to PubChem")
def test_live_ground_chemical_demo_protocol_chemicals():
    h2o2 = pubchem.ground_chemical("hydrogen peroxide")
    assert h2o2.found is True
    assert h2o2.cid == 784
    assert h2o2.ghs is not None
    assert any(g.group_name == "Oxidizing Agents, Strong" for g in h2o2.reactive_groups)

    h2so4 = pubchem.ground_chemical("sulfuric acid")
    assert h2so4.cid == 1118
    assert any(g.group_name == "Acids, Strong Oxidizing" for g in h2so4.reactive_groups)

    azide = pubchem.ground_chemical("sodium azide")
    assert azide.cid == 33557
    assert any("Azide" in g.group_name for g in azide.reactive_groups)


@pytest.mark.skipif(not _network_available(), reason="No network access to PubChem")
def test_live_ground_chemical_unknown_name_is_honest():
    profile = pubchem.ground_chemical("definitely-not-a-real-chemical-xyzzy-12345")
    assert profile.found is False
    assert profile.missing_sections == ["CID resolution"]
