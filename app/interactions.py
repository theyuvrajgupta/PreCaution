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

from pydantic import BaseModel

from app.interaction_matrix import InteractionVerdict, lookup_verdict
from app.models import ChemicalHazardProfile, ExtractionResult


class ChemicalPairFinding(BaseModel):
    step_number: int
    chemical_a_id: str
    chemical_b_id: str
    chemical_a_name: str
    chemical_b_name: str
    origin_a: Literal["added", "carried_over", "residual"]
    origin_b: Literal["added", "carried_over", "residual"]
    status: Literal["hazard_found", "no_established_data", "insufficient_reactive_group_data"]
    verdict: InteractionVerdict | None = None
    note: str | None = None


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
            groups_a = [g.group_name for g in profile_a.reactive_groups] if profile_a else []
            groups_b = [g.group_name for g in profile_b.reactive_groups] if profile_b else []

            base = dict(
                step_number=step.number,
                chemical_a_id=chem_a.id,
                chemical_b_id=chem_b.id,
                chemical_a_name=chem_a.canonical_name,
                chemical_b_name=chem_b.canonical_name,
                origin_a=ref_a.origin,
                origin_b=ref_b.origin,
            )

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
                findings.append(
                    ChemicalPairFinding(
                        **base,
                        status="no_established_data",
                        note=(
                            f"No established interaction data in our local table for "
                            f"{chem_a.canonical_name} ({', '.join(groups_a)}) + "
                            f"{chem_b.canonical_name} ({', '.join(groups_b)}). "
                            f"This does not mean the combination is safe — only that it is not in our current "
                            f"reference set."
                        ),
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
