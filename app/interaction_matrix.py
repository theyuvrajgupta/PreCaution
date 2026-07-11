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
`tests/test_interaction_matrix.py::test_quote_never_contains_authored_prose`
for the check that keeps this from silently regressing.

**Categories vs. example — second structural fix, same day, same root cause
(right quote, wrong chemicals).** RG44-RG2's quote was verbatim and correctly
cited, but its only chemical-specific sentence ("Metal chlorates react
violently with H2SO4...") names a *different member* of the oxidizer group
(metal chlorates) than the one actually in the protocol (hydrogen peroxide).
CAMEO's reactive-group pages document a handful of specific example
reactions per group pair, not one per member — quoting whichever example
happens to be on the page, regardless of which chemical it's about, produces
a citation that is real but misleading. `InteractionVerdict` now separates
`categories` (the group-level hazard-prediction lines — always safe to
quote, since they make no claim about which specific chemical is involved)
from `example` + `example_chemicals` (a documented instance naming specific
chemicals, plus exactly which chemical names must be present in the protocol
for that instance to be relevant). `app/brief.py` renders `example` only
when every name in `example_chemicals` resolves to a chemical in the
protocol being briefed — deterministic, no model call. See
`tests/test_interaction_matrix.py::test_example_never_ships_without_its_required_chemicals`.

**`example_chemicals` is a judgment call made when the entry is written, not
something a rule can derive from the sentence.** Two failed candidate rules,
both falsified by the entries already in this file: "quote the example if it
names a chemical present in the protocol" wrongly passes RG44-RG2's chlorates
sentence (it names H2SO4, which IS present — the sentence is still about a
different oxidizer). "Require every chemical the example names to be
present" wrongly drops RG8-RG2's azide sentence (it names HNO3, which is
NOT present — the sentence is still genuinely about sodium azide + sulfuric
acid). The real distinction — is this sentence about YOUR chemicals, or
about a different member of the same reactive group with yours cast as the
passive partner — is not decidable by string-matching the sentence against
the protocol; it's decided by whoever reads the CAMEO page and writes the
entry. `example_chemicals` records that decision; the render-time check only
enforces it. Extending the matrix by feeding a new page to a model and
trusting whatever it extracts would silently reintroduce this exact bug —
which is why the roadmap's agentic matrix extender proposes entries for a
human to review rather than committing them directly.

