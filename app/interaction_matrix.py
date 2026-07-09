"""Pairwise reactive-group danger verdicts — the second grounding layer.

Architecture boundary (deliberately kept clean — see Project_Baseline_Context.md
Section 2.3 and the Jul 9 2026 build-tracker notes):

  - Per-chemical reactive-group ASSIGNMENT comes LIVE from PubChem
    (app/pubchem.py), which itself passes through NOAA CAMEO Chemicals data.
  - The pairwise DANGER VERDICT for a given pair of reactive groups comes
    from THIS local, offline, hand-encoded table — never from free-form
    model output.

These two layers must stay separate modules with a narrow interface between
them (reactive group names in, a verdict or None out). That separation is
what lets the tool honestly say, in the demo, "this verdict is looked up,
not generated." If grounding and verdict logic get tangled together, that
claim stops being defensible.

Provenance: every entry below was hand-encoded from NOAA CAMEO Chemicals'
own reactive-group datasheets (cameochemicals.noaa.gov/react/<id>), fetched
and quoted directly on 2026-07-09 during build — NOT generated from general
chemistry knowledge. This is a SEED set covering only the pairs the locked
demo protocol needs. Extend deliberately: fetch the actual datasheet for any
new pair before adding it, never from memory.
"""

from pydantic import BaseModel

from app.models import SourceRef


class InteractionVerdict(BaseModel):
    group_a: str
    group_b: str
    hazard_types: list[str]
    summary: str
    source: SourceRef


_TABLE: dict[frozenset[str], InteractionVerdict] = {}


def _pair_key(group_a: str, group_b: str) -> frozenset[str]:
    return frozenset((group_a, group_b))


def _add(group_a: str, group_b: str, hazard_types: list[str], summary: str, source_url: str, source_detail: str) -> None:
    verdict = InteractionVerdict(
        group_a=group_a,
        group_b=group_b,
        hazard_types=hazard_types,
        summary=summary,
        source=SourceRef(
            source_name="CAMEO Chemicals reactive group datasheet",
            url=source_url,
            detail=source_detail,
        ),
    )
    _TABLE[_pair_key(group_a, group_b)] = verdict


# --- Seed entries, sourced 2026-07-09 -------------------------------------

_add(
    "Oxidizing Agents, Strong",
    "Acids, Strong Oxidizing",
    hazard_types=["heat", "fire", "explosion"],
    summary=(
        "Strong oxidizing agents react vigorously with other compounds, generating heat and possibly gaseous "
        "products that can pressurize a closed container. Combining with a strong oxidizing acid amplifies "
        "oxidizing strength, creating particularly dangerous heat-generation and explosion risk. This is the "
        "classic 'piranha solution' hazard pathway (hydrogen peroxide + sulfuric acid)."
    ),
    source_url="https://cameochemicals.noaa.gov/react/44",
    source_detail="Oxidizing Agents, Strong",
)

_add(
    "Azo, Diazo, Azido, Hydrazine, and Azide Compounds",
    "Acids, Strong Oxidizing",
    hazard_types=["toxic_gas", "explosion"],
    summary=(
        "Mixing azo/diazo/azido/hydrazine/azide compounds with strong oxidizing acids generates toxic gases "
        "(hydrazoic acid) with potential for an explosive combination."
    ),
    source_url="https://cameochemicals.noaa.gov/react/8",
    source_detail="Azo, Diazo, Azido, Hydrazine, and Azide Compounds",
)

_add(
    "Azo, Diazo, Azido, Hydrazine, and Azide Compounds",
    "Acids, Strong Non-oxidizing",
    hazard_types=["toxic_gas"],
    summary=(
        "Mixing azo/diazo/azido/hydrazine/azide compounds with strong non-oxidizing acids produces toxic gases "
        "(hydrazoic acid)."
    ),
    source_url="https://cameochemicals.noaa.gov/react/8",
    source_detail="Azo, Diazo, Azido, Hydrazine, and Azide Compounds",
)


def lookup_verdict(group_a: str, group_b: str) -> InteractionVerdict | None:
    """Look up the danger verdict for a pair of reactive groups.

    Returns None if this specific pair isn't in the table. None means
    "no established data in our local set" — it is NOT a safety claim.
    Callers must never treat None as "safe" (see app/interactions.py's
    honest-omission handling).
    """
    return _TABLE.get(_pair_key(group_a, group_b))


def known_pairs() -> list[tuple[str, str]]:
    """For introspection/debugging: every (group_a, group_b) pair currently in the table."""
    return [(v.group_a, v.group_b) for v in _TABLE.values()]
