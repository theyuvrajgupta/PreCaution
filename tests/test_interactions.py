from app.interactions import find_step_interactions
from app.models import (
    Chemical,
    ChemicalHazardProfile,
    ExtractionResult,
    ReactiveGroupEntry,
    SourceRef,
    Step,
    StepChemicalRef,
)


def _profile(canonical_name: str, cid: int, group_name: str | None) -> ChemicalHazardProfile:
    groups = []
    if group_name:
        groups = [
            ReactiveGroupEntry(
                group_name=group_name,
                source=SourceRef(source_name="CAMEO Chemicals", url="https://cameochemicals.noaa.gov/chemical/x"),
            )
        ]
    return ChemicalHazardProfile(
        query_name=canonical_name,
        found=True,
        cid=cid,
        pubchem_url=f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}",
        reactive_groups=groups,
    )


def _demo_extraction_result() -> ExtractionResult:
    """Mirrors the locked demo protocol's structure: peroxide+acid mixed
    directly in step 1, azide meeting carried-over acid in step 5."""
    chemicals = [
        Chemical(
            id="c1",
            as_written="30% hydrogen peroxide",
            canonical_name="hydrogen peroxide",
            concentration="30%",
            resolution_reasoning="Directly named; concentration split into its own field.",
        ),
        Chemical(
            id="c2",
            as_written="concentrated sulfuric acid",
            canonical_name="sulfuric acid",
            concentration="concentrated",
            resolution_reasoning="Directly named; qualifier split into its own field.",
        ),
        Chemical(
            id="c3",
            as_written="sodium azide",
            canonical_name="sodium azide",
            resolution_reasoning="Directly named within the PBS buffer description.",
        ),
    ]
    steps = [
        Step(
            number=1,
            text="Prepare piranha solution by slowly adding 30 mL of 30% hydrogen peroxide to 90 mL of concentrated sulfuric acid.",
            chemicals_present=[
                StepChemicalRef(chemical_id="c1", origin="added"),
                StepChemicalRef(chemical_id="c2", origin="added"),
            ],
        ),
        Step(
            number=5,
            text="Rinse the glassware used for the protein purification buffer (PBS with 0.02% sodium azide) and add that rinse to the same acid waste carboy.",
            vessel="acid waste carboy",
            chemicals_present=[
                StepChemicalRef(chemical_id="c3", origin="added"),
                StepChemicalRef(chemical_id="c2", origin="carried_over"),
            ],
        ),
    ]
    return ExtractionResult(chemicals=chemicals, steps=steps)


def test_step1_piranha_hazard_found():
    result = _demo_extraction_result()
    profiles = {
        "hydrogen peroxide": _profile("hydrogen peroxide", 784, "Oxidizing Agents, Strong"),
        "sulfuric acid": _profile("sulfuric acid", 1118, "Acids, Strong Oxidizing"),
        "sodium azide": _profile("sodium azide", 33557, "Azo, Diazo, Azido, Hydrazine, and Azide Compounds"),
    }
    findings = find_step_interactions(result, profiles)

    step1_findings = [f for f in findings if f.step_number == 1]
    assert len(step1_findings) == 1
    assert step1_findings[0].status == "hazard_found"
    assert "explosion" in step1_findings[0].verdict.hazard_types


def test_step5_waste_stream_hazard_found_via_carried_over():
    result = _demo_extraction_result()
    profiles = {
        "hydrogen peroxide": _profile("hydrogen peroxide", 784, "Oxidizing Agents, Strong"),
        "sulfuric acid": _profile("sulfuric acid", 1118, "Acids, Strong Oxidizing"),
        "sodium azide": _profile("sodium azide", 33557, "Azo, Diazo, Azido, Hydrazine, and Azide Compounds"),
    }
    findings = find_step_interactions(result, profiles)

    step5_findings = [f for f in findings if f.step_number == 5]
    assert len(step5_findings) == 1
    finding = step5_findings[0]
    assert finding.status == "hazard_found"
    assert "toxic_gas" in finding.verdict.hazard_types
    # The whole point: this hazard is only visible because carried_over is tracked.
    origins = {finding.origin_a, finding.origin_b}
    assert origins == {"added", "carried_over"}


