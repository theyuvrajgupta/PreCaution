"""PubChem grounding: canonical chemical name -> ChemicalHazardProfile.

Two live PubChem APIs, both public/no-auth (confirmed working 2026-07-09):
  - PUG-REST: name -> CID.
  - PUG-View: CID + heading -> structured/free-text safety content, always
    carrying its own source citation.

Design rule carried over from the project docs: never silently omit. If a
chemical can't be resolved, or a specific heading has no data, that is
recorded in the profile (found=False / missing_sections), never dropped.

Resilience: every request is disk-cached (app/cache.py) and retried with
backoff on transient failures (connection errors, timeouts, 5xx). For the
demo's handful of chemicals this means a transient network blip can't break
a live run, and once the demo chemicals have been fetched once, recording
the Day-4 video doesn't depend on live network conditions at all.
"""

import json
import re
import time
from typing import Literal
from urllib.parse import quote

import httpx

from app import cache as _cache
from app.models import (
    ChemicalHazardProfile,
    GHSInfo,
    ReactiveGroupEntry,
    SafetyExcerpt,
    SafetyNote,
    SourceRef,
)

PUG_REST_BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
PUG_VIEW_BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound"

# PubChem's published rate limit is 5 req/s. A small floor between requests
# keeps a multi-chemical, multi-heading grounding run comfortably under it.
_MIN_INTERVAL_SECONDS = 0.25
_last_request_at: float = 0.0

_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE_SECONDS = 0.5  # doubles each retry: 0.5s, 1s, 2s

# Headings pulled for every chemical's hazard profile. All live under the
# "Safety and Hazards" section of a PubChem compound record.
SAFETY_NOTE_HEADINGS = [
    "Personal Protective Equipment (PPE)",
    "First Aid Measures",
    "Disposal Methods",
    "Storage Conditions",
]


def _throttle() -> None:
    global _last_request_at
    elapsed = time.monotonic() - _last_request_at
    if elapsed < _MIN_INTERVAL_SECONDS:
        time.sleep(_MIN_INTERVAL_SECONDS - elapsed)
    _last_request_at = time.monotonic()


def _get_json(url: str, params: dict | None = None) -> dict | None:
    """GET url with caching + retry-with-backoff on transient failures.

    Returns the parsed JSON body, or None for a definitive "no data" (404,
    or a well-formed PUG-View Fault body) — never raises for that case, since
    it's an expected outcome, not a failure. Only raises if every retry is
    exhausted on a genuinely transient error.
    """
    cached = _cache.get(url, params)
    if cached is not None:
        return cached

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        _throttle()
        try:
            resp = httpx.get(url, params=params, timeout=30)
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            last_exc = exc
            time.sleep(_RETRY_BACKOFF_BASE_SECONDS * (2**attempt))
            continue

        if resp.status_code == 404:
            return None
        if resp.status_code >= 500:
            last_exc = RuntimeError(f"PubChem returned HTTP {resp.status_code} for {url}")
            time.sleep(_RETRY_BACKOFF_BASE_SECONDS * (2**attempt))
            continue

        resp.raise_for_status()
        # PubChem's declared charset isn't reliable enough for httpx's .json() charset
        # sniffing (it mis-decodes UTF-8 bytes as Latin-1 for some responses, corrupting
        # non-ASCII text like "•" into "â¢"). JSON is UTF-8 by definition (RFC 8259), so
        # decode the raw bytes explicitly instead of trusting response encoding detection.
        data = json.loads(resp.content)
        if "Fault" in data:
            return None  # well-formed "no data for this heading" — not an error, don't cache
        _cache.put(url, params, data)
        return data

    raise RuntimeError(f"PubChem request failed after {_MAX_RETRIES} attempts: {url}") from last_exc


