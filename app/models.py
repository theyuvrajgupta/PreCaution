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


class SafetyNote(BaseModel):
    """A free-text safety subsection: PPE, First Aid, Disposal, Storage, etc."""

    heading: str
    text: str
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
