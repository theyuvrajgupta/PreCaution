"""Tests for Stage 4 (app/brief.py::build_brief).

Offline, pure, no network, no API key, no `costly` marker — build_brief is
deterministic composition over already-fetched data, so every test here
constructs its inputs by hand. Reuses _demo_extraction_result() from
test_interactions.py (same locked demo protocol shape); adds a fuller
profile helper here since Stage 4 also needs ghs/safety_notes, which
test_interactions.py's bare _profile() (reactive groups only) doesn't set.
"""

from app.brief import build_brief
from app.interactions import find_step_interactions
from app.models import (
    Chemical,
    ChemicalHazardProfile,
    ExtractionResult,
    GHSInfo,
    ReactiveGroupEntry,
    SafetyExcerpt,
    SafetyNote,
    SourceRef,
    Step,
    StepChemicalRef,
)
from test_interactions import _demo_extraction_result


def _full_profile(canonical_name: str, cid: int, group_name: str | None = None) -> ChemicalHazardProfile:
    """A fully-grounded profile: GHS + all four safety-note headings + an
    optional reactive group. Every field a fully successful PubChem grounding
    run would populate."""
    reactive_groups = []
    if group_name:
        reactive_groups = [
            ReactiveGroupEntry(
                group_name=group_name,
                source=SourceRef(source_name="CAMEO Chemicals", url="https://cameochemicals.noaa.gov/chemical/x"),
            )
        ]
    ghs = GHSInfo(
        pictograms=["Corrosive"],
        pictogram_urls=["https://pubchem.ncbi.nlm.nih.gov/images/ghs/GHS05.svg"],
        signal_word="Danger",
        hazard_statements=["H314: Causes severe skin burns and eye damage"],
        precautionary_statements=["P260", "P280", "P305+P351+P338"],
        source=SourceRef(
            source_name="PubChem GHS Classification",
            url=f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}#section=GHS-Classification",
        ),
    )
    pubchem_ref = SourceRef(source_name="PubChem", url=f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}")
    safety_notes = [
        SafetyNote(
            heading="Personal Protective Equipment (PPE)",
            excerpts=[
                SafetyExcerpt(
                    source_label=f"NIOSH Pocket Guide for {canonical_name}",
                    audience="niosh",
                    text="Wear safety goggles or a face shield.",
                    source=pubchem_ref,
                ),
                SafetyExcerpt(
                    source_label="ERG Guide 140 [Oxidizers]",
                    audience="erg",
                    text="Wear positive pressure self-contained breathing apparatus (SCBA).",
                    source=pubchem_ref,
                ),
            ],
        ),
        SafetyNote(
            heading="First Aid Measures",
            excerpts=[
                SafetyExcerpt(
                    source_label="PubChem",
                    audience="other",
                    text="Flush eyes with water for at least 15 minutes. Remove contaminated clothing.",
                    source=pubchem_ref,
                ),
            ],
        ),
        SafetyNote(
            heading="Disposal Methods",
            excerpts=[
                SafetyExcerpt(
                    source_label="PubChem",
                    audience="other",
                    text="Dispose of contents/container in accordance with local regulations.",
                    source=pubchem_ref,
                ),
            ],
        ),
        SafetyNote(
            heading="Storage Conditions",
            excerpts=[
                SafetyExcerpt(
                    source_label="PubChem",
                    audience="other",
                    text="Store in a cool, dry, well-ventilated place away from incompatible materials.",
                    source=pubchem_ref,
                ),
            ],
        ),
    ]
    return ChemicalHazardProfile(
        query_name=canonical_name,
        found=True,
        cid=cid,
        pubchem_url=f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}",
        ghs=ghs,
        reactive_groups=reactive_groups,
        safety_notes=safety_notes,
    )


def _fully_grounded_demo_profiles() -> dict[str, ChemicalHazardProfile]:
    return {
        "hydrogen peroxide": _full_profile("hydrogen peroxide", 784, "Oxidizing Agents, Strong"),
        "sulfuric acid": _full_profile("sulfuric acid", 1118, "Acids, Strong Oxidizing"),
        "sodium azide": _full_profile("sodium azide", 33557, "Azo, Diazo, Azido, Hydrazine, and Azide Compounds"),
    }


