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


def test_fix_mojibake_reverses_double_encoded_bullet():
    # Confirmed live 2026-07-10: PubChem's own response bytes for a bullet character
    # are C3 A2 C2 80 C2 A2 — correctly UTF-8-decoding that (which httpx already does)
    # yields exactly this 3-codepoint string, not the intended "•" (U+2022).
    corrupted = "â¢ EYEWASH"
    assert pubchem._fix_mojibake(corrupted) == "• EYEWASH"


def test_fix_mojibake_leaves_clean_text_untouched():
    clean = "Wear appropriate personal protective clothing to prevent skin contact."
    assert pubchem._fix_mojibake(clean) == clean


def test_safety_note_dedupes_exact_repeated_excerpts(monkeypatch):
    # Reproduces the real bug: PubChem cites the same excerpt under two different
    # ReferenceNumbers (confirmed live for hydrogen peroxide's PPE heading).
    fixture = {
        "Record": {
            "Reference": [
                {"ReferenceNumber": 6, "SourceName": "NIOSH Pocket Guide", "URL": "https://example.invalid/niosh"},
                {"ReferenceNumber": 7, "SourceName": "NIOSH Pocket Guide", "URL": "https://example.invalid/niosh"},
            ],
            "Section": [
                {
                    "TOCHeading": "Personal Protective Equipment (PPE)",
                    "Information": [
                        {"ReferenceNumber": 6, "Value": {"StringWithMarkup": [{"String": "Wear gloves."}]}},
                        {"ReferenceNumber": 7, "Value": {"StringWithMarkup": [{"String": "Wear gloves."}]}},
                    ],
                }
            ],
        }
    }
    monkeypatch.setattr(pubchem, "_fetch_heading", lambda cid, heading: fixture)

    note = pubchem.get_safety_note(1, "Personal Protective Equipment (PPE)")

    assert note is not None
    assert len(note.excerpts) == 1  # refs 6 and 7 merge into one NIOSH group
    assert note.excerpts[0].text.count("Wear gloves.") == 1


def test_safety_note_dedupes_cross_label_repeated_excerpts(monkeypatch):
    # Reproduces the real bug (confirmed live 2026-07-12): hydrogen peroxide's PPE
    # heading cites the SAME excerpt verbatim under two entirely different labels —
    # "ERG Guide 140 [Oxidizers]" and "ERG Guide 143 [Oxidizers (Unstable)]" — with two
    # different CAMEO URLs (890 vs 19279). The per-(label, line) dedup inside the parse
    # loop only catches a repeat WITHIN one label/ReferenceNumber; this is a second,
    # later pass over the fully-built excerpt list that catches the same text appearing
    # under a second, different label too.
    fixture = {
        "Record": {
            "Reference": [
                {"ReferenceNumber": 6, "SourceName": "CAMEO Chemicals", "URL": "https://cameochemicals.noaa.gov/chemical/890"},
                {"ReferenceNumber": 7, "SourceName": "CAMEO Chemicals", "URL": "https://cameochemicals.noaa.gov/chemical/19279"},
            ],
            "Section": [
                {
                    "TOCHeading": "Personal Protective Equipment (PPE)",
                    "Information": [
                        {
                            "ReferenceNumber": 6,
                            "Value": {
                                "StringWithMarkup": [
                                    {"String": "Excerpt from ERG Guide 140 [Oxidizers]:"},
                                    {"String": "Wear positive pressure self-contained breathing apparatus (SCBA)."},
                                ]
                            },
                        },
                        {
                            "ReferenceNumber": 7,
                            "Value": {
                                "StringWithMarkup": [
                                    {"String": "Excerpt from ERG Guide 143 [Oxidizers (Unstable)]:"},
                                    {"String": "Wear positive pressure self-contained breathing apparatus (SCBA)."},
                                ]
                            },
                        },
                    ],
                }
            ],
        }
    }
    monkeypatch.setattr(pubchem, "_fetch_heading", lambda cid, heading: fixture)

    note = pubchem.get_safety_note(1, "Personal Protective Equipment (PPE)")

    assert note is not None
    assert len(note.excerpts) == 1  # the byte-identical second citation is dropped
    assert note.excerpts[0].source_label == "ERG Guide 140 [Oxidizers]"  # first occurrence kept, deterministic


