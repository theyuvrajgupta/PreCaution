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

**Quote vs. note — read before adding or editing an entry (2026-07-10 audit).**
An earlier version of this file put an authored sentence ("This is the
classic 'piranha solution' hazard pathway...") directly inside the same
string that was rendered under a `[NOAA CAMEO react/44]` source chip. That
sentence does not appear anywhere on CAMEO's site — confirmed by re-fetching
both the generic single-group datasheet (cameochemicals.noaa.gov/react/44)
AND the actual pairwise reactivity-documentation page it links to
(cameochemicals.noaa.gov/reactivity/documentation/RG44-RG2). A judge checking
the chip against the source would find nothing matching the last sentence —
exactly the failure mode this tool exists to prevent, committed by the tool
itself.

Fix, structural not cosmetic: `InteractionVerdict` no longer has a single
`summary` string. It has `quote` (verbatim or near-verbatim from the
datasheet — the ONLY thing that may render under the source chip) and an
optional `note` (authored, rendered as a separate unchipped line, nominal
facts only — e.g. a common name — never a hazard claim). See
`tests/test_interaction_matrix.py::test_quote_is_grounded_in_the_source_page`
for the check that keeps this from silently regressing.

Provenance: every quote below was fetched directly from CAMEO's own pairwise
reactivity-documentation pages (not the generic single-group datasheets,
which don't carry pair-specific predictions) on 2026-07-10 during the item-1
audit — re-verifying and superseding the 2026-07-09 seed entries, which had
cited the generic /react/<id> URLs. This is a SEED set covering only the
pairs the locked demo protocol needs. Extend deliberately: fetch the actual
*pairwise* datasheet for any new pair before adding it, never from memory,
and never let authored prose share a string with a quote.
"""

from pydantic import BaseModel, Field

from app.models import SourceRef


class InteractionVerdict(BaseModel):
    group_a: str
    group_b: str
    hazard_types: list[str]
    quote: str = Field(description="Verbatim or near-verbatim text from the CAMEO pairwise reactivity-documentation "
                                    "page. The ONLY field that may render under the source chip.")
    note: str | None = Field(
        default=None,
        description="Authored, e.g. a common name for the combination. Rendered as a separate line, visibly "
        "outside the chipped block, with no source chip. Nominal facts only — never a hazard claim.",
    )
    source: SourceRef


_TABLE: dict[frozenset[str], InteractionVerdict] = {}


def _pair_key(group_a: str, group_b: str) -> frozenset[str]:
    return frozenset((group_a, group_b))


def _add(
    group_a: str,
    group_b: str,
    hazard_types: list[str],
    quote: str,
    source_url: str,
    source_detail: str,
    note: str | None = None,
) -> None:
    verdict = InteractionVerdict(
        group_a=group_a,
        group_b=group_b,
        hazard_types=hazard_types,
        quote=quote,
        note=note,
        source=SourceRef(
            source_name="CAMEO Chemicals reactivity documentation",
            url=source_url,
            detail=source_detail,
        ),
    )
    _TABLE[_pair_key(group_a, group_b)] = verdict


# --- Seed entries, re-sourced 2026-07-10 from the pairwise reactivity-
# documentation pages (not the generic single-group datasheets) -----------

_add(
    "Oxidizing Agents, Strong",
    "Acids, Strong Oxidizing",
    hazard_types=["explosion", "toxic_gas"],
    quote=(
        "Explosive: Reaction products may be explosive or sensitive to shock or friction. Generates gas: "
        "Reaction liberates gaseous products and may cause pressurization. Intense or explosive reaction: "
        "Reaction may be particularly intense, violent, or explosive. Toxic: Reaction products may be toxic. "
        "Metal chlorates react violently with H2SO4 and other oxidizing acids, evolving explosive and toxic "
        "ClO2 gas."
    ),
    note='Hydrogen peroxide and concentrated sulfuric acid, in this combination, are commonly called "piranha solution."',
    source_url="https://cameochemicals.noaa.gov/reactivity/documentation/RG44-RG2",
    source_detail="Reactivity Documentation: Oxidizing Agents, Strong × Acids, Strong Oxidizing",
)

_add(
    "Azo, Diazo, Azido, Hydrazine, and Azide Compounds",
    "Acids, Strong Oxidizing",
    hazard_types=["explosion", "toxic_gas", "heat"],
    quote=(
        "Flammable: Reaction products may be flammable. Generates gas: Reaction liberates gaseous products and "
        "may cause pressurization. Generates heat: Exothermic reaction at ambient temperatures (releases heat). "
        "Intense or explosive reaction: Reaction may be particularly intense, violent, or explosive. Toxic: "
        "Reaction products may be toxic. NaN3 reacts violently with H2SO4 or HNO3, and the reaction evolves "
        "toxic and flammable HN3 at ambient temperature."
    ),
    source_url="https://cameochemicals.noaa.gov/reactivity/documentation/RG8-RG2",
    source_detail="Reactivity Documentation: Azo, Diazo, Azido, Hydrazine, and Azide Compounds × Acids, Strong Oxidizing",
)

_add(
    "Azo, Diazo, Azido, Hydrazine, and Azide Compounds",
    "Acids, Strong Non-oxidizing",
    hazard_types=["explosion", "toxic_gas", "heat"],
    quote=(
        "Explosive: Reaction products may be explosive or sensitive to shock or friction. Flammable: Reaction "
        "products may be flammable. Generates gas: Reaction liberates gaseous products and may cause "
        "pressurization. Generates heat: Exothermic reaction at ambient temperatures (releases heat). Toxic: "
        "Reaction products may be toxic. Combining azides and acids may yield gaseous HN3, which is toxic and "
        "flammable."
    ),
    source_url="https://cameochemicals.noaa.gov/reactivity/documentation/RG8-RG1",
    source_detail="Reactivity Documentation: Azo, Diazo, Azido, Hydrazine, and Azide Compounds × Acids, Strong Non-oxidizing",
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