def test_every_brief_statement_has_resolvable_source_ref():
    """The mandatory test from Build_Spec.md §4.3: 'this is grounded' must be a
    passing test, not just an assertion in the README."""
    result = _demo_extraction_result()
    profiles = _fully_grounded_demo_profiles()
    findings = find_step_interactions(result, profiles)

    brief = build_brief(result, profiles, findings)

    grounded_kinds = {"hazard_identity", "precautionary", "ppe", "first_aid", "disposal", "storage", "interaction_hazard"}
    assert brief.statements
    for statement in brief.statements:
        assert statement.source_ref, f"{statement.kind} statement has no source_ref: {statement.text!r}"
        if statement.kind in grounded_kinds:
            assert statement.source_url, f"{statement.kind} statement has no source_url: {statement.text!r}"


def test_unresolved_mentions_are_surfaced_as_statements():
    result = _demo_extraction_result()
    result = result.model_copy(update={"unresolved_mentions": ["the buffer stock solution"]})
    profiles = _fully_grounded_demo_profiles()
    findings = find_step_interactions(result, profiles)

    brief = build_brief(result, profiles, findings)

    unresolved = [s for s in brief.statements if s.kind == "unresolved_mention"]
    assert len(unresolved) == 1
    assert "the buffer stock solution" in unresolved[0].text
    assert unresolved[0].unverified is True
    assert unresolved[0].source_ref


def test_hazard_identity_carries_pictogram_urls():
    result = _demo_extraction_result()
    profiles = _fully_grounded_demo_profiles()
    findings = find_step_interactions(result, profiles)

    brief = build_brief(result, profiles, findings)

    hazard_identity = [s for s in brief.statements if s.kind == "hazard_identity"]
    assert hazard_identity
    for s in hazard_identity:
        assert s.pictogram_urls == ["https://pubchem.ncbi.nlm.nih.gov/images/ghs/GHS05.svg"]
        assert s.pictogram_labels == ["Corrosive"]
    # Only hazard_identity carries pictograms — no other kind should.
    non_hazard = [s for s in brief.statements if s.kind != "hazard_identity"]
    assert all(not s.pictogram_urls for s in non_hazard)


def test_precautionary_statement_resolves_known_p_codes():
    result = _demo_extraction_result()
    profiles = _fully_grounded_demo_profiles()
    findings = find_step_interactions(result, profiles)

    brief = build_brief(result, profiles, findings)

    precautionary = [s for s in brief.statements if s.kind == "precautionary" and "hydrogen peroxide" in s.text]
    assert precautionary
    text = precautionary[0].text
    # Known codes resolve to their official GHS text, not a bare code.
    assert "P260 — Do not breathe dust/fume/gas/mist/vapours/spray." in text
    assert "P280 — Wear protective gloves" in text
    # An unresolved code (not in our small demo-scoped table) falls back to the
    # bare code rather than inventing text — honest omission, not silently dropped.
    assert "P305+P351+P338" in text


def test_glove_disclosure_is_present_and_own_statement():
    result = _demo_extraction_result()
    profiles = _fully_grounded_demo_profiles()
    findings = find_step_interactions(result, profiles)

    brief = build_brief(result, profiles, findings)

    disclosures = [s for s in brief.statements if s.kind == "limitation_disclosure"]
    assert len(disclosures) == 1

    ppe_statements = [s for s in brief.statements if s.kind == "ppe"]
    assert ppe_statements
    for ppe in ppe_statements:
        assert disclosures[0].text not in ppe.text