def resolve_cid(name: str) -> int | None:
    """Name -> CID via PUG-REST. Returns None if PubChem has no match."""
    url = f"{PUG_REST_BASE}/compound/name/{quote(name)}/cids/JSON"
    data = _get_json(url)
    if data is None:
        return None
    cids = data.get("IdentifierList", {}).get("CID", [])
    return cids[0] if cids else None


def _fetch_heading(cid: int, heading: str) -> dict | None:
    """Raw PUG-View JSON for one heading. Returns None if PubChem has no
    data for this (cid, heading) pair — a normal, expected outcome, not an
    error (most chemicals don't have every heading)."""
    url = f"{PUG_VIEW_BASE}/{cid}/JSON"
    return _get_json(url, params={"heading": heading})


def _find_section(node, heading: str) -> dict | None:
    """Depth-first search for a Section/sub-Section with this TOCHeading."""
    if isinstance(node, dict):
        if node.get("TOCHeading") == heading:
            return node
        for value in node.values():
            found = _find_section(value, heading)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_section(item, heading)
            if found is not None:
                return found
    return None


def _references_by_number(record: dict) -> dict[int, dict]:
    return {ref["ReferenceNumber"]: ref for ref in record.get("Reference", [])}


def _fix_mojibake(text: str) -> str:
    """Some PubChem-sourced text (ERG/NIOSH excerpts) is double-encoded at the source:
    confirmed live (2026-07-10) that PubChem's own HTTP response bytes for a bullet
    character are C3 A2 C2 80 C2 A2 — the UTF-8 encoding of "•" (E2 80 A2) with each of
    those three bytes *individually* re-encoded as UTF-8 a second time, as if an
    upstream system had decoded UTF-8 bytes as Latin-1 and then UTF-8-encoded the
    result. Correctly decoding our own response (this module already does) faithfully
    reproduces their corruption, so it must be reversed after parsing, not before.

    The Latin-1 C1 control range (U+0080-U+009F) never appears in legitimate English
    safety text, so its presence reliably flags this exact pattern. Reverse it by
    re-encoding as Latin-1 (a lossless 1:1 byte mapping for codepoints 0-255) and
    decoding as UTF-8. Leaves anything that doesn't match the pattern untouched —
    never fabricates a fix.
    """
    if not any(0x80 <= ord(ch) <= 0x9F for ch in text):
        return text
    try:
        return text.encode("latin1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def _strings_with_markup_text(value: dict) -> str:
    parts = [_fix_mojibake(s.get("String", "")) for s in value.get("StringWithMarkup", [])]
    return " ".join(p for p in parts if p)


def get_ghs_classification(cid: int) -> GHSInfo | None:
    """A compound can carry GHS classifications from several independent
    notifiers (e.g. EU CLP harmonized classification, ECHA C&L Inventory
    self-notifications, national agencies), each its own ReferenceNumber
    group in the same section. PubChem's own UI shows only the first/primary
    one by default (DisplayControls.ShowAtMost == 1) — mixing all of them
    together produces a duplicated, contradictory-looking hazard list, so we
    match that behavior and take only the first group."""
    data = _fetch_heading(cid, "GHS Classification")
    if data is None:
        return None
    record = data["Record"]
    section = _find_section(record, "GHS Classification")
    if section is None:
        return None

    information = section.get("Information", [])
    if not information:
        return None
    primary_ref_num = information[0].get("ReferenceNumber")
    primary_info = [info for info in information if info.get("ReferenceNumber") == primary_ref_num]

    pictograms: list[str] = []
    pictogram_urls: list[str] = []
    signal_word: str | None = None
    hazard_statements: list[str] = []
    precautionary_statements: list[str] = []

    for info in primary_info:
        name = info.get("Name", "")
        value = info.get("Value", {})
        if name == "Pictogram(s)":
            for swm in value.get("StringWithMarkup", []):
                for markup in swm.get("Markup", []):
                    if markup.get("Type") == "Icon":
                        pictogram_urls.append(markup.get("URL", ""))
                        pictograms.append(_fix_mojibake(markup.get("Extra", "")))
        elif name == "Signal":
            text = _strings_with_markup_text(value)
            if text:
                signal_word = text
        elif name == "GHS Hazard Statements":
            for swm in value.get("StringWithMarkup", []):
                s = _fix_mojibake(swm.get("String", ""))
                if s:
                    hazard_statements.append(s)
        elif name == "Precautionary Statement Codes":
            text = _strings_with_markup_text(value)
            if text:
                # PubChem writes this as a natural-language list ("P405, and P501"), not
                # a clean CSV — strip the trailing conjunction so codes match cleanly.
                for part in text.split(","):
                    code = part.strip()
                    if code.lower().startswith("and "):
                        code = code[4:].strip()
                    if code:
                        precautionary_statements.append(code)

    ref = _references_by_number(record).get(primary_ref_num, {})
    source = SourceRef(
        source_name=ref.get("SourceName", "PubChem GHS Classification"),
        url=ref.get("URL") or f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}#section=GHS-Classification",
        detail=ref.get("Name"),
    )

    return GHSInfo(
        pictograms=pictograms,
        pictogram_urls=pictogram_urls,
        signal_word=signal_word,
        hazard_statements=hazard_statements,
        precautionary_statements=precautionary_statements,
        source=source,
    )


