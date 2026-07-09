"""Stage 4: the control layer / brief rendering.

Turns the already-grounded output of Stages 1-3 (ExtractionResult,
per-chemical ChemicalHazardProfile, and ChemicalPairFinding) into a Brief —
a list of attributed BriefStatement objects a UI can render directly.

Deliberately pure and deterministic: no network calls, no Anthropic calls.
This is a design decision, not just a cost-saving default (see
private/Build_Spec.md §4.5 and the Stage 4 plan) — with no generation step
in this module, there is no possibility of an ungrounded claim slipping into
the render layer. Every BriefStatement.source_ref traces back to a specific
field on ChemicalHazardProfile or ChemicalPairFinding; this is checked by
tests/test_brief.py, not just asserted here.

Composition may reformat and structure retrieved text (labels, punctuation,
splitting PubChem's pipe-joined snippets into clear sentences) but must
never add an adjective, quantity, or claim that isn't already present in the
source field. That line is what keeps the "never introduce a claim" rule
(Build_Spec.md §4.3) intact under a rewrite.
"""

from app.interactions import ChemicalPairFinding
from app.models import (
    Brief,
    BriefStatement,
    BriefStep,
    Chemical,
    ChemicalHazardProfile,
    ExtractionResult,
    GHSInfo,
    Step,
)

# Maps a PubChem safety-note heading (see app.pubchem.SAFETY_NOTE_HEADINGS) to
# the BriefKind it renders as.
_SAFETY_NOTE_KIND = {
    "Personal Protective Equipment (PPE)": "ppe",
    "First Aid Measures": "first_aid",
    "Disposal Methods": "disposal",
    "Storage Conditions": "storage",
}

# Human-readable label for a missing_sections heading, used in honest-omission text.
_MISSING_SECTION_LABEL = {
    "GHS Classification": "GHS hazard classification",
    "Personal Protective Equipment (PPE)": "PPE guidance",
    "First Aid Measures": "first aid guidance",
    "Disposal Methods": "disposal guidance",
    "Storage Conditions": "storage guidance",
}

# Verbatim per UI_Design_Spec.md §6.6 — this is exact required copy, not paraphrased.
_GLOVE_DISCLOSURE_TEXT = (
    "The PPE guidance above is what PubChem publishes. Compound-specific glove material "
    "must be confirmed against SDS Section 8 and the manufacturer's resistance data. "
    "Breakthrough can occur faster than published times suggest."
)


def _sentence(text: str) -> str:
    """Ensure text ends with terminal punctuation, without doubling it up."""
    text = text.strip()
    if not text or text.endswith((".", "!", "?")):
        return text
    return text + "."


def _hazard_identity_text(canonical_name: str, ghs: GHSInfo) -> str:
    parts: list[str] = []
    if ghs.signal_word:
        parts.append(f'Signal word "{ghs.signal_word}".')
    if ghs.pictograms:
        parts.append(f"Pictograms: {', '.join(ghs.pictograms)}.")
    if ghs.hazard_statements:
        parts.append(" ".join(_sentence(h) for h in ghs.hazard_statements))
    body = " ".join(parts) if parts else "No further hazard detail on file."
    return f"{canonical_name}: {body}"


def _precautionary_text(canonical_name: str, codes: list[str]) -> str:
    return f"Precautionary statements for {canonical_name} (P-codes): {', '.join(codes)}."


def _safety_note_text(canonical_name: str, kind: str, note_text: str) -> str:
    # PubChem's get_safety_note joins multiple source snippets with " | " — split
    # those back out into clearly delimited sentences rather than rendering the
    # raw pipe-joined blob.
    clauses = [_sentence(part) for part in note_text.split(" | ") if part.strip()]
    body = " ".join(clauses)
    label = {
        "ppe": f"PPE for {canonical_name}",
        "first_aid": f"First aid if exposed to {canonical_name}",
        "disposal": f"Disposal of {canonical_name}",
        "storage": f"Storage of {canonical_name}",
    }[kind]
    return f"{label}: {body}"