def test_found_false_chemical_emits_no_data_not_silence():
    result = _demo_extraction_result()
    profiles = _fully_grounded_demo_profiles()
    profiles["sulfuric acid"] = ChemicalHazardProfile(
        query_name="sulfuric acid", found=False, missing_sections=["CID resolution"]
    )
    findings = find_step_interactions(result, profiles)

    brief = build_brief(result, profiles, findings)

    no_data = [s for s in brief.statements if s.kind == "no_data" and "c2" in s.chemical_ids]
    assert no_data, "expected a no_data statement for the ungrounded chemical, not silence"
    assert "sulfuric acid" in no_data[0].text.lower()

    # A confirmed absence (found=False, grounding_error=None) is NOT the same thing as an
    # incomplete brief — the brief is complete, it just honestly has nothing to report.
    assert brief.incomplete is False
    assert brief.incomplete_chemicals == []


def test_grounding_error_emits_grounding_incomplete_statement():
    """A transient PubChem failure (grounding_error set) must render as a distinct,
    explicit statement — never silently indistinguishable from a confirmed 'not found'."""
    result = _demo_extraction_result()
    profiles = _fully_grounded_demo_profiles()
    profiles["sulfuric acid"] = ChemicalHazardProfile(
        query_name="sulfuric acid", found=False, grounding_error="PubChem request failed after 3 attempts"
    )
    findings = find_step_interactions(result, profiles)

    brief = build_brief(result, profiles, findings)

    incomplete_statements = [
        s for s in brief.statements if s.kind == "grounding_incomplete" and "c2" in s.chemical_ids
    ]
    assert len(incomplete_statements) == 1
    text = incomplete_statements[0].text.lower()
    assert "unknown" in text
    assert "not" in text and ("safe" in text or "absent" in text)  # explicitly not a safety claim
    assert incomplete_statements[0].source_ref  # still resolvable, per the trust contract


def test_brief_incomplete_flag_and_chemicals_list():
    """Brief.incomplete/incomplete_chemicals is computed once here so the UI never has to
    inspect `profiles` itself to render the incompleteness banner."""
    result = _demo_extraction_result()
    profiles = _fully_grounded_demo_profiles()
    profiles["sulfuric acid"] = ChemicalHazardProfile(
        query_name="sulfuric acid", found=False, grounding_error="PubChem unreachable"
    )
    findings = find_step_interactions(result, profiles)

    brief = build_brief(result, profiles, findings)

    assert brief.incomplete is True
    assert brief.incomplete_chemicals == ["sulfuric acid"]


def test_missing_heading_emits_no_data():
    result = _demo_extraction_result()
    profiles = _fully_grounded_demo_profiles()
    acid = profiles["sulfuric acid"]
    acid.safety_notes = [n for n in acid.safety_notes if n.heading != "Disposal Methods"]
    acid.missing_sections = ["Disposal Methods"]
    findings = find_step_interactions(result, profiles)

    brief = build_brief(result, profiles, findings)

    matches = [
        s
        for s in brief.statements
        if s.kind == "no_data" and "c2" in s.chemical_ids and "disposal" in s.text.lower()
    ]
    assert matches


def test_missing_sections_aggregate_into_one_card_per_chemical():
    """Item 3: a chemical missing all five headings used to emit five near-identical
    "no data" cards (water/nitrogen/PBS each did) — that's the gap flood recreated one
    level down from the pair-gap aggregation. One card per chemical now, listing which
    sections are absent, still surfacing the gap honestly.
    """
    result = _demo_extraction_result()
    profiles = _fully_grounded_demo_profiles()
    acid = profiles["sulfuric acid"]
    acid.ghs = None
    acid.safety_notes = []
    acid.missing_sections = [
        "GHS Classification",
        "Personal Protective Equipment (PPE)",
        "First Aid Measures",
        "Disposal Methods",
        "Storage Conditions",
    ]
    findings = find_step_interactions(result, profiles)

    brief = build_brief(result, profiles, findings)

    gap_cards = [s for s in brief.statements if s.kind == "no_data" and "c2" in s.chemical_ids]
    assert len(gap_cards) == 1  # not five
    text = gap_cards[0].text
    assert "GHS classification" in text
    assert "PPE" in text
    assert "first aid" in text
    assert "disposal" in text
    assert "storage" in text
    assert "hazard-free" in text  # honest-omission language survives the aggregation