def get_reactive_groups(cid: int) -> list[ReactiveGroupEntry]:
    data = _fetch_heading(cid, "Reactive Group")
    if data is None:
        return []
    record = data["Record"]
    section = _find_section(record, "Reactive Group")
    if section is None:
        return []

    refs = _references_by_number(record)
    seen: set[str] = set()
    entries: list[ReactiveGroupEntry] = []
    for info in section.get("Information", []):
        ref_num = info.get("ReferenceNumber")
        ref = refs.get(ref_num, {})
        for swm in info.get("Value", {}).get("StringWithMarkup", []):
            group_name = _fix_mojibake(swm.get("String", ""))
            if not group_name or group_name in seen:
                continue
            seen.add(group_name)
            entries.append(
                ReactiveGroupEntry(
                    group_name=group_name,
                    source=SourceRef(
                        source_name=ref.get("SourceName", "CAMEO Chemicals"),
                        url=ref.get("URL"),
                        detail=ref.get("Name"),
                    ),
                )
            )
    return entries


# PubChem prefixes a self-identifying citation onto some excerpts, e.g.
# "Excerpt from NIOSH Pocket Guide for Sulfuric acid:" or "Excerpt from ERG
# Guide 140 [Oxidizers]:" — confirmed live 2026-07-10 as a standalone string
# that always precedes the excerpt's own body text. This is the only place
# the true original authority (NIOSH / ERG) is stated; the Reference block's
# own SourceName is often just the aggregator (e.g. "CAMEO Chemicals", which
# rehosts both NIOSH and ERG content under its own ReferenceNumber).
_EXCERPT_PREFIX_RE = re.compile(r"^Excerpt from (.+?):\s*$")


def _classify_audience(label: str) -> Literal["niosh", "erg", "other"]:
    lowered = label.lower()
    if "niosh" in lowered:
        return "niosh"
    if "erg" in lowered or "emergency response guidebook" in lowered:
        return "erg"
    return "other"


