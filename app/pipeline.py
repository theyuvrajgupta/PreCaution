"""The orchestrator: wires Stages 1-4 into a single call.

Each stage (extraction, per-chemical grounding, interaction reasoning, brief
composition) has always been code-complete and tested on its own — this
module is the thing that was missing: a single `run_pipeline()` that runs
all four end-to-end and returns everything, not just the final brief. This
closes the Day-2 carried-over item ("one integrated end-to-end run, not
three stages verified separately") and is what scripts/run_pipeline.py and
app/main.py's POST /brief sit on top of.

`stream_pipeline_events()` is a second entry point for the same four stages,
used by POST /brief/stream: an async generator that yields real progress as
each stage genuinely completes, for a UI stage log. It shares the exact same
stage functions as `run_pipeline` (extract, ground_chemical,
find_step_interactions, build_brief) — no logic is duplicated, only
orchestration and progress-event emission are new.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Literal

from pydantic import BaseModel

from app.brief import build_brief
from app.extraction import ExtractionError, extract
from app.interactions import ChemicalPairFinding, find_step_interactions
from app.models import Brief, ChemicalHazardProfile, ExtractionResult
from app.omissions import OmissionDetectionError, OmissionDetectionResult, detect_omissions
from app.pubchem import ground_chemical
from app.segmentation import SegmentationError, needs_segmentation, segment_protocol


class PipelineResult(BaseModel):
    extraction: ExtractionResult
    profiles: dict[str, ChemicalHazardProfile]
    findings: list[ChemicalPairFinding]
    omissions: OmissionDetectionResult
    brief: Brief


def _extraction_input(protocol_text: str) -> str:
    """Restore step structure ahead of extraction for the one input shape that
    needs it: continuous prose (a single unbroken paragraph of >=2 sentences).

    Strictly additive by construction. Already-structured protocols and trivial
    one-liners fail `needs_segmentation` and pass straight through to extraction
    exactly as before. And on any segmentation failure (or a non-procedure that
    segments to nothing) we return the raw text, so this can never do worse than
    feeding the original protocol to extraction — a non-protocol paragraph still
    reaches extraction, finds no chemicals, and lands in the empty state.
    """
    if not needs_segmentation(protocol_text):
        return protocol_text
    try:
        segmented = segment_protocol(protocol_text)
    except SegmentationError:
        return protocol_text
    return segmented.as_protocol_text() if segmented.steps else protocol_text


def run_pipeline(protocol_text: str, enable_omissions: bool = True) -> PipelineResult:
    """Run the full pipeline on a free-text protocol.

    May raise app.extraction.ExtractionError if Stage 1 fails — grounding
    (app.pubchem.ground_chemical) never raises (a transient PubChem failure
    is isolated to that one chemical and recorded as ChemicalHazardProfile.
    grounding_error, not thrown), so no additional error type is introduced
    here. A grounding failure on some chemicals therefore still produces a
    Brief — just one with Brief.incomplete=True.

    `enable_omissions` is the per-protocol suppression switch — default on.
    Omission-detection is a
    separate Claude reasoning step (like extraction) with its own failure mode;
    a failure there degrades to zero flags rather than taking down the whole
    brief, since it's a strictly additive layer, never load-bearing for the
    hazard verdicts the rest of the pipeline produces.
    """
    result = extract(_extraction_input(protocol_text))

    profiles = {name: ground_chemical(name) for name in {c.canonical_name for c in result.chemicals}}

    findings = find_step_interactions(result, profiles)

    if enable_omissions:
        try:
            omissions = detect_omissions(result, profiles)
        except OmissionDetectionError:
            omissions = OmissionDetectionResult(flags=[])
    else:
        omissions = OmissionDetectionResult(flags=[])

    brief = build_brief(result, profiles, findings, omissions.flags)

    return PipelineResult(extraction=result, profiles=profiles, findings=findings, omissions=omissions, brief=brief)


class StreamMessage(BaseModel):
    event: Literal["stage", "chemical", "error", "result"]
    data: dict


async def stream_pipeline_events(
    protocol_text: str, enable_omissions: bool = True
) -> AsyncIterator[StreamMessage]:
    """Run the full pipeline, yielding a StreamMessage as each real event happens.

    Honesty rule: a message is yielded only after its
    underlying work has actually completed — never before, never synthesized.
    The two genuinely blocking calls (extract: one Anthropic call; ground_chemical:
    several httpx calls + a rate-limit sleep) run via asyncio.to_thread so they
    don't block the event loop between yields. Stages 3-4 are pure local
    composition (microseconds) and run inline.

    Grounding is deliberately sequential, not parallelized: app/pubchem.py
    throttles PubChem calls via a module-global timestamp to respect its 5 req/s
    limit; awaiting each to_thread call before starting the next is what keeps
    that promise. asyncio.gather-ing them would race the shared global.

    `result` fires whenever a Brief was produced, including an incomplete one —
    suppressing it on a grounding failure would hide the exact "Incomplete brief"
    state the product is designed to surface. A per-
    chemical grounding failure yields both a `chemical` event and a `recoverable`
    `error` event, then the loop continues.
    """
    yield StreamMessage(event="stage", data={"stage": "extraction", "status": "started"})
    try:
        # Prose segmentation (if the input needs it) and extraction both run inside
        # this one to_thread so the "extraction" stage keeps its existing shape — no
        # new stage event, no change to the stream contract. Segmentation is folded
        # into extraction from the UI's point of view, which is the truth of it: it
        # only reshapes what the extractor reads, it does not reason about hazards.
        result = await asyncio.to_thread(lambda: extract(_extraction_input(protocol_text)))
    except ExtractionError as exc:
        yield StreamMessage(
            event="error", data={"stage": "extraction", "message": str(exc), "recoverable": False}
        )
        return  # unrecoverable — no brief is possible, no result event
    yield StreamMessage(
        event="stage",
        data={
            "stage": "extraction",
            "status": "done",
            "detail": {
                "chemicals": len(result.chemicals),
                "steps": len(result.steps),
                "mixtures": len(result.recognized_mixtures),
                "unresolved": len(result.unresolved_mentions),
            },
        },
    )

    profiles: dict[str, ChemicalHazardProfile] = {}
    for name in dict.fromkeys(c.canonical_name for c in result.chemicals):  # ordered dedup
        profile = await asyncio.to_thread(ground_chemical, name)
        profiles[name] = profile
        # chemical_ids: every Chemical.id sharing this canonical_name — lets the client
        # join this event back to BriefStatement.chemical_ids (which reference "c1" etc,
        # not canonical_name) without waiting for the final `result` event.
        chemical_ids = [c.id for c in result.chemicals if c.canonical_name == name]
        # Extraction captures concentration per Chemical, keyed by canonical_name here like
        # chemical_ids above — take the first mention's value. Shown in the UI, never read by
        # any hazard logic (see README limitations: no concentration-threshold data is grounded).
        concentration = next((c.concentration for c in result.chemicals if c.canonical_name == name), None)
        yield StreamMessage(
            event="chemical",
            data={
                "name": name,
                "cid": profile.cid,
                "found": profile.found,
                "missing_sections": profile.missing_sections,
                "chemical_ids": chemical_ids,
                "concentration": concentration,
                "grounding_error": profile.grounding_error,
                "not_small_molecule": profile.not_small_molecule,
                "fallback_source": profile.fallback_source.source.source_name if profile.fallback_source else None,
            },
        )
        if profile.grounding_error is not None:
            yield StreamMessage(
                event="error",
                data={"stage": "grounding", "message": profile.grounding_error, "recoverable": True},
            )

    findings = find_step_interactions(result, profiles)
    unique_pairs = {frozenset((f.chemical_a_id, f.chemical_b_id)) for f in findings}
    hazard_pairs = {
        frozenset((f.chemical_a_id, f.chemical_b_id)) for f in findings if f.status == "hazard_found"
    }
    yield StreamMessage(
        event="stage",
        data={
            "stage": "interactions",
            "status": "done",
            "detail": {"pairs_checked": len(unique_pairs), "hazards_found": len(hazard_pairs)},
        },
    )

    # Omission-detection: a second, separate Claude call (comprehension, like
    # extraction — never a hazard verdict, never model-generated advice). Genuinely
    # slow like extraction, so it earns its own started/done pair rather than
    # appearing to happen for free between "interactions" and "brief". A failure here
    # degrades to zero flags — never takes down the brief, since this layer is
    # strictly additive (the enable_omissions switch covers deliberate per-protocol
    # suppression; this covers the unplanned-failure case).
    omissions = OmissionDetectionResult(flags=[])
    if enable_omissions:
        yield StreamMessage(event="stage", data={"stage": "omissions", "status": "started"})
        try:
            omissions = await asyncio.to_thread(detect_omissions, result, profiles)
        except OmissionDetectionError as exc:
            yield StreamMessage(
                event="error", data={"stage": "omissions", "message": str(exc), "recoverable": True}
            )
        yield StreamMessage(
            event="stage",
            data={"stage": "omissions", "status": "done", "detail": {"flags": len(omissions.flags)}},
        )

    brief = build_brief(result, profiles, findings, omissions.flags)
    yield StreamMessage(
        event="stage",
        data={"stage": "brief", "status": "done", "detail": {"statements": len(brief.statements)}},
    )

    yield StreamMessage(event="result", data=brief.model_dump(mode="json"))
