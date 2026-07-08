"""FastAPI app. For now, exposes entity extraction only — this is the Day-3 seam
the web UI will build on."""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.extraction import ExtractionError, extract
from app.models import ExtractionResult

app = FastAPI(title="PreCaution")


class ExtractRequest(BaseModel):
    protocol_text: str


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/extract", response_model=ExtractionResult)
def extract_endpoint(req: ExtractRequest) -> ExtractionResult:
    try:
        return extract(req.protocol_text)
    except ExtractionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
