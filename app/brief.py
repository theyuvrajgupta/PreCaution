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

from app.interaction_matrix import InteractionVerdict
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
from app.precautionary_codes import resolve_precautionary_code

# Maps a PubChem safety-note heading (see app.pubchem.SAFETY_NOTE_HEADINGS) to
# the BriefKind it renders as.
_SAFETY_NOTE_KIND = {
    "Personal Protective Equipment (PPE)": "ppe",
    "First Aid Measures": "first_aid",
    "Disposal Methods": "disposal",
    "Storage Conditions": "storage",
}

# Short noun form of a missing_sections heading, for the AGGREGATED per-chemical gap
# statement (item 3, 2026-07-10: one card per chemical, not one per missing heading —
# five missing headings used to mean five near-identical "no data" cards per chemical).
_MISSING_SECTION_SHORT_LABEL = {
    "GHS Classification": "GHS classification",
    "Personal Protective Equipment (PPE)": "PPE",
    "First Aid Measures": "first aid",
    "Disposal Methods": "disposal",
    "Storage Conditions": "storage",
}


def _join_with_or(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} or {items[-1]}"

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
    # A bare P-code ("P210") is not guidance to a newcomer — resolve it against the
    # static GHS table where we have it; an unresolved code falls back to the bare
    # code itself rather than inventing text (honest omission, see
    # app/precautionary_codes.py).
    parts = []
    for code in codes:
        resolved = resolve_precautionary_code(code)
        parts.append(f"{code} — {resolved}" if resolved else code)
    return f"Precautionary statements for {canonical_name}: " + " ".join(parts)


def _safety_note_text(canonical_name: str, kind: str, excerpt_text: str) -> str:
    body = _sentence(excerpt_text)
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
                signal_word=profile.ghs.signal_word,
                pictogram_urls=profile.ghs.pictogram_urls,
                pictogram_labels=profile.ghs.pictograms,
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
        # One statement per excerpt, not per heading — a heading can cite more than
        # one authority (e.g. both NIOSH and ERG under PPE), and collapsing them into
        # one blob is exactly the "per-chemical wall" problem §20 exists to fix.
        for excerpt in note.excerpts:
            statements.append(
                BriefStatement(
                    text=_safety_note_text(chemical.canonical_name, kind, excerpt.text),
                    kind=kind,  # type: ignore[arg-type]
                    source_ref=cid_ref,
                    source_url=excerpt.source.url,
                    chemical_ids=[chemical.id],
                    audience=excerpt.audience,
                    source_label=excerpt.source_label,
                )
            )

    # One aggregated gap card per chemical, not one per missing heading — surfacing the
    # gap is the honest-omission rule; flooding the brief with five near-identical cards
    # per chemical (water, nitrogen, and PBS each had all five) is not (item 3).
    short_labels = [_MISSING_SECTION_SHORT_LABEL[h] for h in profile.missing_sections if h in _MISSING_SECTION_SHORT_LABEL]
    if short_labels:
        statements.append(
            BriefStatement(
                text=(
                    f"{chemical.canonical_name} — no {_join_with_or(short_labels)} data in PubChem. "
                    "This does not mean it is hazard-free — consult its SDS directly."
                ),
                kind="no_data",
                source_ref=cid_ref,
                source_url=profile.pubchem_url,
                chemical_ids=[chemical.id],
            )
        )

    return statements


def _cameo_react_label(url: str | None) -> str | None:
    """https://cameochemicals.noaa.gov/reactivity/documentation/RG44-RG2 -> "NOAA CAMEO
    documentation/RG44-RG2" — a short, chip-ready label derived mechanically from the URL
    path, not free-text parsing."""
    if not url:
        return None
    parts = url.rstrip("/").split("/")
    if len(parts) < 2:
        return None
    return f"NOAA CAMEO {parts[-2]}/{parts[-1]}"


def _origin_phrase(name: str, origin: str, added_step: int | None, vessel_entry_step: int | None, vessel: str | None) -> str:
    if origin == "added":
        return f"{name} (added in step {added_step})" if added_step else f"{name} (added)"
    # A carried-over/residual chemical can have TWO distinct facts: when it was first
    # added to the protocol, and when it entered the vessel this finding is actually
    # about — these can differ (added in a beaker in step 1, poured into a waste
    # carboy in step 4), and naming only the origin step implies false continuity in
    # one container (2026-07-10 item-3 follow-up). Name both when they differ and
    # we have real vessel data for it; otherwise fall back to whichever single step
    # is known.
    if added_step and vessel_entry_step and vessel_entry_step != added_step:
        where = f"entered the {vessel}" if vessel else "entered this vessel"
        return f"{name} (added in step {added_step}, {where} in step {vessel_entry_step})"
    step = added_step or vessel_entry_step
    verb = "carried over" if origin == "carried_over" else "residual"
    return f"{name} ({verb} from step {step})" if step else f"{name} ({verb})"


