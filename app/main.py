"""FastAPI app. Exposes entity extraction alone (/extract), the full pipeline
(/brief), a streamed version of the full pipeline (/brief/stream) that the web
UI's stage log consumes, and the web UI itself as static files."""

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.extraction import ExtractionError, extract
from app.interaction_matrix import InteractionVerdict, all_verdicts
from app.models import ExtractionResult
from app.pipeline import (
    PipelineResult,
    StreamMessage,
    run_pipeline,
    stream_pipeline_events,
)

app = FastAPI(title="PreCaution")


class ExtractRequest(BaseModel):
    protocol_text: str


class BriefRequest(BaseModel):
    protocol_text: str
    # Per-protocol suppression switch for omission-detection — default on. Lets a
    # specific run be recorded without the layer if a flag ever lands somewhere
    # distracting, with no code change or redeploy needed.
    enable_omissions: bool = True


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/interaction-matrix")
def interaction_matrix_endpoint() -> list[InteractionVerdict]:
    """The real, hand-encoded pairwise interaction table (app/interaction_matrix.py) —
    read-only, for the web UI's interaction-table panel. Same object the interaction
    engine looks verdicts up in; this endpoint adds no logic of its own."""
    return all_verdicts()


@app.post("/extract")
def extract_endpoint(req: ExtractRequest) -> ExtractionResult:
    try:
        return extract(req.protocol_text)
    except ExtractionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/brief")
def brief_endpoint(req: BriefRequest) -> PipelineResult:
    """Thin wrapper over run_pipeline, mirrors /extract. With app.pubchem.ground_chemical's
    grounding_error fix, this no longer crashes on a PubChem outage — it returns a
    PipelineResult whose brief carries Brief.incomplete/incomplete_chemicals instead."""
    try:
        return run_pipeline(req.protocol_text, enable_omissions=req.enable_omissions)
    except ExtractionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _format_sse(msg: StreamMessage) -> str:
    # json.dumps guarantees the data payload never contains a bare newline that
    # could split the frame.
    return f"event: {msg.event}\ndata: {json.dumps(msg.data)}\n\n"


@app.post("/brief/stream")
async def brief_stream_endpoint(req: BriefRequest) -> StreamingResponse:
    """text/event-stream over the full pipeline. Client must use fetch() + a
    ReadableStream reader, not EventSource (EventSource is GET-only)."""

    async def event_source():
        try:
            async for msg in stream_pipeline_events(req.protocol_text, enable_omissions=req.enable_omissions):
                yield _format_sse(msg)
        except asyncio.CancelledError:
            raise  # client disconnected — let it propagate so work stops
        except Exception as exc:  # never let an exception escape mid-stream and corrupt a frame
            yield _format_sse(
                StreamMessage(event="error", data={"stage": "pipeline", "message": str(exc), "recoverable": False})
            )

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


# Mounted last and deliberately: Starlette matches routes in registration order,
# so every explicit path operation above wins over this catch-all. StaticFiles
# with html=True serves app/static/index.html for "/" and other directory paths.
# Path resolved from this file, not the CWD, so it works regardless of where
# uvicorn/pytest is invoked from.
_STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