Provenance: every quote below was fetched directly from CAMEO's own pairwise
reactivity-documentation pages (not the generic single-group datasheets,
which don't carry pair-specific predictions) on 2026-07-10 during the item-1
audit and its same-day follow-up — re-verifying and superseding the
2026-07-09 seed entries, which had cited the generic /react/<id> URLs. This
is a SEED set covering only the pairs the locked demo protocol needs. Extend
deliberately: fetch the actual *pairwise* datasheet for any new pair before
adding it, never from memory, and never let authored prose share a string
with a quote.
"""

from pydantic import BaseModel, Field

from app.models import SourceRef


class InteractionVerdict(BaseModel):
    group_a: str
    group_b: str
    hazard_types: list[str]
    categories: str = Field(
        description="Verbatim group-level hazard-category predictions from the CAMEO pairwise "
        "reactivity-documentation page (e.g. 'Explosive: ...'). Always safe to quote regardless of which "
        "specific chemicals are in the protocol — these are class-level, not instance-level, claims."
    )
    example: str | None = Field(
        default=None,
        description="A verbatim documented-example sentence from the same page, naming specific chemicals. "
        "Only ever rendered when every name in example_chemicals is present in the protocol being briefed "
        "(app/brief.py) — otherwise it would cite a real CAMEO sentence as if it were about chemicals that "
        "were never used.",
    )
    example_chemicals: list[str] | None = Field(
        default=None,
        description="canonical_name-style chemical names that must ALL be present in the protocol for "
        "`example` to be included. Curated, not a mechanical extraction of every noun in the sentence: an "
        "'X reacts with Y or Z' example only requires the alternative actually present, and a named reaction "
        "PRODUCT is never a requirement (it's an output, not an input to check for). None/empty if `example` "
        "is None.",
    )
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
    categories: str,
    source_url: str,
    source_detail: str,
    example: str | None = None,
    example_chemicals: list[str] | None = None,
    note: str | None = None,
) -> None:
    verdict = InteractionVerdict(
        group_a=group_a,
        group_b=group_b,
        hazard_types=hazard_types,
        categories=categories,
        example=example,
        example_chemicals=example_chemicals,
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
    categories=(
        "Explosive: Reaction products may be explosive or sensitive to shock or friction. Generates gas: "
        "Reaction liberates gaseous products and may cause pressurization. Intense or explosive reaction: "
        "Reaction may be particularly intense, violent, or explosive. Toxic: Reaction products may be toxic."
    ),
    # Re-checked 2026-07-10 (item-1 follow-up): RG44-RG2's only documented example names metal
    # chlorates, not hydrogen peroxide — no example on this page involves hydrogen peroxide
    # specifically. Leaving example unset rather than quoting a different oxidizer's example under
    # this pair's chip; add a hydrogen-peroxide-specific example here only if CAMEO adds one.
    note='Hydrogen peroxide and concentrated sulfuric acid, in this combination, are commonly called "piranha solution."',
    source_url="https://cameochemicals.noaa.gov/reactivity/documentation/RG44-RG2",
    source_detail="Reactivity Documentation: Oxidizing Agents, Strong × Acids, Strong Oxidizing",
)

_add(
    "Azo, Diazo, Azido, Hydrazine, and Azide Compounds",
    "Acids, Strong Oxidizing",
    hazard_types=["explosion", "toxic_gas", "heat"],
    categories=(
        "Flammable: Reaction products may be flammable. Generates gas: Reaction liberates gaseous products and "
        "may cause pressurization. Generates heat: Exothermic reaction at ambient temperatures (releases heat). "
        "Intense or explosive reaction: Reaction may be particularly intense, violent, or explosive. Toxic: "
        "Reaction products may be toxic."
    ),
    example=(
        "NaN3 reacts violently with H2SO4 or HNO3, and the reaction evolves toxic and flammable HN3 at ambient "
        "temperature."
    ),
    # Names two alternative oxidizing acids ("H2SO4 or HNO3"); require only the one this protocol
    # actually has (sulfuric acid) plus the azide itself — nitric acid is an alternative, not a
    # co-requirement, and HN3 is the reaction PRODUCT, not an input chemical to check for.
    example_chemicals=["sodium azide", "sulfuric acid"],
    source_url="https://cameochemicals.noaa.gov/reactivity/documentation/RG8-RG2",
    source_detail="Reactivity Documentation: Azo, Diazo, Azido, Hydrazine, and Azide Compounds × Acids, Strong Oxidizing",
)

_add(
    "Salts, Basic",
    "Acids, Strong Oxidizing",
    hazard_types=["corrosive", "toxic_gas", "heat"],
    categories=(
        "Corrosive: Reaction products may be corrosive. Generates gas: Reaction liberates gaseous products and "
        "may cause pressurization. Generates heat: Exothermic reaction at ambient temperatures (releases heat). "
        "Toxic: Reaction products may be toxic."
    ),
    # 2026-07-11: sodium hypochlorite (bleach) meeting sulfuric acid is a well-documented lab
    # hazard (evolves chlorine gas) — but that specific sentence lives only on sodium
    # hypochlorite's own per-chemical CAMEO datasheet (cameochemicals.noaa.gov/chemical/4503,
    # "Can react with sulfuric acid to produce heat and chlorine gas"), not on this PAIRWISE
    # reactivity-documentation page, which is the only source this table quotes from. RG39-RG2's
    # own documented example names sodium carbonate, not sodium hypochlorite — same "right quote,
    # wrong chemical" trap as RG44-RG2's chlorate example above — so it's left unset rather than
    # quoted under this pair's chip. Sodium hypochlorite is classified "Salts, Basic" (and also
    # "Oxidizing Agents, Strong", which is why it already collides with the piranha-solution pair
    # above and cannot get a second, distinct entry there); this pair (Salts, Basic × Acids,
    # Strong Oxidizing) is the one that actually fires for sulfuric acid + sodium hypochlorite
    # without touching the existing piranha entry.
    source_url="https://cameochemicals.noaa.gov/reactivity/documentation/RG39-RG2",
    source_detail="Reactivity Documentation: Salts, Basic × Acids, Strong Oxidizing",
)

_add(
    "Azo, Diazo, Azido, Hydrazine, and Azide Compounds",
    "Acids, Strong Non-oxidizing",
    hazard_types=["explosion", "toxic_gas", "heat"],
    categories=(
        "Explosive: Reaction products may be explosive or sensitive to shock or friction. Flammable: Reaction "
        "products may be flammable. Generates gas: Reaction liberates gaseous products and may cause "
        "pressurization. Generates heat: Exothermic reaction at ambient temperatures (releases heat). Toxic: "
        "Reaction products may be toxic. Combining azides and acids may yield gaseous HN3, which is toxic and "
        "flammable."
    ),
    # Re-checked 2026-07-10: this page's documented examples name only generic classes ("azides",
    # "inorganic acids", "organic azides"), never a specific compound — nothing here rises to a
    # chemical-specific example under this rule, so the class-level sentence stays in categories
    # rather than being held out as `example` (which would have no example_chemicals to check).
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


def all_verdicts() -> list[InteractionVerdict]:
    """Every verdict in the table, for the in-app interaction-table panel (app/main.py's
    GET /interaction-matrix). Read-only accessor over the same _TABLE lookup_verdict
    reads — the panel must render this module's real data, never a copy of it."""
    return list(_TABLE.values())
