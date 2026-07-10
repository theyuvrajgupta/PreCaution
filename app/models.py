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
# PubChem grounding (task #5). A ChemicalHazardProfile is what per-chemical
# grounding produces for one Chemical.canonical_name; interaction reasoning
# (#6) and honest-omission reporting (#7) both consume it.
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
    first responders at a transport incident (UI_Design_Spec.md §20)."""

    source_label: str = Field(
        description="The citation as PubChem states it, e.g. 'NIOSH Pocket Guide for Sulfuric acid' or "
        "'ERG Guide 140 [Oxidizers]', parsed from an inline 'Excerpt from X:' marker in the source text. "
        "Falls back to the reference's own SourceName (e.g. 'Hazardous Substances Data Bank (HSDB)') when "
        "no such marker is present."
    )
    audience: Literal["niosh", "erg", "other"] = Field(
        description="Coarse bucket derived deterministically from source_label, driving §20's grouped "
        "rendering: NIOSH open by default, ERG collapsed and labelled, other shown as-is."
    )
    text: str
    source: SourceRef


class SafetyNote(BaseModel):
    """A free-text safety subsection: PPE, First Aid, Disposal, Storage, etc. —
    one or more source-attributed excerpts (see SafetyExcerpt)."""

    heading: str
    excerpts: list[SafetyExcerpt] = Field(default_factory=list)


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


# ---------------------------------------------------------------------------
# Stage 4: the control layer / brief rendering (see private/Build_Spec.md §4).
# A Brief is pure composition over ChemicalHazardProfile + ChemicalPairFinding
# — no new grounding, no model call. Every BriefStatement must carry a
# resolvable source_ref; that's the whole trust contract for this stage,
# enforced as a test (tests/test_brief.py), not just an assertion.
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
    "step_context",
    "limitation_disclosure",
    "no_data",
    "grounding_incomplete",
]


class BriefStatement(BaseModel):
    text: str = Field(description="The rendered statement text. Composed from a source field, never invented.")
    kind: BriefKind
    source_ref: str = Field(description="Human-readable provenance. Always non-empty — this is the trust contract.")
    source_url: str | None = Field(default=None, description="Clickable link, when the source has one.")
    unverified: bool = Field(
        default=False,
        description="True only for 'step_context': this came from extraction (Claude reading the protocol), "
        "not from an independent grounding source. See Build_Spec.md §3.3.",
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


class BriefStep(BaseModel):
    number: int
    text: str
    vessel: str | None = Field(default=None, description="From Step.vessel — drives the carryover thread's vessel-change tick (UI_Design_Spec.md §6.1).")
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