def test_piranha_interaction_hazard_statement_present():
    result = _demo_extraction_result()
    profiles = _fully_grounded_demo_profiles()
    findings = find_step_interactions(result, profiles)

    brief = build_brief(result, profiles, findings)

    hazard = [s for s in brief.statements if s.kind == "interaction_hazard" and 1 in s.step_numbers]
    assert len(hazard) == 1
    assert hazard[0].pair == ("c1", "c2")
    assert hazard[0].step_numbers == [1]  # this scaffold only has c1+c2 co-present in step 1
    assert "cameochemicals.noaa.gov" in (hazard[0].source_url or "")
    # short, chip-ready — not a citation sentence; the pairwise reactivity-documentation
    # page, not the generic single-group datasheet (2026-07-10 audit)
    assert hazard[0].source_ref == "NOAA CAMEO documentation/RG44-RG2"
    assert "Step 1:" not in hazard[0].text  # step number lives in step_numbers, not baked into prose


def test_interaction_hazard_chip_text_is_exactly_the_quote():
    """The item-1 audit fix, locked in as a test: the chipped block (BriefStatement.text)
    must be composed ENTIRELY from InteractionVerdict.categories (plus .example, only
    when every example_chemicals name is present in the protocol) — never concatenated
    with authored prose like "Combining X and Y" or a note. That authored framing lives
    in `lead_in` / `hazard_note` instead, both rendered separately with no chip. This is
    what makes it structurally impossible (not just a wording convention) to reintroduce
    the bug where an authored sentence rendered under a government-source citation chip.
    """
    result = _demo_extraction_result()
    profiles = _fully_grounded_demo_profiles()
    findings = find_step_interactions(result, profiles)
    protocol_names = {c.canonical_name for c in result.chemicals}

    brief = build_brief(result, profiles, findings)

    hazards = [s for s in brief.statements if s.kind == "interaction_hazard"]
    assert hazards
    for statement in hazards:
        pair_findings = [f for f in findings if {f.chemical_a_id, f.chemical_b_id} == set(statement.pair)]
        verdict = pair_findings[0].verdict
        assert verdict is not None
        if verdict.example and verdict.example_chemicals and all(n in protocol_names for n in verdict.example_chemicals):
            expected = f"{verdict.categories} {verdict.example}"
        else:
            expected = verdict.categories
        assert statement.text == expected
        # No word from lead_in/hazard_note may appear in text unless it's already in the
        # quote itself — guards against a future edit re-merging the fields.
        if statement.lead_in:
            assert statement.lead_in not in statement.text
        if statement.hazard_note:
            assert statement.hazard_note not in statement.text


def test_interaction_example_never_ships_for_a_chemical_it_doesnt_name():
    """2026-07-10 follow-up to the item-1 audit: a documented example must never render
    under a pair's chip unless every chemical it names is actually in the protocol.
    Regression-tests the exact failure mode reported: RG44-RG2's only documented
    example names metal chlorates, not hydrogen peroxide — the pair this demo protocol
    actually resolves to.
    """
    result = _demo_extraction_result()
    profiles = _fully_grounded_demo_profiles()
    findings = find_step_interactions(result, profiles)

    brief = build_brief(result, profiles, findings)

    peroxide_acid = next(
        s for s in brief.statements if s.kind == "interaction_hazard" and set(s.pair) == {"c1", "c2"}
    )
    assert "chlorate" not in peroxide_acid.text.lower()
    assert "ClO2" not in peroxide_acid.text

    azide_acid = next(
        s for s in brief.statements if s.kind == "interaction_hazard" and set(s.pair) == {"c2", "c3"}
    )
    assert "NaN3" in azide_acid.text  # sodium azide + sulfuric acid ARE both in this protocol


def test_interaction_no_data_is_surfaced():
    result = _demo_extraction_result()
    profiles = _fully_grounded_demo_profiles()
    del profiles["sulfuric acid"]  # no reactive-group data available at all for this chemical
    findings = find_step_interactions(result, profiles)

    brief = build_brief(result, profiles, findings)

    no_data = [s for s in brief.statements if s.kind == "interaction_no_data" and 1 in s.step_numbers]
    assert len(no_data) == 1
    assert no_data[0].text  # reused the finding's own note verbatim, not silence
    assert "not" in no_data[0].text.lower()


