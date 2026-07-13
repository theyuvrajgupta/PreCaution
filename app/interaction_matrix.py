"""Pairwise reactive-group danger verdicts — the second grounding layer.

Architecture boundary, kept deliberately clean:
  - Per-chemical reactive-group ASSIGNMENT comes live from PubChem
    (app/pubchem.py), which itself passes through NOAA CAMEO Chemicals data.
  - The pairwise DANGER VERDICT for a pair of reactive groups comes from THIS
    local, offline, hand-encoded table — never from free-form model output.

These stay separate modules with a narrow interface (reactive-group names in,
a verdict or None out). That separation is what lets the tool honestly claim
"this verdict is looked up, not generated"; tangle the two and the claim stops
being defensible.

Quote vs. note — read before adding or editing an entry. Only verbatim
datasheet text may render under a source chip, so `InteractionVerdict` splits
its text to keep authored prose from ever sharing a string with a quote:
  - `categories`: verbatim group-level hazard-category predictions (e.g.
    "Explosive: ..."). Class-level claims, so always safe to quote.
  - `example` / `example_chemicals`: a verbatim documented-example sentence
    naming specific chemicals, plus the chemical names that must ALL be present
    in the protocol for it to render (enforced in app/brief.py). This guards
    the subtle failure of quoting a real sentence about a *different* member of
    the same reactive group — e.g. an oxidizer page whose only example names
    metal chlorates, not the hydrogen peroxide actually in hand.
  - `note`: authored, nominal facts only (e.g. a common name), rendered on a
    separate unchipped line — never a hazard claim.

`example_chemicals` is a judgment made when the entry is written, not something
a rule derives from the sentence: "X reacts with Y or Z" requires only the
alternative actually present, and a named reaction PRODUCT is an output, not an
input to check for. Neither "name a present chemical" nor "require every named
chemical" gets this right — whether a sentence is about YOUR chemicals is a
human read of the CAMEO page. That is why extending the matrix must never mean
trusting whatever a model extracts from a new page.

This is a SEED set covering only the pairs the locked demo protocol needs.
Extend deliberately: fetch the actual pairwise datasheet for a new pair before
adding it, never from memory.
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


# --- Seed entries: each quote fetched from CAMEO's pairwise reactivity-
# documentation pages (not the generic single-group datasheets, which carry
# no pair-specific predictions) ---

_add(
    "Oxidizing Agents, Strong",
    "Acids, Strong Oxidizing",
    hazard_types=["explosion", "toxic_gas"],
    categories=(
        "Explosive: Reaction products may be explosive or sensitive to shock or friction. Generates gas: "
        "Reaction liberates gaseous products and may cause pressurization. Intense or explosive reaction: "
        "Reaction may be particularly intense, violent, or explosive. Toxic: Reaction products may be toxic."
    ),
    # This pair's only documented example names metal chlorates, not the hydrogen peroxide actually
    # in the protocol — so `example` stays unset rather than quoting a different oxidizer's reaction
    # under this pair's chip. Add a hydrogen-peroxide-specific example here only if CAMEO adds one.
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
    # Sodium hypochlorite + sulfuric acid (evolves chlorine gas) is well documented, but that
    # specific sentence lives only on hypochlorite's per-chemical CAMEO datasheet, not on this
    # pairwise page — the only source this table quotes — and this page's own example names sodium
    # carbonate, so `example` stays unset (the same "right quote, wrong chemical" trap as above).
    # Hypochlorite is classified both "Salts, Basic" and "Oxidizing Agents, Strong"; this
    # (Salts, Basic × Acids, Strong Oxidizing) pair fires for it without colliding with the
    # existing piranha entry above.
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
    # This page's examples name only generic classes ("azides", "inorganic acids"), never a
    # specific compound — so the class-level sentence stays in `categories` rather than being held
    # out as an `example` that would have no example_chemicals to gate on.
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
    """Every (group_a, group_b) pair currently in the table — used by the tests to iterate it."""
    return [(v.group_a, v.group_b) for v in _TABLE.values()]


def all_verdicts() -> list[InteractionVerdict]:
    """Every verdict in the table, for the in-app interaction-table panel (app/main.py's
    GET /interaction-matrix). Read-only accessor over the same _TABLE lookup_verdict
    reads — the panel must render this module's real data, never a copy of it."""
    return list(_TABLE.values())
