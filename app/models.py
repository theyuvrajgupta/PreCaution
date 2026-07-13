"""Pydantic schema for entity extraction output.

This is the contract between extraction (this stage) and everything
downstream: per-chemical PubChem grounding, interaction reasoning, and
honest-omission reporting all consume `ExtractionResult`.
"""

from typing import Literal

from pydantic import BaseModel, Field


class Chemical(BaseModel):
    id: str = Field(description="Stable reference id for this chemical within the extraction, e.g. 'c1'.")
    as_written: str = Field(description="The exact phrase used in the protocol, e.g. '30% hydrogen peroxide'.")
    canonical_name: str = Field(
        description="Normalized chemical name suitable for a PubChem name lookup, e.g. 'hydrogen peroxide'."
    )
    concentration: str | None = Field(default=None, description="e.g. '30%', 'concentrated', '0.02%'.")
    physical_state: str | None = Field(default=None, description="e.g. 'aqueous solution', 'solid', 'gas'.")
    cas_number: str | None = Field(default=None, description="Only if explicitly stated in the protocol text.")
    role: str | None = Field(
        default=None, description="e.g. 'reagent', 'solvent', 'cleaning agent', 'preservative', 'waste'."
    )
    notes: str | None = Field(default=None, description="Any other extraction-relevant context, e.g. source mixture.")
    resolution_reasoning: str = Field(
        description="One or two sentences on HOW this chemical was identified from the protocol text: what phrase "
        "triggered it, any coreference resolved ('the acid' -> this chemical because X), any name normalization "
        "applied (concentration/state stripped from the name), and why. This is the observable evidence of the "
        "extraction reasoning — always fill it in, even for a trivial direct match."
    )


class RecognizedMixture(BaseModel):
    """A named substance in the protocol that is NOT itself a chemical to look up —
    it's the resulting mixture of other extracted chemicals (e.g. 'piranha solution'
    is what you get from combining hydrogen peroxide and sulfuric acid). Capturing
    this distinction explicitly is what proves the tool understood the procedure
    rather than just pattern-matching chemical-sounding words."""

    as_written: str = Field(description="The name as used in the protocol, e.g. 'piranha solution'.")
    constituent_chemical_ids: list[str] = Field(description="Chemical.id values that combine to form this mixture.")
    reasoning: str = Field(
        description="Why this was recognized as a resulting mixture rather than extracted as its own chemical "
        "to ground, e.g. 'piranha solution is the name for the H2O2+H2SO4 mixture prepared in step 1, not a "
        "separate reagent added to the protocol.'"
    )


class StepChemicalRef(BaseModel):
    chemical_id: str = Field(description="References Chemical.id.")
    origin: Literal["added", "carried_over", "residual"] = Field(
        description=(
            "'added': freshly introduced in this step. "
            "'carried_over': already present in the vessel from a prior step (e.g. waste carboy contents). "
            "'residual': trace leftover implied by the protocol but not freshly added or explicitly carried."
        )
    )


class Step(BaseModel):
    number: int = Field(description="1-indexed step number, matching the protocol's own ordering.")
    text: str = Field(description="The step's original text (verbatim or lightly trimmed).")
    operations: list[str] = Field(
        default_factory=list,
        description="Physical operations performed, e.g. ['mix','add_dropwise','heat','rinse','transfer_to_waste'].",
    )
    vessel: str | None = Field(default=None, description="Container/apparatus for this step, e.g. 'acid waste carboy'.")
    conditions: str | None = Field(default=None, description="Free-text conditions: temperature, fume hood, duration.")
    chemicals_present: list[StepChemicalRef] = Field(
        default_factory=list, description="Every chemical physically present in this step's vessel, with origin."
    )


