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
from typing import AsyncIterator, Literal

from pydantic import BaseModel

from app.brief import build_brief
from app.extraction import ExtractionError, extract
from app.interactions import ChemicalPairFinding, find_step_interactions
from app.models import Brief, ChemicalHazardProfile, ExtractionResult
from app.pubchem import ground_chemical


class PipelineResult(BaseModel):
    extraction: ExtractionResult
    profiles: dict[str, ChemicalHazardProfile]
    findings: list[ChemicalPairFinding]
    brief: Brief


def run_pipeline(protocol_text: str) -> PipelineResult:
    """Run the full pipeline on a free-text protocol.

    May raise app.extraction.ExtractionError if Stage 1 fails — grounding
    (app.pubchem.ground_chemical) never raises (a transient PubChem failure
    is isolated to that one chemical and recorded as ChemicalHazardProfile.
    grounding_error, not thrown), so no additional error type is introduced
    here. A grounding failure on some chemicals therefore still produces a
    Brief — just one with Brief.incomplete=True.
    """
    result = extract(protocol_text)

    profiles = {name: ground_chemical(name) for name in {c.canonical_name for c in result.chemicals}}

    findings = find_step_interactions(result, profiles)
    brief = build_brief(result, profiles, findings)

    return PipelineResult(extraction=result, profiles=profiles, findings=findings, brief=brief)


class StreamMessage(BaseModel):
    event: Literal["stage", "chemical", "error", "result"]
    data: dict


async def stream_pipeline_events(protocol_text: str) -> AsyncIterator[StreamMessage]:
    """Run the full pipeline, yielding a StreamMessage as each real event happens.

    Honesty rule (UI_Design_Spec.md §14.2): a message is yielded only after its
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
    state (UI_Design_Spec.md §16.1) the product is designed to surface. A per-
    chemical grounding failure yields both a `chemical` event and a `recoverable`
    `error` event, then the loop continues.
    """
    yield StreamMessage(event="stage", data={"stage": "extraction", "status": "started"})
    try:
        result = await asyncio.to_thread(extract, protocol_text)
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
        yield StreamMessage(
            event="chemical",
            data={
                "name": name,
                "cid": profile.cid,
                "found": profile.found,
                "missing_sections": profile.missing_sections,
                "chemical_ids": chemical_ids,
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

    brief = build_brief(result, profiles, findings)
    yield StreamMessage(
        event="stage",
        data={"stage": "brief", "status": "done", "detail": {"statements": len(brief.statements)}},
    )

    yield StreamMessage(event="result", data=brief.model_dump(mode="json"))