def test_safety_note_groups_by_source_with_audience_labels(monkeypatch):
    # Reproduces the real shape confirmed live for sulfuric acid's PPE heading:
    # a "Excerpt from X:" marker names the true authority (NIOSH here rehosted
    # under CAMEO's own ReferenceNumber), and a separate, unlabelled reference
    # (HSDB) contributes text with no such marker.
    fixture = {
        "Record": {
            "Reference": [
                {"ReferenceNumber": 6, "SourceName": "CAMEO Chemicals", "URL": "https://example.invalid/cameo"},
                {"ReferenceNumber": 53, "SourceName": "Hazardous Substances Data Bank (HSDB)", "URL": "https://example.invalid/hsdb"},
            ],
            "Section": [
                {
                    "TOCHeading": "Personal Protective Equipment (PPE)",
                    "Information": [
                        {
                            "ReferenceNumber": 6,
                            "Value": {
                                "StringWithMarkup": [
                                    {"String": "Excerpt from NIOSH Pocket Guide for Sulfuric acid:"},
                                    {"String": "Skin: PREVENT SKIN CONTACT."},
                                ]
                            },
                        },
                        {
                            "ReferenceNumber": 53,
                            "Value": {"StringWithMarkup": [{"String": "Eye/face protection: Tightly fitting goggles."}]},
                        },
                    ],
                }
            ],
        }
    }
    monkeypatch.setattr(pubchem, "_fetch_heading", lambda cid, heading: fixture)

    note = pubchem.get_safety_note(1, "Personal Protective Equipment (PPE)")

    assert note is not None
    assert len(note.excerpts) == 2
    niosh = next(e for e in note.excerpts if e.audience == "niosh")
    other = next(e for e in note.excerpts if e.audience == "other")
    assert niosh.source_label == "NIOSH Pocket Guide for Sulfuric acid"
    assert "Skin: PREVENT SKIN CONTACT." in niosh.text
    assert "Excerpt from" not in niosh.text  # the marker line itself is stripped, not rendered as body text
    assert other.source_label == "Hazardous Substances Data Bank (HSDB)"  # falls back to the reference's SourceName
    assert "goggles" in other.text


def test_get_json_retries_on_transient_error_then_succeeds(monkeypatch, tmp_path):
    monkeypatch.setattr(pubchem._cache, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(pubchem, "_throttle", lambda: None)  # skip real sleeps in the test
    monkeypatch.setattr(pubchem.time, "sleep", lambda _seconds: None)

    calls = {"n": 0}

    class _FakeResponse:
        status_code = 200
        content = b'{"ok": true}'

        def raise_for_status(self):
            pass

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


def test_ground_chemical_survives_pubchem_outage(monkeypatch):
    """A genuine PubChem outage (not a 404 'not found') must not crash the caller —
    it must isolate to this one chemical and record it honestly as unknown, not absent."""

    def _always_raises(name):
        raise RuntimeError("PubChem request failed after 3 attempts: https://example.invalid/x")

    monkeypatch.setattr(pubchem, "resolve_cid", _always_raises)

    profile = pubchem.ground_chemical("hydrogen peroxide")  # must not raise

    assert profile.found is False
    assert profile.cid is None
    assert profile.grounding_error is not None
    assert "failed after 3 attempts" in profile.grounding_error


def test_grounding_error_distinct_from_not_found(monkeypatch):
    """found=False must mean two different things distinguishably: 'PubChem confirms this
    doesn't exist' (grounding_error=None) vs 'we don't know, the network failed'
    (grounding_error set). A network failure must never masquerade as confirmed absence.
    Both cases mocked — this must stay offline, not a live network call."""
    monkeypatch.setattr(pubchem, "resolve_cid", lambda name: None)  # simulates a clean 404
    not_found_profile = pubchem.ground_chemical("definitely-not-a-real-chemical-xyzzy-12345")
    assert not_found_profile.found is False
    assert not_found_profile.grounding_error is None
    assert not_found_profile.missing_sections == ["CID resolution"]

    def _always_raises(name):
        raise RuntimeError("PubChem request failed after 3 attempts: https://example.invalid/x")

    monkeypatch.setattr(pubchem, "resolve_cid", _always_raises)
    outage_profile = pubchem.ground_chemical("hydrogen peroxide")
    assert outage_profile.found is False
    assert outage_profile.grounding_error is not None


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