class ExtractionResult(BaseModel):
    chemicals: list[Chemical] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)
    recognized_mixtures: list[RecognizedMixture] = Field(
        default_factory=list,
        description="Named mixtures/solutions recognized as combinations of already-extracted chemicals, "
        "rather than extracted as standalone chemicals themselves (e.g. 'piranha solution').",
    )
    unresolved_mentions: list[str] = Field(
        default_factory=list,
        description="Chemical-looking mentions that could not be confidently resolved to a specific chemical. "
        "Never silently dropped — surfaced explicitly per the honest-omission design rule.",
    )


# ---------------------------------------------------------------------------
# PubChem grounding. A ChemicalHazardProfile is what per-chemical grounding
# produces for one Chemical.canonical_name; interaction reasoning and
# honest-omission reporting both consume it.
# ---------------------------------------------------------------------------


class SourceRef(BaseModel):
    source_name: str = Field(description="e.g. 'PubChem', 'CAMEO Chemicals'.")
    url: str | None = Field(default=None, description="Direct link the user can click to verify the claim.")
    detail: str | None = Field(default=None, description="e.g. a specific SDS citation or CAMEO datasheet name.")


class GHSInfo(BaseModel):
    pictograms: list[str] = Field(default_factory=list, description="Plain-English pictogram labels, e.g. 'Corrosive'.")
    pictogram_urls: list[str] = Field(default_factory=list, description="SVG URLs for the pictograms, in the same order.")
    signal_word: str | None = Field(default=None, description="'Danger' or 'Warning'.")
    hazard_statements: list[str] = Field(default_factory=list, description="Full H-statement text, e.g. 'H314: ...'.")
    precautionary_statements: list[str] = Field(default_factory=list, description="P-statement codes.")
    source: SourceRef


class ReactiveGroupEntry(BaseModel):
    group_name: str = Field(description="e.g. 'Oxidizing Agents, Strong'.")
    source: SourceRef = Field(description="Points back to the specific CAMEO Chemicals datasheet.")


class SafetyExcerpt(BaseModel):
    """One source-attributed chunk of a SafetyNote heading's text — e.g. one
    excerpt from the NIOSH Pocket Guide, one from an ERG Guide. PubChem often
    cites more than one authority under a single heading; kept separate (not
    flattened into one joined string) so the UI can group and label them by
    audience — NIOSH is occupational guidance, ERG is written for hazmat
    first responders at a transport incident."""

    source_label: str = Field(
        description="The citation as PubChem states it, e.g. 'NIOSH Pocket Guide for Sulfuric acid' or "
        "'ERG Guide 140 [Oxidizers]', parsed from an inline 'Excerpt from X:' marker in the source text. "
        "Falls back to the reference's own SourceName (e.g. 'Hazardous Substances Data Bank (HSDB)') when "
        "no such marker is present."
    )
    audience: Literal["niosh", "erg", "other"] = Field(
        description="Coarse bucket derived deterministically from source_label, driving the grouped "
        "rendering: NIOSH open by default, ERG collapsed and labelled, other shown as-is."
    )
    text: str
    source: SourceRef


class SafetyNote(BaseModel):
    """A free-text safety subsection: PPE, First Aid, Disposal, Storage, etc. —
    one or more source-attributed excerpts (see SafetyExcerpt)."""

    heading: str
    excerpts: list[SafetyExcerpt] = Field(default_factory=list)


class FallbackHazardEntry(BaseModel):
    """One entry in the hand-verified, offline fallback hazard table
    (app/fallback_hazards.py) — hazard data for a biological reagent PubChem's
    small-molecule database has no record for, sourced from a real supplier SDS,
    never invented. See that module's docstring for the verification rule."""

    canonical_name: str = Field(description="The name this entry renders under, e.g. 'trypsin'.")
    cas_number: str | None = Field(default=None)
    signal_word: str
    hazard_statements: list[str] = Field(description="Verbatim from the cited SDS, not paraphrased.")
    source: SourceRef


