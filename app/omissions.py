"""Omission-detection: the third pillar (Build_Spec.md's framing) alongside
comprehension (extraction) and accumulation (carryover tracking) — noticing
what a protocol failed to specify that matters for safety.

Architecture rule: this is a SEPARATE Claude reasoning step on the "Claude
reads" side of the trust line, same category as extraction, never touching
the zero-model-call hazard-verdict path (grounding, the CAMEO interaction
table, brief composition's hazard logic). Its output never feeds into, alters,
or renders as a hazard verdict — it's its own BriefKind, rendered only in the
left (bench) pane, never in the right-pane scan layer or interaction section.

Same forced-tool-call pattern as app/extraction.py, same one-source-of-truth
schema generation. The one difference: extraction reads raw protocol text;
this reads ALREADY-extracted steps and ALREADY-grounded per-chemical
precautionary data (built by _build_user_message below) — it never re-reads
the protocol from scratch and never fetches anything itself.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.claude_client import get_client
from app.config import get_settings
from app.models import ChemicalHazardProfile, ExtractionResult
from app.precautionary_codes import resolve_precautionary_code

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "omission_system.md"
_TOOL_NAME = "emit_omission_flags"


class OmissionFlag(BaseModel):
    step_number: int = Field(description="Which step this observation applies to.")
    basis: Literal["sds", "procedural"] = Field(
        description="'sds': grounded in a specific chemical's own precautionary data. "
        "'procedural': one of the fixed procedural-completeness categories, no chemical record involved."
    )
    chemical_ids: list[str] = Field(
        default_factory=list, description="For basis='sds', the chemical id(s) whose data motivated this. Empty for 'procedural'."
    )
    text: str = Field(
        description="The tentative observation itself — one sentence, never a prescribed fix, "
        "ending with a soft handoff (e.g. 'Worth confirming.')."
    )


class OmissionDetectionResult(BaseModel):
    flags: list[OmissionFlag] = Field(default_factory=list)


class OmissionDetectionError(RuntimeError):
    """Raised when Claude does not return a valid, schema-conforming result."""


def _load_system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _build_tool_schema() -> dict:
    schema = OmissionDetectionResult.model_json_schema()
    return {
        "name": _TOOL_NAME,
        "description": "Emit the tentative omission flags for this protocol's steps.",
        "input_schema": schema,
    }


def _chemical_summary(profile: ChemicalHazardProfile) -> str | None:
    """One line per chemical: GHS signal word + resolved precautionary statements —
    the concrete, grounded 'what precaution applies' signal the SDS-grounded basis
    reasons over. None when there's genuinely nothing to summarize (not found, or
    found but carries no GHS/precautionary data) — the prompt already treats that as
    unremarkable, not a reason to flag anything."""
    if profile.ghs is None:
        return None
    parts = []
    if profile.ghs.signal_word:
        parts.append(f'signal word "{profile.ghs.signal_word}"')
    if profile.ghs.precautionary_statements:
        resolved = [
            f"{code}: {resolve_precautionary_code(code)}" if resolve_precautionary_code(code) else code
            for code in profile.ghs.precautionary_statements
        ]
        parts.append("precautions: " + "; ".join(resolved))
    if not parts:
        return None
    return "; ".join(parts)


def _build_user_message(result: ExtractionResult, profiles: dict[str, ChemicalHazardProfile]) -> str:
    chemical_by_id = {c.id: c for c in result.chemicals}

    chem_lines = ["CHEMICALS (grounded safety data):"]
    for chemical in result.chemicals:
        profile = profiles.get(chemical.canonical_name)
        summary = _chemical_summary(profile) if profile else None
        chem_lines.append(
            f"- {chemical.id} {chemical.canonical_name}: {summary}"
            if summary
            else f"- {chemical.id} {chemical.canonical_name}: no precautionary data on file"
        )

    step_lines = ["", "STEPS:"]
    for step in result.steps:
        present = []
        for ref in step.chemicals_present:
            name = chemical_by_id.get(ref.chemical_id)
            label = name.canonical_name if name else ref.chemical_id
            present.append(f"{ref.chemical_id} {label} ({ref.origin})")
        vessel_part = f" Vessel: {step.vessel}." if step.vessel else ""
        chemicals_part = f" Chemicals: {', '.join(present)}." if present else " Chemicals: none tracked."
        step_lines.append(f'{step.number}. "{step.text}"{vessel_part}{chemicals_part}')

    return "\n".join(chem_lines + step_lines)


def detect_omissions(result: ExtractionResult, profiles: dict[str, ChemicalHazardProfile]) -> OmissionDetectionResult:
    """Run omission-detection over an already-extracted, already-grounded protocol.

    Never raises for "no flags found" (that's OmissionDetectionResult(flags=[]), a
    normal result) — only raises OmissionDetectionError if Claude doesn't call the
    tool or returns schema-invalid output, mirroring app.extraction.extract's contract.
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
        messages=[{"role": "user", "content": _build_user_message(result, profiles)}],
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == _TOOL_NAME:
            try:
                return OmissionDetectionResult.model_validate(block.input)
            except Exception as exc:
                raise OmissionDetectionError(f"Omission-detection output failed schema validation: {exc}") from exc

    raise OmissionDetectionError(
        f"Model did not call the '{_TOOL_NAME}' tool. Stop reason: {response.stop_reason!r}"
    )
