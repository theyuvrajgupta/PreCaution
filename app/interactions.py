"""Finds interaction hazards within each protocol step.

This module is the seam between the three pipeline stages — it doesn't do
grounding (app/pubchem.py) or verdict lookup (app/interaction_matrix.py)
itself, it only combines their outputs:

  1. extraction's step-model tells us which chemicals are co-present in a
     step's vessel, and how (added / carried_over / residual);
  2. each chemical's PubChem-grounded reactive groups tell us what class it
     belongs to;
  3. the local matrix tells us whether a given pair of classes is a known
     danger.

Every chemical pair that shares a vessel in a step produces exactly one
ChemicalPairFinding — never silently skipped. A pair either has a known
hazard, has no established data for that specific group combination, or
lacks the reactive-group data needed to even check. All three outcomes are
explicit and distinguishable so the honest-omission story holds at the
interaction layer too, not just at the per-chemical layer.
"""

from itertools import combinations
from typing import Literal

from pydantic import BaseModel, Field

from app.interaction_matrix import InteractionVerdict, lookup_verdict
from app.models import (
    ChemicalHazardProfile,
    ExtractionResult,
    ReactiveGroupEntry,
    SourceRef,
)

# CAMEO's own reactive-group taxonomy (surfaced live via app/pubchem.py) includes this
# as a genuine assignment, not an absence of data — e.g. nitrogen is classified this way.
# Worth stating as a classification rather than folding it into the generic
# "no established data" phrasing (UI_Design_Spec.md §21's "free grounding win").
_NOT_REACTIVE_GROUP = "Not Chemically Reactive"


class ChemicalPairFinding(BaseModel):
    step_number: int
    chemical_a_id: str
    chemical_b_id: str
    chemical_a_name: str
    chemical_b_name: str
    origin_a: Literal["added", "carried_over", "residual"]
    origin_b: Literal["added", "carried_over", "residual"]
    added_step_a: int | None = Field(
        default=None,
        description="EARLIEST step <= step_number where chemical_a's origin was 'added' — its true point of "
        "introduction into the protocol. Equals step_number when origin_a=='added' at this step. Deliberately "
        "the first such tag, not the most recent: a later step re-tagging the chemical 'added' when it's "
        "poured into a new vessel is a vessel transition (see vessel_entry_step_a), not a second origin "
        "(2026-07-10 follow-up to the item-1/item-2 audit).",
    )
    added_step_b: int | None = Field(default=None, description="Same as added_step_a, for chemical_b.")
    vessel: str | None = Field(default=None, description="Step.vessel at step_number — the vessel this pair is "
        "co-present in.")
    vessel_entry_step_a: int | None = Field(
        default=None,
        description="Most recent step < step_number at which chemical_a is recorded present in the SAME "
        "vessel as `vessel`, found by walking backward and stopping at the first step it's recorded in a "
        "DIFFERENT named vessel. None if there's no vessel data to confirm a transition — never guessed. "
        "Distinct from added_step_a: a chemical can be added in step 1 and only enter the vessel of a later "
        "finding in some subsequent step (e.g. poured into a waste carboy) — naming the origin step alone "
        "would wrongly imply it's been in this vessel the whole time.",
    )
    vessel_entry_step_b: int | None = Field(default=None, description="Same as vessel_entry_step_a, for chemical_b.")
    concentration_a: str | None = Field(
        default=None,
        description="Chemical.concentration as extraction captured it (e.g. '0.02%', 'concentrated'). Carried "
        "through so app/brief.py can name it next to the chemical — it is NOT used anywhere in this module's "
        "own hazard logic; the verdict is identical regardless of concentration (see README limitations).",
    )
    concentration_b: str | None = Field(default=None, description="Same as concentration_a, for chemical_b.")
    status: Literal["hazard_found", "no_established_data", "insufficient_reactive_group_data"]
    verdict: InteractionVerdict | None = None
    note: str | None = None
    classification_source: SourceRef | None = Field(
        default=None,
        description="Set only when note states a real CAMEO reactive-group classification (e.g. 'Not "
        "Chemically Reactive') rather than a generic no-data statement — the source behind that specific "
        "claim, so the UI can cite it instead of the generic interaction-table placeholder.",
    )


def _find_classification(name: str, entries: list[ReactiveGroupEntry], group_name: str) -> ReactiveGroupEntry | None:
    return next((e for e in entries if e.group_name == group_name), None)


def _find_added_step(chemical_id: str, up_to_step: int, steps: list) -> int | None:
    """EARLIEST step <= up_to_step where this chemical's origin was 'added' — the step
    it truly entered the picture. Stops at the first match rather than the most recent:
    see ChemicalPairFinding.added_step_a for why (a vessel transfer re-tagged 'added' is
    not a second origin)."""
    for step in steps:
        if step.number > up_to_step:
            break
        for ref in step.chemicals_present:
            if ref.chemical_id == chemical_id and ref.origin == "added":
                return step.number
    return None


