"""Tests for entity extraction.

Two tiers:
- test_offline_fixture_validates: fast, no API key needed. Validates a
  captured live response against the ExtractionResult schema, guarding
  the parse/validation layer independent of the network.
- test_live_extraction_on_demo_protocol: real API call against the locked
  demo protocol. Marked `costly` (spends real Anthropic budget) so it's
  excluded from the default `pytest` run (see pytest.ini) — opt in with
  `pytest -m costly`. Also skips if ANTHROPIC_API_KEY isn't set.
"""

import json
from pathlib import Path

import pytest

from app.config import get_settings
from app.models import ExtractionResult

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_offline_fixture_validates():
    fixture_path = FIXTURES / "extraction_response.json"
    if not fixture_path.exists():
        pytest.skip(
            "No captured fixture yet at tests/fixtures/extraction_response.json — "
            "run scripts/run_extraction.py against the demo protocol once and save its output there."
        )
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    result = ExtractionResult.model_validate(raw)

    canonical_names = {c.canonical_name.lower() for c in result.chemicals}
    assert "hydrogen peroxide" in canonical_names
    assert "sulfuric acid" in canonical_names
    assert "sodium azide" in canonical_names


@pytest.mark.costly
@pytest.mark.skipif(not get_settings().anthropic_api_key, reason="ANTHROPIC_API_KEY not set")
def test_live_extraction_on_demo_protocol():
    from app.extraction import extract

    protocol_text = (FIXTURES / "demo_protocol.txt").read_text(encoding="utf-8")
    result = extract(protocol_text)

    canonical_names = {c.canonical_name.lower() for c in result.chemicals}
    assert "hydrogen peroxide" in canonical_names
    assert "sulfuric acid" in canonical_names
    assert "sodium azide" in canonical_names

    # Step 1: peroxide + sulfuric acid both freshly added together.
    step1 = next(s for s in result.steps if s.number == 1)
    step1_chem_ids = {ref.chemical_id for ref in step1.chemicals_present}
    peroxide_id = next(c.id for c in result.chemicals if c.canonical_name.lower() == "hydrogen peroxide")
    acid_id = next(c.id for c in result.chemicals if c.canonical_name.lower() == "sulfuric acid")
    assert peroxide_id in step1_chem_ids
    assert acid_id in step1_chem_ids

    # Step 5: azide added to a vessel that carries over acidic spent piranha.
    step5 = next(s for s in result.steps if s.number == 5)
    azide_id = next(c.id for c in result.chemicals if c.canonical_name.lower() == "sodium azide")
    azide_refs = [ref for ref in step5.chemicals_present if ref.chemical_id == azide_id]
    assert azide_refs and azide_refs[0].origin == "added"

    step5_origins = {ref.origin for ref in step5.chemicals_present}
    assert "carried_over" in step5_origins, "Expected acidic waste to be modeled as carried_over into step 5"

    assert result.unresolved_mentions == []