def _lead_in(finding: ChemicalPairFinding, step_numbers: list[int]) -> str:
    """The authored, deterministic framing line — never concatenated with the CAMEO
    quote (see app/interaction_matrix.py's 2026-07-10 audit note). "Combined" only for
    the step both chemicals were freshly added; if the pair then stays co-present
    across later steps, say so explicitly rather than implying the hazard was a single
    instant (2026-07-10 item-2 follow-up). Otherwise names each chemical's origin,
    since a carryover meeting is a materially different claim from a same-step mix
    (§3.3's trust-critical seam) and deserves to be said in the card, not just shown in
    the thread graphic."""
    if finding.origin_a == "added" and finding.origin_b == "added":
        if len(step_numbers) > 1:
            return (
                f"{finding.chemical_a_name} and {finding.chemical_b_name}, combined in step "
                f"{finding.step_number}, co-present through step {step_numbers[-1]}."
            )
        return f"Combining {finding.chemical_a_name} and {finding.chemical_b_name}."
    a = _origin_phrase(finding.chemical_a_name, finding.origin_a, finding.added_step_a, finding.vessel_entry_step_a, finding.vessel)
    b = _origin_phrase(finding.chemical_b_name, finding.origin_b, finding.added_step_b, finding.vessel_entry_step_b, finding.vessel)
    return f"{a} meets {b}."


def _render_quote(verdict: InteractionVerdict, protocol_chemical_names: set[str]) -> str:
    """The chipped hazard-card body: always the group-level `categories`, plus CAMEO's
    documented `example` ONLY when every chemical it names is actually present in this
    protocol (2026-07-10 item-1 follow-up). A real, correctly-cited CAMEO example can
    still mislead if it happens to document a *different* member of the same reactive
    group than the one in front of the reader — e.g. a metal-chlorate example under a
    hydrogen-peroxide finding. Deterministic, no model call."""
    if verdict.example and verdict.example_chemicals and all(
        name in protocol_chemical_names for name in verdict.example_chemicals
    ):
        return f"{verdict.categories} {verdict.example}"
    return verdict.categories


def _interaction_statement(
    findings_for_pair: list[ChemicalPairFinding], protocol_chemical_names: set[str]
) -> BriefStatement:
    """One statement per unique chemical pair, not one per (pair, step) occurrence.

    `status`/`verdict`/`note` depend only on the pair's reactive groups (step-independent —
    see app/interactions.py), so every finding in findings_for_pair shares them identically;
    only which steps the pair co-occurred in varies. Collapsing them here (rather than in the
    UI) is what makes the carryover thread renderable correctly: four or five near-identical
    statements would read as four or five separate hazards when it's really one that persists.

    The representative finding (findings_for_pair[0]) is the earliest step the pair was
    co-present — find_step_interactions appends findings in step order — so its origin_a/
    origin_b describe the pair's actual onset, which is what _lead_in and the "combined vs.
    co-present" distinction need.
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
            text=_render_quote(verdict, protocol_chemical_names),  # ONLY CAMEO text — this is what the chip attaches to
            kind="interaction_hazard",
            source_ref=source_ref,
            source_url=verdict.source.url,
            lead_in=_lead_in(finding, step_numbers),
            hazard_note=verdict.note,
            step_numbers=step_numbers,
            chemical_ids=chemical_ids,
            pair=pair,
        )

    # status in {"no_established_data", "insufficient_reactive_group_data"} — reuse the
    # finding's own note verbatim; it's already carefully worded for honest omission.
    # A real CAMEO classification (e.g. "Not Chemically Reactive") gets cited to its own
    # source rather than the generic interaction-table placeholder — it's a lookup, not
    # an absence (§21).
    if finding.classification_source is not None:
        source_ref = finding.classification_source.source_name
        source_url = finding.classification_source.url
    else:
        source_ref = "PreCaution interaction table"
        source_url = None
    return BriefStatement(
        text=finding.note or "",
        kind="interaction_no_data",
        source_ref=source_ref,
        source_url=source_url,
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


def _unresolved_mention_statement(mention: str) -> BriefStatement:
    """§16.2/§D: a chemical-looking phrase Stage 1 couldn't confidently resolve.
    Never silently dropped — same honest-omission rule as a missing grounding
    heading, just at the extraction layer instead of PubChem's."""
    return BriefStatement(
        text=(
            f'"{mention}" could not be confidently resolved to a specific chemical. '
            "Do not assume it is unimportant — check the original protocol text and consult its SDS if unsure."
        ),
        kind="unresolved_mention",
        source_ref="Extraction (Stage 1) — Claude's read of the protocol text, not independently checked",
        unverified=True,
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

    for mention in result.unresolved_mentions:
        statements.append(_unresolved_mention_statement(mention))

    for step in result.steps:
        step_chemical_ids = [ref.chemical_id for ref in step.chemicals_present]
        statements.append(_step_context_statement(step, step_chemical_ids))

    # Group findings by unique chemical pair before rendering — one statement per pair,
    # not one per (pair, step) occurrence. Preserves first-seen (i.e. earliest-step) order.
    protocol_chemical_names = {c.canonical_name for c in result.chemicals}
    pair_groups: dict[frozenset[str], list[ChemicalPairFinding]] = {}
    for finding in findings:
        key = frozenset((finding.chemical_a_id, finding.chemical_b_id))
        pair_groups.setdefault(key, []).append(finding)
    for group in pair_groups.values():
        statements.append(_interaction_statement(group, protocol_chemical_names))

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