def test_step_context_flagged_unverified():
    result = _demo_extraction_result()
    profiles = _fully_grounded_demo_profiles()
    findings = find_step_interactions(result, profiles)

    brief = build_brief(result, profiles, findings)

    step_contexts = [s for s in brief.statements if s.kind == "step_context"]
    assert step_contexts
    for s in step_contexts:
        assert s.unverified is True
        assert s.source_url is None


def test_brief_steps_index_enables_grouping():
    result = _demo_extraction_result()
    profiles = _fully_grounded_demo_profiles()
    findings = find_step_interactions(result, profiles)

    brief = build_brief(result, profiles, findings)

    assert {step.number for step in result.steps} == {s.number for s in brief.steps}

    step1 = next(s for s in brief.steps if s.number == 1)
    assert set(step1.chemical_ids) == {"c1", "c2"}

    step5 = next(s for s in brief.steps if s.number == 5)
    assert set(step5.chemical_ids) == {"c3", "c2"}


def test_brief_steps_carry_origin_and_vessel_for_the_thread():
    """BriefStep.chemicals/vessel are what the carryover thread (UI_Design_Spec.md §6.1)
    draws from: a token at 'added', a continuing line at 'carried_over', a tick on a
    vessel change. Must survive Stage 4 unchanged from Step.chemicals_present/Step.vessel."""
    result = _demo_extraction_result()
    profiles = _fully_grounded_demo_profiles()
    findings = find_step_interactions(result, profiles)

    brief = build_brief(result, profiles, findings)

    step1 = next(s for s in brief.steps if s.number == 1)
    origins = {ref.chemical_id: ref.origin for ref in step1.chemicals}
    assert origins == {"c1": "added", "c2": "added"}

    source_steps = {s.number: s for s in result.steps}
    for brief_step in brief.steps:
        assert brief_step.vessel == source_steps[brief_step.number].vessel


def test_repeated_pair_across_steps_collapses_to_one_statement():
    """The dedup itself, unit-tested directly against build_brief — not just observed
    incidentally via the full pipeline fixture. A pair persisting across 3 steps must
    produce ONE interaction_hazard statement carrying all 3 step numbers, not 3 near-
    identical statements. This is what makes the carryover thread (one onset diamond +
    a continuous hot span) renderable correctly instead of misrepresenting a persisting
    hazard as separate events."""
    chemicals = [
        Chemical(id="c1", as_written="hydrogen peroxide", canonical_name="hydrogen peroxide", resolution_reasoning="x"),
        Chemical(id="c2", as_written="sulfuric acid", canonical_name="sulfuric acid", resolution_reasoning="x"),
    ]
    steps = [
        Step(
            number=n,
            text=f"Step {n} text.",
            chemicals_present=[
                StepChemicalRef(chemical_id="c1", origin="added" if n == 1 else "carried_over"),
                StepChemicalRef(chemical_id="c2", origin="added" if n == 1 else "carried_over"),
            ],
        )
        for n in (1, 2, 3)
    ]
    result = ExtractionResult(chemicals=chemicals, steps=steps)
    profiles = {
        "hydrogen peroxide": _full_profile("hydrogen peroxide", 784, "Oxidizing Agents, Strong"),
        "sulfuric acid": _full_profile("sulfuric acid", 1118, "Acids, Strong Oxidizing"),
    }
    findings = find_step_interactions(result, profiles)
    assert len(findings) == 3  # Stage 3 correctly reports the pair present in all 3 steps

    brief = build_brief(result, profiles, findings)

    hazards = [s for s in brief.statements if s.kind == "interaction_hazard"]
    assert len(hazards) == 1, "one persisting hazard must be one statement, not one per step"
    assert hazards[0].step_numbers == [1, 2, 3]
    assert hazards[0].pair == ("c1", "c2")
