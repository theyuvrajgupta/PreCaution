"""Entity extraction: free-text protocol -> ExtractionResult.

Uses Anthropic tool-use with a forced tool_choice so the model must return
a single, schema-valid structured call rather than free-form prose. The
tool's input_schema is generated directly from the ExtractionResult
pydantic model, so the schema has one source of truth (app/models.py).
"""

from pathlib import Path

from app.claude_client import get_client
from app.config import get_settings
from app.models import ExtractionResult

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "extraction_system.md"
_TOOL_NAME = "emit_extraction"


def _load_system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _build_tool_schema() -> dict:
    schema = ExtractionResult.model_json_schema()
    return {
        "name": _TOOL_NAME,
        "description": "Emit the structured extraction result for the given protocol.",
        "input_schema": schema,
    }


class ExtractionError(RuntimeError):
    """Raised when Claude does not return a valid, schema-conforming extraction."""


def extract(protocol_text: str) -> ExtractionResult:
    """Run entity extraction on a free-text protocol and return a validated ExtractionResult.

    Raises ExtractionError if the model doesn't call the tool or returns
    output that fails schema validation — callers should not silently
    swallow this; a broken extraction should surface, not be guessed at.
    """
    settings = get_settings()
    client = get_client()
    tool = _build_tool_schema()

    response = client.messages.create(
        model=settings.precaution_model,
        max_tokens=4096,
        temperature=0,
        system=_load_system_prompt(),
        tools=[tool],
        tool_choice={"type": "tool", "name": _TOOL_NAME},
        messages=[{"role": "user", "content": protocol_text}],
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == _TOOL_NAME:
            return _validate(block.input)

    raise ExtractionError(
        f"Model did not call the '{_TOOL_NAME}' tool. Stop reason: {response.stop_reason!r}"
    )


def _validate(raw: dict) -> ExtractionResult:
    try:
        return ExtractionResult.model_validate(raw)
    except Exception as exc:  # pydantic.ValidationError, but keep this broad and re-raise typed
        raise ExtractionError(f"Extraction output failed schema validation: {exc}") from exc