def test_concentration_round_trips_onto_the_finding():
    """Chemical.concentration is captured at extraction; ChemicalPairFinding must carry
    it through untouched so app/brief.py can show it, without this module's own hazard
    logic ever reading it (the verdict is identical regardless of concentration)."""
    result = _demo_extraction_result()
    profiles = {
        "hydrogen peroxide": _profile("hydrogen peroxide", 784, "Oxidizing Agents, Strong"),
        "sulfuric acid": _profile("sulfuric acid", 1118, "Acids, Strong Oxidizing"),
        "sodium azide": _profile("sodium azide", 33557, "Azo, Diazo, Azido, Hydrazine, and Azide Compounds"),
    }
    findings = find_step_interactions(result, profiles)

    step1_finding = next(f for f in findings if f.step_number == 1)
    assert step1_finding.concentration_a == "30%"  # c1 = hydrogen peroxide
    assert step1_finding.concentration_b == "concentrated"  # c2 = sulfuric acid

    step5_finding = next(f for f in findings if f.step_number == 5)
    # c3 = sodium azide has no concentration set in this scaffold, and c2 = sulfuric
    # acid's carries through unchanged regardless of which side of the pair it's on.
    assert step5_finding.concentration_a is None
    assert step5_finding.concentration_b == "concentrated"


def test_missing_reactive_group_data_is_surfaced_not_silent():
    result = _demo_extraction_result()
    profiles = {
        "hydrogen peroxide": _profile("hydrogen peroxide", 784, "Oxidizing Agents, Strong"),
        # sulfuric acid profile deliberately omitted -> no reactive-group data available
        "sodium azide": _profile("sodium azide", 33557, "Azo, Diazo, Azido, Hydrazine, and Azide Compounds"),
    }
    findings = find_step_interactions(result, profiles)

    step1_finding = next(f for f in findings if f.step_number == 1)
    assert step1_finding.status == "insufficient_reactive_group_data"
    assert step1_finding.verdict is None
    assert "sulfuric acid" in step1_finding.note
    assert "do not assume" in step1_finding.note.lower()


def test_unrelated_known_groups_report_no_established_data_not_safe():
    result = ExtractionResult(
        chemicals=[
            Chemical(id="c1", as_written="ethanol", canonical_name="ethanol", resolution_reasoning="Direct match."),
            Chemical(id="c2", as_written="water", canonical_name="water", resolution_reasoning="Direct match."),
        ],
        steps=[
            Step(
                number=1,
                text="Dilute ethanol with water.",
                chemicals_present=[
                    StepChemicalRef(chemical_id="c1", origin="added"),
                    StepChemicalRef(chemical_id="c2", origin="added"),
                ],
            )
        ],
    )
    profiles = {
        "ethanol": _profile("ethanol", 702, "Alcohols and Glycols"),
        "water": _profile("water", 962, "Water and Aqueous Solutions"),
    }
    findings = find_step_interactions(result, profiles)

    assert len(findings) == 1
    assert findings[0].status == "no_established_data"
    assert findings[0].verdict is None
    assert "not" in findings[0].note.lower()  # explicitly says this isn't a safety claim


def test_not_chemically_reactive_pair_states_classification_not_absence():
    # UI_Design_Spec.md §21's "free grounding win": nitrogen's CAMEO reactive group is
    # literally "Not Chemically Reactive" (confirmed live 2026-07-10) — that's a real
    # assignment, not missing data, and the no-data note should say so.
    result = ExtractionResult(
        chemicals=[
            Chemical(id="c1", as_written="water", canonical_name="water", resolution_reasoning="Direct match."),
            Chemical(id="c2", as_written="nitrogen", canonical_name="nitrogen", resolution_reasoning="Direct match."),
        ],
        steps=[
            Step(
                number=3,
                text="Rinse with deionized water and dry under a stream of nitrogen.",
                chemicals_present=[
                    StepChemicalRef(chemical_id="c1", origin="added"),
                    StepChemicalRef(chemical_id="c2", origin="added"),
                ],
            )
        ],
    )
    profiles = {
        "water": _profile("water", 962, "Water and Aqueous Solutions"),
        "nitrogen": _profile("nitrogen", 947, "Not Chemically Reactive"),
    }
    findings = find_step_interactions(result, profiles)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.status == "no_established_data"  # still not a matrix hazard verdict
    assert "Nitrogen is classified **Not Chemically Reactive**" in finding.note  # sentence-case leading fragment
    assert "does not establish" in finding.note  # never concludes safety
    assert finding.classification_source is not None
    assert finding.classification_source.url == "https://cameochemicals.noaa.gov/chemical/x"
