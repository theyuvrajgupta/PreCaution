"""Pre-segmentation: continuous prose -> ordered step strings, run *before*
extraction.

Why this stage exists: extraction reliably finds chemicals and hazards the
moment a protocol has step structure, but a protocol pasted as one unbroken
paragraph of prose was landing in the empty state — chemicals never got
extracted at all. The single variable was line/step structure (see
Build_Spec: Prose Segmentation, Run A vs Run B). This stage restores that
structure ahead of extraction so the highest-value input (a free-text methods
section or bench note) works like a numbered one.

Deliberate boundaries, matching the project's stage-separation rule:
- This is a SEPARATE Claude pass with its own prompt, never folded into the
  extraction prompt. A bad segmentation then shows up as visibly wrong step
  boundaries (easy to inspect), not as silent drift in hazard detection.
- It only re-punctuates existing text into steps. It never extracts chemicals,
  evaluates safety, or adds/removes/completes content — that is extraction's
  and the hazard stages' job, downstream and unchanged.

`needs_segmentation()` is the guard that keeps this additive: it fires only for
the one shape that actually fails — a single unbroken paragraph of two or more
sentences. Already-structured input (multiple lines) and trivial one-liners
skip this pass entirely and hit extraction exactly as they did before, so the
seven known-good protocols are untouched by this change.
"""

import re
from pathlib import Path

from pydantic import BaseModel, Field

from app.claude_client import get_client
from app.config import get_settings

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "segmentation_system.md"
_TOOL_NAME = "emit_segmentation"

# A line that already reads as its own step: numbered ("1.", "2)"), lettered
# ("a.", "b)"), or bulleted ("-", "*", "•"). Used only to recognize
# already-structured input so we can leave it completely alone.
_STEP_MARKER = re.compile(r"^\s*(?:\d+[.)]|[a-zA-Z][.)]|[-*•])\s+")
# Sentence terminator followed by whitespace or end-of-text — a coarse but
# sufficient count of "how many sentences is this paragraph".
_SENTENCE_END = re.compile(r"[.!?](?:\s|$)")


class SegmentationResult(BaseModel):
    steps: list[str] = Field(
        default_factory=list,
        description="The protocol's discrete procedural steps, in order, using the "
        "author's own wording. Empty when the text is not a bench procedure.",
    )

    def as_protocol_text(self) -> str:
        """Serialize back to numbered-step text — the shape extraction reliably reads."""
        return "\n".join(f"{i}. {step}" for i, step in enumerate(self.steps, start=1))


class SegmentationError(RuntimeError):
    """Raised when Claude does not return a valid, schema-conforming result."""


def needs_segmentation(protocol_text: str) -> bool:
    """True only for a single unbroken paragraph of >=2 sentences.

    This is the exact shape that fails today (continuous prose). Anything with
    real line structure, or a single-sentence one-liner, is left for extraction
    to handle unchanged — which is what keeps this pass strictly additive rather
    than a behavior change for inputs that already work.
    """
    non_empty_lines = [ln for ln in protocol_text.splitlines() if ln.strip()]
    if len(non_empty_lines) != 1:
        return False  # multiple lines: already has structure (or is empty)
    if _STEP_MARKER.match(non_empty_lines[0]):
        return False  # a single already-marked step line — not prose to segment
    return len(_SENTENCE_END.findall(non_empty_lines[0])) >= 2


def _load_system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _build_tool_schema() -> dict:
    schema = SegmentationResult.model_json_schema()
    return {
        "name": _TOOL_NAME,
        "description": "Emit the protocol re-expressed as an ordered list of discrete steps.",
        "input_schema": schema,
    }


def segment_protocol(protocol_text: str) -> SegmentationResult:
    """Re-express a prose protocol as ordered step strings.

    Raises SegmentationError if the model doesn't call the tool or returns
    output that fails schema validation — the pipeline treats that as a reason
    to fall back to the raw text, never to fail the run.
    """
    settings = get_settings()
    client = get_client()
    tool = _build_tool_schema()

    response = client.messages.create(
        model=settings.precaution_model,
        max_tokens=4096,
        system=_load_system_prompt(),
        tools=[tool],
        tool_choice={"type": "tool", "name": _TOOL_NAME},
        messages=[{"role": "user", "content": protocol_text}],
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == _TOOL_NAME:
            try:
                return SegmentationResult.model_validate(block.input)
            except Exception as exc:
                raise SegmentationError(f"Segmentation output failed schema validation: {exc}") from exc

    raise SegmentationError(
        f"Model did not call the '{_TOOL_NAME}' tool. Stop reason: {response.stop_reason!r}"
    )