class ChemicalHazardProfile(BaseModel):
    query_name: str = Field(description="The canonical_name that was looked up.")
    found: bool = Field(description="False if PubChem has no record at all for this name.")
    cid: int | None = Field(default=None)
    pubchem_url: str | None = Field(default=None)
    ghs: GHSInfo | None = Field(default=None)
    reactive_groups: list[ReactiveGroupEntry] = Field(default_factory=list)
    safety_notes: list[SafetyNote] = Field(default_factory=list, description="PPE / First Aid / Disposal / Storage.")
    missing_sections: list[str] = Field(
        default_factory=list,
        description="Headings that were queried but PubChem had no data for. Feeds the honest-omission rule — "
        "never inferred as 'safe', always surfaced.",
    )
    grounding_error: str | None = Field(
        default=None,
        description="Set only when grounding could not be COMPLETED due to a transient failure (network outage / "
        "PubChem 5xx after retries exhausted). Means hazard status is UNKNOWN, not confirmed absent — distinct "
        "from found=False with this field None, which is a definitive PubChem 'no record'. A network failure "
        "must never masquerade as 'this chemical doesn't exist'.",
    )
    not_small_molecule: bool = Field(
        default=False,
        description="Set only when found=False AND the query name matches a curated pattern for a known "
        "protein/antibody/serum biologic (see app.pubchem._is_likely_protein). A correct, expected absence — "
        "proteins aren't small molecules, PubChem's small-molecule database was never going to carry a record "
        "for one — not a resolution miss. Deterministic pattern match against a hand-maintained list, never a "
        "lookup, never used to ground any hazard claim.",
    )
    fallback_source: FallbackHazardEntry | None = Field(
        default=None,
        description="Set only when found=False AND app.fallback_hazards has a hand-verified entry "
        "for this name — PubChem genuinely has no record, but a real supplier SDS does. found stays False "
        "(that is still an accurate fact about PubChem specifically); this field carries the alternate, "
        "separately-cited hazard data. Never set when a live PubChem result exists — PubChem is always tried "
        "first and this never overrides it.",
    )


# ---------------------------------------------------------------------------
# The brief / control layer. A Brief is pure composition over
# ChemicalHazardProfile + ChemicalPairFinding — no new grounding, no model
# call. Every BriefStatement must carry a resolvable source_ref; that's the
# whole trust contract for this stage, enforced as a test (tests/test_brief.py),
# not just an assertion.
# ---------------------------------------------------------------------------

BriefKind = Literal[
    "hazard_identity",
    "precautionary",
    "ppe",
    "first_aid",
    "disposal",
    "storage",
    "interaction_hazard",
    "interaction_no_data",
    "reactive_classification",
    "not_small_molecule",
    "step_context",
    "limitation_disclosure",
    "no_data",
    "grounding_incomplete",
    "unresolved_mention",
    "omission_flag",
]