def _find_vessel_entry_step(chemical_id: str, up_to_step: int, steps: list) -> int | None:
    """Most recent step < up_to_step at which this chemical is recorded present in the
    same vessel it's in at up_to_step, walking backward and stopping at the first step
    it's recorded in a different named vessel. None if there's no vessel data to place a
    transition (e.g. Step.vessel unset for the intervening steps) — never guessed."""
    current_vessel = next((s.vessel for s in steps if s.number == up_to_step), None)
    if not current_vessel:
        return None
    entry_step: int | None = None
    for step in sorted((s for s in steps if s.number < up_to_step), key=lambda s: s.number, reverse=True):
        present = any(ref.chemical_id == chemical_id for ref in step.chemicals_present)
        if not present:
            continue
        if step.vessel == current_vessel:
            entry_step = step.number
        elif step.vessel:
            break
    return entry_step


def _no_data_note(
    name_a: str, entries_a: list[ReactiveGroupEntry], name_b: str, entries_b: list[ReactiveGroupEntry]
) -> tuple[str, SourceRef | None]:
    """Text (and, if applicable, its source) for a pair with no matrix verdict.

    If either chemical's reactive group is CAMEO's own "Not Chemically Reactive"
    assignment, state that classification rather than the generic no-data phrasing —
    it's a real, live-fetched grounding result, not an absence (§21's "free win").
    Never concludes safety from it.
    """
    classified = [
        (name, _find_classification(name, entries, _NOT_REACTIVE_GROUP))
        for name, entries in ((name_a, entries_a), (name_b, entries_b))
    ]
    classified = [(name, entry) for name, entry in classified if entry is not None]
    if classified:
        stated = " and ".join(f"{name} is classified **{_NOT_REACTIVE_GROUP}**" for name, _ in classified)
        text = (
            f"{stated} (CAMEO). This does not establish that combining {name_a} and {name_b} is safe — "
            f"only that {'this chemical has' if len(classified) == 1 else 'these chemicals have'} no known "
            f"reactive hazard class of its own. Consult an SDS."
        )
        return text, classified[0][1].source

    groups_a_names = [e.group_name for e in entries_a]
    groups_b_names = [e.group_name for e in entries_b]
    text = (
        f"No established interaction data in our local table for "
        f"{name_a} ({', '.join(groups_a_names)}) + {name_b} ({', '.join(groups_b_names)}). "
        f"This does not mean the combination is safe — only that it is not in our current reference set."
    )
    return text, None


def find_step_interactions(
    result: ExtractionResult, profiles: dict[str, ChemicalHazardProfile]
) -> list[ChemicalPairFinding]:
    """profiles is keyed by Chemical.canonical_name (the same key used when
    grounding each chemical via app.pubchem.ground_chemical)."""
    chem_by_id = {c.id: c for c in result.chemicals}
    findings: list[ChemicalPairFinding] = []

    for step in result.steps:
        for ref_a, ref_b in combinations(step.chemicals_present, 2):
            chem_a = chem_by_id.get(ref_a.chemical_id)
            chem_b = chem_by_id.get(ref_b.chemical_id)
            if chem_a is None or chem_b is None:
                continue  # extraction referenced an id it never defined; not this module's failure mode to model

            profile_a = profiles.get(chem_a.canonical_name)
            profile_b = profiles.get(chem_b.canonical_name)
            entries_a = profile_a.reactive_groups if profile_a else []
            entries_b = profile_b.reactive_groups if profile_b else []
            groups_a = [g.group_name for g in entries_a]
            groups_b = [g.group_name for g in entries_b]

            base = {
                "step_number": step.number,
                "chemical_a_id": chem_a.id,
                "chemical_b_id": chem_b.id,
                "chemical_a_name": chem_a.canonical_name,
                "chemical_b_name": chem_b.canonical_name,
                "origin_a": ref_a.origin,
                "origin_b": ref_b.origin,
                "added_step_a": _find_added_step(chem_a.id, step.number, result.steps),
                "added_step_b": _find_added_step(chem_b.id, step.number, result.steps),
                "vessel": step.vessel,
                "vessel_entry_step_a": _find_vessel_entry_step(chem_a.id, step.number, result.steps),
                "vessel_entry_step_b": _find_vessel_entry_step(chem_b.id, step.number, result.steps),
                "concentration_a": chem_a.concentration,
                "concentration_b": chem_b.concentration,
            }

            if not groups_a or not groups_b:
                missing = [name for name, groups in ((chem_a.canonical_name, groups_a), (chem_b.canonical_name, groups_b)) if not groups]
                findings.append(
                    ChemicalPairFinding(
                        **base,
                        status="insufficient_reactive_group_data",
                        note=(
                            f"Could not find authoritative reactive-group data for {' and '.join(missing)}. "
                            f"Do not assume this combination is safe — consult an SDS."
                        ),
                    )
                )
                continue

            verdict = _first_match(groups_a, groups_b)
            if verdict is not None:
                findings.append(ChemicalPairFinding(**base, status="hazard_found", verdict=verdict))
            else:
                note, classification_source = _no_data_note(
                    chem_a.canonical_name, entries_a, chem_b.canonical_name, entries_b
                )
                findings.append(
                    ChemicalPairFinding(
                        **base,
                        status="no_established_data",
                        note=note,
                        classification_source=classification_source,
                    )
                )

    return findings


def _first_match(groups_a: list[str], groups_b: list[str]) -> InteractionVerdict | None:
    for ga in groups_a:
        for gb in groups_b:
            verdict = lookup_verdict(ga, gb)
            if verdict is not None:
                return verdict
    return None
