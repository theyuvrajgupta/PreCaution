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

import time
from urllib.parse import quote

import httpx

from app import cache as _cache
from app.models import ChemicalHazardProfile, GHSInfo, ReactiveGroupEntry, SafetyNote, SourceRef

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
        data = resp.json()
        if "Fault" in data:
            return None  # well-formed "no data for this heading" — not an error, don't cache
        _cache.set(url, params, data)
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


def _strings_with_markup_text(value: dict) -> str:
    parts = [s.get("String", "") for s in value.get("StringWithMarkup", [])]
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
                        pictograms.append(markup.get("Extra", ""))
        elif name == "Signal":
            text = _strings_with_markup_text(value)
            if text:
                signal_word = text
        elif name == "GHS Hazard Statements":
            for swm in value.get("StringWithMarkup", []):
                s = swm.get("String", "")
                if s:
                    hazard_statements.append(s)
        elif name == "Precautionary Statement Codes":
            text = _strings_with_markup_text(value)
            if text:
                precautionary_statements.extend(p.strip() for p in text.split(",") if p.strip())

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
            group_name = swm.get("String", "")
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


def get_safety_note(cid: int, heading: str) -> SafetyNote | None:
    data = _fetch_heading(cid, heading)
    if data is None:
        return None
    record = data["Record"]
    section = _find_section(record, heading)
    if section is None:
        return None

    refs = _references_by_number(record)
    texts: list[str] = []
    source: SourceRef | None = None
    for info in section.get("Information", []):
        text = _strings_with_markup_text(info.get("Value", {}))
        if text:
            texts.append(text)
        if source is None:
            ref = refs.get(info.get("ReferenceNumber"))
            if ref:
                source = SourceRef(source_name=ref.get("SourceName", "PubChem"), url=ref.get("URL"), detail=ref.get("Name"))

    if not texts:
        return None
    if source is None:
        source = SourceRef(source_name="PubChem", url=f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}")

    return SafetyNote(heading=heading, text=" | ".join(texts), source=source)


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