def get_safety_note(cid: int, heading: str) -> SafetyNote | None:
    data = _fetch_heading(cid, heading)
    if data is None:
        return None
    record = data["Record"]
    section = _find_section(record, heading)
    if section is None:
        return None

    refs = _references_by_number(record)

    # Group by resolved source label, not raw ReferenceNumber: PubChem sometimes
    # cites the identical excerpt under two different ReferenceNumbers (confirmed:
    # sulfuric acid's PPE heading repeats its NIOSH excerpt verbatim under refs 6
    # and 7) — grouping by label naturally merges those, and the per-(label, text)
    # dedup below still guards against literal repeats within a group.
    group_order: list[str] = []
    group_texts: dict[str, list[str]] = {}
    group_source: dict[str, SourceRef] = {}
    seen: set[tuple[str, str]] = set()

    _UNSET = object()
    prev_refnum: object = _UNSET
    current_label: str | None = None

    def _ensure_group(label: str, ref: dict) -> None:
        if label not in group_texts:
            group_order.append(label)
            group_texts[label] = []
            group_source[label] = SourceRef(
                source_name=ref.get("SourceName", label), url=ref.get("URL"), detail=ref.get("Name")
            )

    for info in section.get("Information", []):
        refnum = info.get("ReferenceNumber")
        ref = refs.get(refnum, {})
        if refnum != prev_refnum:
            current_label = None  # a new citation starts; forget any prior "Excerpt from" label
        prev_refnum = refnum

        for swm in info.get("Value", {}).get("StringWithMarkup", []):
            raw = _fix_mojibake(swm.get("String", ""))
            stripped = raw.strip()
            if not stripped:
                continue

            marker = _EXCERPT_PREFIX_RE.match(stripped)
            if marker:
                current_label = marker.group(1).strip()
                _ensure_group(current_label, ref)
                continue  # the marker line itself isn't body text

            label = current_label or ref.get("SourceName") or "PubChem"
            _ensure_group(label, ref)
            key = (label, stripped)
            if key in seen:
                continue
            seen.add(key)
            group_texts[label].append(raw)

    if not group_order:
        return None

    excerpts = [
        SafetyExcerpt(
            source_label=label,
            audience=_classify_audience(label),
            text=" ".join(group_texts[label]),
            source=group_source[label],
        )
        for label in group_order
        if group_texts[label]  # a label that only ever matched its own marker line has no body text
    ]
    if not excerpts:
        return None

    return SafetyNote(heading=heading, excerpts=excerpts)


def ground_chemical(canonical_name: str) -> ChemicalHazardProfile:
    """The main entry point: canonical chemical name -> full hazard profile.

    Never raises. Two distinct "no data" outcomes, kept honestly separate:
      - found=False, grounding_error=None: PubChem was reached and has no
        record for this name (a definitive, confirmed absence).
      - found=False, grounding_error=<message>: grounding could not be
        COMPLETED (network outage, PubChem 5xx after retries exhausted —
        see _get_json). Hazard status is UNKNOWN here, not confirmed
        absent — callers must not treat this the same as a real 404. A
        transient failure on one chemical must never masquerade as "this
        chemical doesn't exist," and must never crash the whole pipeline.
    """
    try:
        cid = resolve_cid(canonical_name)
        if cid is None:
            return ChemicalHazardProfile(query_name=canonical_name, found=False, missing_sections=["CID resolution"])

        profile = ChemicalHazardProfile(
            query_name=canonical_name,
            found=True,
            cid=cid,
            pubchem_url=f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}",
        )

        ghs = get_ghs_classification(cid)
        if ghs is not None:
            profile.ghs = ghs
        else:
            profile.missing_sections.append("GHS Classification")

        reactive_groups = get_reactive_groups(cid)
        if reactive_groups:
            profile.reactive_groups = reactive_groups
        else:
            profile.missing_sections.append("Reactive Group")

        for heading in SAFETY_NOTE_HEADINGS:
            note = get_safety_note(cid, heading)
            if note is not None:
                profile.safety_notes.append(note)
            else:
                profile.missing_sections.append(heading)

        return profile
    except (httpx.HTTPError, RuntimeError) as exc:
        # RuntimeError: _get_json exhausted its retries on a transient network/5xx
        # failure. httpx.HTTPError: an uncaught non-404/non-5xx status from
        # resp.raise_for_status(). Isolate the failure to this one chemical rather
        # than letting it take down the whole pipeline run.
        return ChemicalHazardProfile(query_name=canonical_name, found=False, grounding_error=str(exc))