class BriefStatement(BaseModel):
    text: str = Field(
        description="The rendered statement text. For 'interaction_hazard', this is ONLY the CAMEO quote — "
        "the exact text that renders under the source chip. Composed from a source field, never invented."
    )
    kind: BriefKind
    source_ref: str = Field(description="Human-readable provenance. Always non-empty — this is the trust contract.")
    source_url: str | None = Field(default=None, description="Clickable link, when the source has one.")
    lead_in: str | None = Field(
        default=None,
        description="Set only for 'interaction_hazard': the authored, deterministic framing line — which "
        "chemicals, and (per app/interactions.py's origin data) whether they were combined directly or one "
        "arrived by carryover. Renders ABOVE the quote, with no chip — never concatenated into `text`, so the "
        "chipped block can never contain authored prose (see app/interaction_matrix.py).",
    )
    hazard_note: str | None = Field(
        default=None,
        description="Set only for 'interaction_hazard', only when the matrix entry has one: an authored nominal "
        "fact (e.g. a common name like 'piranha solution'), never a hazard claim. Renders as a separate line "
        "below the quote, with no source chip.",
    )
    unverified: bool = Field(
        default=False,
        description="True only for 'step_context': this came from extraction (Claude reading the protocol), "
        "not from an independent grounding source.",
    )
    step_numbers: list[int] = Field(
        default_factory=list,
        description="Every step this statement applies to, in order. Set for step-scoped kinds "
        "(interaction_*, step_context). Empty means not step-scoped. A hazard that persists across "
        "steps (the same pair still co-present) carries every one of those step numbers here, in ONE "
        "statement — never one near-duplicate statement per step.",
    )
    chemical_ids: list[str] = Field(
        default_factory=list, description="Chemical.id value(s) this statement concerns."
    )
    pair: tuple[str, str] | None = Field(
        default=None, description="(chemical_a_id, chemical_b_id) for interaction_* kinds."
    )
    audience: Literal["niosh", "erg", "other"] | None = Field(
        default=None,
        description="Set only for per-chemical safety-note kinds (ppe/first_aid/disposal/storage): which "
        "authority this excerpt is from and who it's written for (see SafetyExcerpt). Drives the grouped "
        "rendering — NIOSH open by default, ERG collapsed and labelled.",
    )
    source_label: str | None = Field(
        default=None,
        description="The specific citation this excerpt came from, e.g. 'NIOSH Pocket Guide for Sulfuric acid' "
        "or 'ERG Guide 140 [Oxidizers]' — distinct from source_ref (the short chip identifier), used as the "
        "group heading text in the UI.",
    )
    signal_word: str | None = Field(
        default=None,
        description="Set only for 'hazard_identity': GHSInfo.signal_word ('Danger' or 'Warning'), carried as a "
        "structured field (not just embedded in text) so the UI can colour the per-chemical row and badge it "
        "without re-parsing prose.",
    )
    pictogram_urls: list[str] = Field(
        default_factory=list,
        description="Set only for 'hazard_identity': GHSInfo.pictogram_urls — real GHS SVGs from PubChem, "
        "parallel to pictogram_labels. Rendered as-is, 28px, never recoloured.",
    )
    pictogram_labels: list[str] = Field(
        default_factory=list,
        description="Parallel to pictogram_urls (GHSInfo.pictograms) — plain-English label per pictogram, "
        "e.g. 'Corrosive', used as each SVG's alt text.",
    )
    gap_status: Literal["no_established_data", "insufficient_reactive_group_data"] | None = Field(
        default=None,
        description="Set only for 'interaction_no_data': mirrors ChemicalPairFinding.status, so the UI can "
        "group 'we checked this pair against our reference set and nothing matched' separately from 'we "
        "could not even determine one or both chemicals' reactive groups to check' — two different epistemic "
        "states that must never render identically. Both already produce differently-worded `text`; this "
        "exposes WHY as a structured field instead of leaving the renderer to sniff the prose.",
    )


class BriefStep(BaseModel):
    number: int
    text: str
    vessel: str | None = Field(default=None, description="From Step.vessel — drives the carryover thread's vessel-change tick.")
    chemicals: list[StepChemicalRef] = Field(
        default_factory=list,
        description="Every chemical present this step, with origin (added/carried_over/residual) — this is "
        "what the carryover thread draws: a token at 'added', a continuing line at 'carried_over'. Same data "
        "as Step.chemicals_present, carried through unchanged.",
    )
    chemical_ids: list[str] = Field(description="From Step.chemicals_present — lets a UI group statements per step.")


class Brief(BaseModel):
    statements: list[BriefStatement] = Field(default_factory=list)
    steps: list[BriefStep] = Field(default_factory=list)
    incomplete: bool = Field(
        default=False,
        description="True if grounding could not be completed for at least one chemical (see "
        "ChemicalHazardProfile.grounding_error). Computed once here so the UI never has to inspect profiles "
        "itself — it stays a thin renderer with no logic of its own.",
    )
    incomplete_chemicals: list[str] = Field(
        default_factory=list, description="canonical_name of every chemical whose grounding_error is set."
    )