def _chemical_statements(chemical: Chemical, profile: ChemicalHazardProfile) -> list[BriefStatement]:
    statements: list[BriefStatement] = []

    if profile.grounding_error is not None:
        # Distinct from "PubChem confirms this doesn't exist" (below): grounding never
        # completed, so hazard status is UNKNOWN, not absent. Never conflate the two.
        statements.append(
            BriefStatement(
                text=(
                    f'Could not complete PubChem grounding for "{chemical.canonical_name}" '
                    f"({profile.grounding_error}). Hazard status is UNKNOWN — this is NOT a "
                    "confirmation the chemical is safe or absent. Retry when PubChem is "
                    "reachable, or consult its SDS directly."
                ),
                kind="grounding_incomplete",
                source_ref=f"PubChem grounding incomplete for '{chemical.canonical_name}'",
                chemical_ids=[chemical.id],
            )
        )
        return statements

    if not profile.found:
        statements.append(
            BriefStatement(
                text=(
                    f'No PubChem record was found for "{chemical.canonical_name}" '
                    f'(as written: "{chemical.as_written}"). Do not assume this chemical is '
                    "safe — consult its SDS directly and verify the name."
                ),
                kind="no_data",
                source_ref="PubChem",
                chemical_ids=[chemical.id],
            )
        )
        return statements

    # Provenance chips are short, identifier-style labels (e.g. "CID 784"), not citation
    # sentences — the UI renders source_ref directly with zero client-side formatting.
    cid_ref = f"CID {profile.cid}"

    if profile.ghs is not None:
        statements.append(
            BriefStatement(
                text=_hazard_identity_text(chemical.canonical_name, profile.ghs),
                kind="hazard_identity",
                source_ref=cid_ref,
                source_url=profile.ghs.source.url,
                chemical_ids=[chemical.id],
            )
        )
        if profile.ghs.precautionary_statements:
            statements.append(
                BriefStatement(
                    text=_precautionary_text(chemical.canonical_name, profile.ghs.precautionary_statements),
                    kind="precautionary",
                    source_ref=cid_ref,
                    source_url=profile.ghs.source.url,
                    chemical_ids=[chemical.id],
                )
            )

    for note in profile.safety_notes:
        kind = _SAFETY_NOTE_KIND.get(note.heading)
        if kind is None:
            continue
        statements.append(
            BriefStatement(
                text=_safety_note_text(chemical.canonical_name, kind, note.text),
                kind=kind,  # type: ignore[arg-type]
                source_ref=cid_ref,
                source_url=note.source.url,
                chemical_ids=[chemical.id],
            )
        )

    for heading in profile.missing_sections:
        label = _MISSING_SECTION_LABEL.get(heading)
        if label is None:
            continue
        statements.append(
            BriefStatement(
                text=(
                    f"No {label} was found in PubChem for {chemical.canonical_name}. "
                    "Do not assume none applies — consult its SDS directly."
                ),
                kind="no_data",
                source_ref=cid_ref,
                source_url=profile.pubchem_url,
                chemical_ids=[chemical.id],
            )
        )

    return statements


def _cameo_react_label(url: str | None) -> str | None:
    """https://cameochemicals.noaa.gov/react/44 -> "NOAA CAMEO react/44" — a short,
    chip-ready label derived mechanically from the URL path, not free-text parsing."""
    if not url:
        return None
    parts = url.rstrip("/").split("/")
    if len(parts) < 2:
        return None
    return f"NOAA CAMEO {parts[-2]}/{parts[-1]}"


def _interaction_statement(findings_for_pair: list[ChemicalPairFinding]) -> BriefStatement:
    """One statement per unique chemical pair, not one per (pair, step) occurrence.

    `status`/`verdict`/`note` depend only on the pair's reactive groups (step-independent —
    see app/interactions.py), so every finding in findings_for_pair shares them identically;
    only which steps the pair co-occurred in varies. Collapsing them here (rather than in the
    UI) is what makes the carryover thread renderable correctly: four or five near-identical
    statements would read as four or five separate hazards when it's really one that persists.
    """
    finding = findings_for_pair[0]  # representative — status/verdict/note identical across the group
    pair = (finding.chemical_a_id, finding.chemical_b_id)
    chemical_ids = [finding.chemical_a_id, finding.chemical_b_id]
    step_numbers = sorted({f.step_number for f in findings_for_pair})

    if finding.status == "hazard_found":
        verdict = finding.verdict
        assert verdict is not None  # status=hazard_found guarantees this (see app/interactions.py)
        source_ref = _cameo_react_label(verdict.source.url) or verdict.source.source_name
        return BriefStatement(
            text=f"Combining {finding.chemical_a_name} and {finding.chemical_b_name} — {verdict.summary}",
            kind="interaction_hazard",
            source_ref=source_ref,
            source_url=verdict.source.url,
            step_numbers=step_numbers,
            chemical_ids=chemical_ids,
            pair=pair,
        )

    # status in {"no_established_data", "insufficient_reactive_group_data"} — reuse the
    # finding's own note verbatim; it's already carefully worded for honest omission.
    return BriefStatement(
        text=finding.note or "",
        kind="interaction_no_data",
        source_ref="PreCaution interaction table",
        step_numbers=step_numbers,
        chemical_ids=chemical_ids,
        pair=pair,
    )


