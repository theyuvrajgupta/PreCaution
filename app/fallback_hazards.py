"""Phase 2: the second grounding source — a small, hand-verified, offline fallback table
for hazard data on biologicals PubChem's small-molecule database genuinely has no record
for, consulted ONLY when live PubChem grounding (app.pubchem.resolve_cid, including its
Phase 1a normalization/alias fallbacks) has already failed.

Architecture rule, same reasoning as app/interaction_matrix.py's separation from
app/pubchem.py: PubChem stays the PRIMARY, live, unchanged source. This table is a
strictly additive, offline FALLBACK — it never overrides or is consulted before a real
PubChem result, and every entry here is fetched and verified against a real supplier SDS
or authoritative reference at the time it's added, never from model recall. A wrong or
invented entry here would be worse than an honest miss, so this stays small; extend
deliberately, same fetch-and-verify process each time.

Seed entry: trypsin. Verified 2026-07-13 against Southern Biological's SDS (product
MC23.1M, issued 2020-03-12) — https://www.southernbiological.com/content/MSDS/MC23.1M_%20Trypsin%20SDS%202020.pdf
CAS 9002-07-7, GHS signal word "Danger", hazard classification: Respiratory Sensitisation
Category 1, Skin Irritation Category 2, Eye Irritation Category 2A, Specific Target Organ
Toxicity Category 3. Hazard statements quoted verbatim from Section 2 of that SDS.

Papain was investigated and deliberately NOT added: a WebSearch summary claimed a Category
1 respiratory-sensitization classification, but the actual primary-source SDS fetched and
read for this project (Megazyme, product E-PAPN, GHS-AU-DELTA) states "GHS US
classification: Not classified" / "No labeling applicable" for that specific product —
directly contradicting the search summary. Rather than pick a side between two
disagreeing secondary/primary sources, it's left out. This is exactly the "no guessed
hazards" rule in practice, not an oversight — a future session extending this table should
re-verify against one specific, named product's SDS before adding papain, not trust a
search summary.
"""

from app.models import FallbackHazardEntry, SourceRef

_TABLE: dict[str, FallbackHazardEntry] = {}


def _add(
    canonical_name: str,
    cas_number: str | None,
    signal_word: str,
    hazard_statements: list[str],
    source_name: str,
    source_url: str,
    source_detail: str,
    aliases: list[str] | None = None,
) -> None:
    entry = FallbackHazardEntry(
        canonical_name=canonical_name,
        cas_number=cas_number,
        signal_word=signal_word,
        hazard_statements=hazard_statements,
        source=SourceRef(source_name=source_name, url=source_url, detail=source_detail),
    )
    _TABLE[canonical_name.lower()] = entry
    for alias in aliases or []:
        _TABLE[alias.lower()] = entry


_add(
    "trypsin",
    cas_number="9002-07-7",
    signal_word="Danger",
    hazard_statements=[
        "May cause allergy or asthma symptoms or breathing difficulties if inhaled.",
        "Causes skin irritation.",
        "Causes serious eye irritation.",
        "May cause respiratory irritation.",
    ],
    source_name="Southern Biological SDS",
    source_url="https://www.southernbiological.com/content/MSDS/MC23.1M_%20Trypsin%20SDS%202020.pdf",
    source_detail="Product MC23.1M, issued 2020-03-12",
)


def lookup(canonical_name: str) -> FallbackHazardEntry | None:
    """Case-insensitive exact match only — no fuzzy matching, no partial-name matching.
    A false-positive match here would attribute one product's SDS data to a different,
    unverified reagent, which is worse than the honest miss it would otherwise be."""
    return _TABLE.get(canonical_name.strip().lower())
