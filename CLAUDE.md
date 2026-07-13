# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

PreCaution: a procedure-aware lab safety brief generator. A scientist pastes a free-text
experimental protocol; the tool extracts the chemicals and steps, grounds each chemical's
hazards against PubChem, and flags interaction hazards that emerge from the *procedure*
(e.g. two reagents combined in a step, or two waste streams converging into the same carboy
across steps) rather than just listing per-chemical hazards. It is strictly defensive — it
never suggests reagents, quantities, or synthetic routes, and never completes/extends a
procedure. Built for the Built with Claude: Life Sciences Hackathon (submission window
July 7–13, 2026).

Every hazard claim must trace back to a real, checkable source. Nothing about chemical
hazards or interaction danger is generated from model recall.

## Commands

Python 3.13, run from a `.venv`.

One-command setup + launch (creates the venv, installs deps, ensures `.env`, starts the app on
`127.0.0.1:8000`). This is the judge-facing path — idempotent and offline-safe on re-runs:

```bash
bash run.sh      # macOS / Linux
.\run.ps1        # Windows (PowerShell)
```

Manual equivalent:

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows; use .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
cp .env.example .env          # fill in ANTHROPIC_API_KEY
```

Run tests:
```bash
pytest                                   # default: excludes tests marked `costly` (real Anthropic API spend) — see pytest.ini
pytest -m costly                         # also run the costly (real Anthropic API) tests — opt in explicitly
pytest tests/test_pubchem.py             # one file
pytest tests/test_pubchem.py::test_parse_ghs_classification_offline   # one test
```

Tests that call the real PubChem API (free, network-dependent) run by default and self-skip if the
network is unreachable. Tests that call the real Anthropic API are marked `@pytest.mark.costly` and
are excluded by default (`addopts = -m "not costly"` in `pytest.ini`) — opt in with `pytest -m costly`
when you actually want to validate against a live model call. Apply the same marker to any new test
that spends Anthropic budget.

Run extraction only against a protocol file:
```bash
python scripts/run_extraction.py tests/fixtures/demo_protocol.txt
```

Run the full pipeline (all four stages, ending in a rendered `Brief`) against a protocol file:
```bash
python scripts/run_pipeline.py tests/fixtures/demo_protocol.txt
```

Run the API locally:
```bash
uvicorn app.main:app --reload
```

There is no lint/format command configured yet.

## Architecture

Four-stage pipeline, each stage a separate module with a narrow, typed interface, wired
together by `app/pipeline.py::run_pipeline()`. The stage boundaries are deliberate and
load-bearing for the project's core claim ("every verdict is grounded, not generated") —
don't collapse them for convenience.

1. **Extraction** (`app/extraction.py`, prompt in `prompts/extraction_system.md`) — free
   protocol text → `ExtractionResult` (`app/models.py`). Uses the Anthropic SDK with a
   *forced* tool call (`tool_choice={"type": "tool", ...}`) whose `input_schema` is
   generated directly from the `ExtractionResult` pydantic model via
   `model_json_schema()` — the schema has exactly one source of truth. Captures not just
   a flat chemical list but: per-chemical `resolution_reasoning` (how each name was
   normalized/coreference-resolved), `RecognizedMixture` (named results of combining
   chemicals, e.g. "piranha solution", which are explicitly *not* extracted as their own
   chemical), per-step `chemicals_present` tagged with `origin` (`added` /
   `carried_over` / `residual` — this is what lets waste-stream convergence across steps
   get caught, not just same-sentence mixing), and `unresolved_mentions` for anything
   that can't be confidently resolved.

2. **Per-chemical grounding** (`app/pubchem.py`) — `canonical_name` →
   `ChemicalHazardProfile`. The only live network dependency in the pipeline. Two public,
   no-auth PubChem APIs: PUG-REST (name → CID) and PUG-View (CID + heading → structured
   safety content, e.g. `GHS Classification`, `Reactive Group`, `Personal Protective
   Equipment (PPE)`, `First Aid Measures`, `Disposal Methods`, `Storage Conditions`).
   Notably, PubChem's `Reactive Group` heading is itself sourced from NOAA CAMEO
   Chemicals, complete with citation — so reactive-group *assignment* comes live through
   this same module, no separate CAMEO scraping needed. Every request is disk-cached
   (`app/cache.py`, `.cache/pubchem/`, gitignored — indefinite cache since hazard data is
   static for this project's purposes) and retried with backoff on transient failures
   (`_MIN_INTERVAL_SECONDS` throttle to respect PubChem's 5 req/s limit). A missing
   chemical or missing heading is never inferred as "safe" — it's recorded explicitly
   (`found=False`, `missing_sections`) per the honest-omission rule below.

3. **Interaction reasoning** — split across two modules with a strict boundary:
   - `app/interaction_matrix.py` — a small, **hand-encoded, offline** table mapping pairs
     of reactive-group names to an `InteractionVerdict` (hazard types + summary +
     source). Every entry must be fetched and quoted directly from its actual CAMEO
     Chemicals datasheet (`cameochemicals.noaa.gov/react/<id>`) at the time it's added —
     never written from general chemistry knowledge. This is intentionally a small seed
     set scoped to the locked demo protocol; extend deliberately, same fetch-and-quote
     process each time.
   - `app/interactions.py` — combines extraction's step/vessel model with each
     chemical's grounded reactive groups and the matrix lookup to produce one
     `ChemicalPairFinding` per co-present chemical pair per step. Every pair gets one of
     three explicit outcomes: `hazard_found`, `no_established_data` (pair not in the
     local table — never treated as "safe"), or `insufficient_reactive_group_data`
     (PubChem had no reactive-group data for one/both chemicals). Nothing is silently
     dropped.

   Keep the live per-chemical assignment (`pubchem.py`) and the offline pairwise verdict
   (`interaction_matrix.py`) as separate modules with `interactions.py` as the only thing
   that touches both — this is what makes "this verdict is looked up, not generated" a
   defensible claim.

4. **Brief rendering** (`app/brief.py::build_brief()`) — composes `ExtractionResult` +
   `ChemicalHazardProfile` + `ChemicalPairFinding` into a `Brief` (`app/models.py`): a
   list of `BriefStatement { text, kind, source_ref, source_url, unverified, step_number,
   chemical_ids, pair }` plus a `steps` index for per-step UI grouping. **Deliberately
   makes zero Claude calls** — pure Python composition over data the earlier stages
   already fetched. This was a considered decision, not just a cost default: with no
   generation step in this module, there is no possibility of an ungrounded claim
   slipping into the render layer, extending "looked up, not generated" to the render
   stage too. Every `BriefStatement.source_ref` must be non-empty and every grounded-kind
   statement's `source_url` must be set — enforced by
   `tests/test_brief.py::test_every_brief_statement_has_resolvable_source_ref`, not just
   asserted. A compound-specific glove-material recommendation and a reagent-substitution
   feature were both explicitly designed out (see `private/Build_Spec.md` §4.4) — don't
   re-add either without reading why first.

`app/pipeline.py::run_pipeline(protocol_text) -> PipelineResult` runs all four stages
end-to-end and is the thing both `scripts/run_pipeline.py` and (eventually) a `POST /brief`
endpoint sit on top of. `scripts/run_extraction.py` (Stage 1 only) and
`scripts/run_pipeline.py` (all four stages) mirror the same CLI convention.

`app/models.py` is the schema backbone: `ExtractionResult` is the contract extraction
produces and every later stage consumes; `ChemicalHazardProfile` is what grounding
produces per chemical (keyed by `canonical_name`); `SourceRef` is attached to every
hazard-bearing field throughout so claims are always traceable to a clickable source.

`app/main.py` is a thin FastAPI wrapper (currently only exposes `/extract` — a `/brief`
endpoint over `run_pipeline()` is deferred until the web UI actually needs it);
`app/config.py` loads settings from `.env` via pydantic-settings; `app/claude_client.py`
is a cached Anthropic client factory.

### The honest-omission rule

This is a project-wide invariant, not just a per-module convention: whenever authoritative
data is missing (a chemical PubChem doesn't recognize, a heading with no content, a
reactive-group pair not in the local matrix, a protocol mention that can't be resolved),
that absence must be surfaced explicitly to the end user, never silently dropped and never
inferred as "safe." Every module above has a specific field for this
(`missing_sections`, `unresolved_mentions`, `no_established_data` /
`insufficient_reactive_group_data`) — when adding new grounding or reasoning logic, give it
an equivalent explicit-absence path rather than letting `None`/empty silently mean "fine."

### Testing pattern

Each stage's tests follow the same two-tier shape (see `tests/test_extraction.py`,
`tests/test_pubchem.py`): an **offline tier** using real API responses captured once into
`tests/fixtures/` (fast, deterministic, no network/key needed) and a **live tier** that
hits the real API and is skipped automatically when the prerequisite isn't available
(`pytest.mark.skipif(not get_settings().anthropic_api_key, ...)` for Claude calls; PubChem
live tests just need network). When adding grounding logic against a new PubChem heading or
a new interaction-matrix entry, capture the real response into `tests/fixtures/` rather
than hand-writing a synthetic one.

## Working in this repo

- `private/` holds hackathon planning docs — gitignored, never pushed.
  `private/Build_Spec.md` is the primary source of truth for technical/architecture state
  (per-stage data provenance, non-negotiable design rules, current status) and wins on any
  conflict; read it first when asked to resync or figure out what's next.
  `private/Hackathon_Build_Tracker.md` is the day-by-day calendar/checklist.
  `Project_Baseline_Context.md` and `Hackathon_Idea_Research_and_Comparison.md` are decision
  archives (the *why*), rarely needed once a decision is locked.
- Never add a `Co-Authored-By: Claude` trailer to commits in this repo.
- When adding or extending authoritative data (reactive-group verdicts, hazard claims),
  fetch and verify against the real primary source at build time — never fill it in from
  model recall, even for chemistry that seems well-known.