def _step_context_statement(step: Step, all_chemical_ids: list[str]) -> BriefStatement:
    parts = [step.text]
    if step.vessel:
        parts.append(f"Vessel: {step.vessel}.")
    if step.conditions:
        parts.append(f"Conditions: {step.conditions}.")
    return BriefStatement(
        text=" ".join(parts),
        kind="step_context",
        source_ref="Extraction (Stage 1) — from protocol text, not independently grounded",
        unverified=True,
        step_numbers=[step.number],
        chemical_ids=all_chemical_ids,
    )


def build_brief(
    result: ExtractionResult,
    profiles: dict[str, ChemicalHazardProfile],
    findings: list[ChemicalPairFinding],
) -> Brief:
    """Compose the Stage 4 Brief. Pure — no network, no Anthropic, no PubChem.

    `profiles` is keyed by Chemical.canonical_name, matching the convention
    already used by app.interactions.find_step_interactions.
    """
    statements: list[BriefStatement] = []

    for chemical in result.chemicals:
        profile = profiles.get(chemical.canonical_name)
        if profile is None:
            # Not grounded at all (shouldn't happen via app.pipeline.run_pipeline, which
            # grounds every extracted chemical) — treat identically to found=False rather
            # than silently skipping the chemical.
            statements.append(
                BriefStatement(
                    text=(
                        f'"{chemical.canonical_name}" was never sent for grounding. '
                        "Do not assume this chemical is safe — consult its SDS directly."
                    ),
                    kind="no_data",
                    source_ref="PubChem",
                    chemical_ids=[chemical.id],
                )
            )
            continue
        statements.extend(_chemical_statements(chemical, profile))

    for step in result.steps:
        step_chemical_ids = [ref.chemical_id for ref in step.chemicals_present]
        statements.append(_step_context_statement(step, step_chemical_ids))

    # Group findings by unique chemical pair before rendering — one statement per pair,
    # not one per (pair, step) occurrence. Preserves first-seen (i.e. earliest-step) order.
    pair_groups: dict[frozenset[str], list[ChemicalPairFinding]] = {}
    for finding in findings:
        key = frozenset((finding.chemical_a_id, finding.chemical_b_id))
        pair_groups.setdefault(key, []).append(finding)
    for group in pair_groups.values():
        statements.append(_interaction_statement(group))

    # Exactly one, always — independent of whether any PPE data was found. See
    # Build_Spec.md §4.4: the least groundable claim in this whole layer is a
    # compound-specific glove recommendation, so we disclose the gap instead of
    # guessing. This disclosure is a feature, not a footnote.
    statements.append(
        BriefStatement(
            text=_GLOVE_DISCLOSURE_TEXT,
            kind="limitation_disclosure",
            source_ref="OSHA",
        )
    )

    steps = [
        BriefStep(
            number=step.number,
            text=step.text,
            vessel=step.vessel,
            chemicals=list(step.chemicals_present),
            chemical_ids=[ref.chemical_id for ref in step.chemicals_present],
        )
        for step in result.steps
    ]

    # Computed once here, not left for the UI to derive from `profiles` — keeps the
    # "thin renderer, no logic of its own" rule literal rather than almost-true.
    incomplete_chemicals = [name for name, profile in profiles.items() if profile.grounding_error is not None]

    return Brief(
        statements=statements,
        steps=steps,
        incomplete=bool(incomplete_chemicals),
        incomplete_chemicals=incomplete_chemicals,
    )
