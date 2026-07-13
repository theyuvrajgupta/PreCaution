// PreCaution — state machine + stage log + brief rendering
// (the last delegated to render.js).
//
// Five states, one explicit variable, no routing:
//   empty -> reading -> read
//                    -> incomplete   (grounding failed for >=1 chemical)
//                    -> failed       (pipeline could not run at all)
//
// Global invariant: the pasted protocol text is never lost — not on error,
// not on retry, not on a source-chip click.

import { parseSSEStream } from "./stream.js";
import { renderBrief, renderInteractionTable } from "./render.js";
import { renderThread } from "./thread.js";

const DEMO_PROTOCOL = `1. Prepare piranha solution by slowly adding 30 mL of 30% hydrogen peroxide to 90 mL of concentrated sulfuric acid in a glass beaker inside the fume hood.
2. Submerge glass coverslips in the piranha solution for 15 minutes to strip organic residue.
3. Rinse the coverslips thoroughly with deionized water and dry under a stream of nitrogen.
4. Pour the spent piranha solution into the acid waste carboy.
5. Rinse the glassware used for the protein purification buffer (PBS with 0.02% sodium azide) and add that rinse to the same acid waste carboy.
`;

const MIN_LINE_MS = 150; // a line may linger, minimum 150ms visible
const BRIEF_HOLD_MS = 1500; // hold the completed stage log so its final "Composing the brief" line is seen, not flashed away by the transition to the rendered brief

const appEl = document.getElementById("app");
const protocolInput = document.getElementById("protocol-input");
const demoBtn = document.getElementById("demo-btn");
const readBtn = document.getElementById("read-btn");
const brandHomeBtn = document.getElementById("brand-home");
const benchControlsEl = document.getElementById("bench-controls");
const benchGutterEl = document.getElementById("bench-gutter");
const benchStepsEl = document.getElementById("bench-steps");
const printBtn = document.getElementById("print-btn");
const interactionTableDialog = document.getElementById("interaction-table-dialog");
const interactionTableBody = document.getElementById("interaction-table-body");
const interactionTableClose = document.getElementById("interaction-table-close");

const panels = {
  empty: document.getElementById("paper-empty"),
  stageLog: document.getElementById("stage-log"),
  receipt: document.getElementById("receipt"),
  incompleteBanner: document.getElementById("incomplete-banner"),
  briefOutput: document.getElementById("brief-output"),
  failedPanel: document.getElementById("failed-panel"),
};

const state = {
  current: "empty", // 'empty' | 'reading' | 'read' | 'incomplete' | 'failed'
  protocolText: "",
  brief: null, // the final Brief, once we have one
  extractionDetail: null, // {chemicals, steps, mixtures, unresolved} counts from the stream
  chemicalRecords: [], // [{name, cid, found, missing_sections, chemical_ids, concentration}, ...]
  lastError: null,
  // 'error' (extraction/network failure) | 'no_chemicals' (not an error, an
  // explanation) — which flavor of the 'failed' state to render.
  failureKind: "error",
};

function setState(next) {
  state.current = next;
  appEl.dataset.state = next;
  // The bench pane's sticky thread is anchored within a container as tall as the
  // (usually much longer) paper pane, so it can track scroll across the whole brief.
  // But that means whatever scroll position accumulated while the stage log
  // was streaming carries straight into the freshly-rendered brief — the thread would
  // open mid-stuck, below the fold, instead of at the top. A brief that just finished
  // loading should always be seen from its own top. Same reasoning for landing back on
  // "empty" — resetToEmpty() and the failed-panel's "load the demo" both go through
  // here, and either could be triggered from deep in a scrolled-down brief.
  if (next === "read" || next === "incomplete" || next === "empty") {
    window.scrollTo(0, 0);
  }
  render();
}

function showOnly(...visibleKeys) {
  for (const [key, el] of Object.entries(panels)) {
    el.hidden = !visibleKeys.includes(key);
  }
}

function clearChildren(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function showBenchThread(showThread) {
  // A <textarea> can't expose per-line layout, so once a Brief exists the bench
  // pane swaps from the raw pasted text to structured step rows the thread
  // can attach to. The pasted text itself is never discarded — just hidden.
  protocolInput.hidden = showThread;
  benchControlsEl.hidden = showThread;
  benchStepsEl.hidden = !showThread;
  if (!showThread) clearChildren(benchGutterEl);
}

function render() {
  // Only meaningful once there's a brief on the page.
  printBtn.hidden = state.current !== "read" && state.current !== "incomplete";

  switch (state.current) {
    case "empty":
      protocolInput.readOnly = false;
      showBenchThread(false);
      showOnly("empty");
      break;

    case "reading":
      protocolInput.readOnly = true;
      showBenchThread(false);
      showOnly("stageLog");
      break;

    case "read":
      protocolInput.readOnly = true;
      showBenchThread(true);
      renderThread(benchGutterEl, benchStepsEl, { steps: state.brief.steps, statements: state.brief.statements });
      renderReceipt();
      renderBrief(panels.briefOutput, {
        brief: state.brief,
        chemicalRecords: state.chemicalRecords,
        extractionDetail: state.extractionDetail,
        onOpenInteractionTable: openInteractionTablePanel,
      });
      showOnly("receipt", "briefOutput");
      break;

    case "incomplete":
      protocolInput.readOnly = true;
      showBenchThread(true);
      renderThread(benchGutterEl, benchStepsEl, { steps: state.brief.steps, statements: state.brief.statements });
      renderReceipt();
      renderIncompleteBanner();
      renderBrief(panels.briefOutput, {
        brief: state.brief,
        chemicalRecords: state.chemicalRecords,
        extractionDetail: state.extractionDetail,
        onOpenInteractionTable: openInteractionTablePanel,
      });
      showOnly("receipt", "incompleteBanner", "briefOutput");
      break;

    case "failed":
      protocolInput.readOnly = false; // per spec: Failed unlocks the bench, text intact
      showBenchThread(false);
      renderFailedPanel();
      showOnly("failedPanel");
      break;
  }
  updateReadButtonEnabled();
}

// Row heights are text-driven and reflow on resize — redraw the gutter overlay
// so marks stay pinned to their step row instead of drifting out of alignment.
let resizeTimer = null;
window.addEventListener("resize", () => {
  if (state.current !== "read" && state.current !== "incomplete") return;
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    renderThread(benchGutterEl, benchStepsEl, { steps: state.brief.steps, statements: state.brief.statements });
  }, 150);
});

function renderReceipt() {
  clearChildren(panels.receipt);
  // chemicals/steps counts already appear in the scan layer's first line right below —
  // kept once, there; the receipt now reports only what it uniquely carries.
  // "statements" -> "sourced claims", legible to a non-chemist reader.
  const line1 = document.createElement("p");
  const claimCount = state.brief.statements.length;
  line1.textContent = `Protocol read: ${claimCount} sourced claim${claimCount === 1 ? "" : "s"}`;
  panels.receipt.appendChild(line1);

  // The credit half of the footer's restraint line ("Claude read the protocol. Claude did
  // not write the safety advice.") — near the top of the brief, not just the
  // bottom, so a reader sees the division of labour before reading a single hazard claim,
  // not only after. Keep this to one line: what Claude structured, not a restatement of
  // every stage.
  const line2 = document.createElement("p");
  line2.className = "receipt-credit";
  line2.textContent =
    "Claude structured this protocol: its chemicals, steps, and vessels. Every hazard claim below is a deterministic lookup, not generated.";
  panels.receipt.appendChild(line2);
}

// This banner is intrinsic to what distinguishes the Incomplete state from Read.
// It renders before brief-output in DOM order (so it sits above the scan layer) and
// is non-dismissible (no close control is offered).
function renderIncompleteBanner() {
  clearChildren(panels.incompleteBanner);
  const { brief, extractionDetail } = state;

  const heading = document.createElement("p");
  heading.className = "signal";
  heading.textContent = "This brief is incomplete";

  const body = document.createElement("p");
  body.textContent =
    `Hazard data could not be retrieved for ${brief.incomplete_chemicals.length} of ` +
    `${extractionDetail.chemicals} chemicals: ${brief.incomplete_chemicals.join(", ")}.`;

  const caveat = document.createElement("p");
  caveat.textContent = "Absence of a warning below does not mean absence of hazard. Retry, or consult the SDS.";

  const retryBtn = document.createElement("button");
  retryBtn.type = "button";
  retryBtn.className = "btn btn-quiet";
  retryBtn.textContent = "Retry";
  retryBtn.addEventListener("click", startReading);

  panels.incompleteBanner.append(heading, body, caveat, retryBtn);
}

// Covers the "Extraction call fails (502)" case (and any failure before the stream
// produced a single real event — see the freeze-in-place handling in startReading for
// the mid-stream case, which does NOT route through here) plus "No chemicals identified"
// — not an error, an
// explanation, so it gets its own copy and an offer to load the demo instead
// of the danger-coloured "could not read" heading.
function renderFailedPanel() {
  clearChildren(panels.failedPanel);
  const heading = document.createElement("p");
  heading.className = "signal";

  const detail = document.createElement("p");
  detail.className = "mono";

  const retryBtn = document.createElement("button");
  retryBtn.type = "button";
  retryBtn.className = "btn btn-primary";
  retryBtn.textContent = "Retry";
  retryBtn.addEventListener("click", startReading);

  if (state.failureKind === "no_chemicals") {
    heading.textContent = "No chemicals identified.";
    // State what preCaution actually knows — that it found no chemicals — never that
    // the text "isn't a protocol," which it has no way to judge.
    detail.textContent = "No chemicals were confidently identified in this text.";

    const demoOfferBtn = document.createElement("button");
    demoOfferBtn.type = "button";
    demoOfferBtn.className = "btn btn-quiet";
    demoOfferBtn.textContent = "Load the demo protocol";
    demoOfferBtn.addEventListener("click", () => {
      protocolInput.value = DEMO_PROTOCOL;
      setState("empty");
      updateReadButtonEnabled();
      protocolInput.focus();
    });

    panels.failedPanel.append(heading, detail, demoOfferBtn, retryBtn);
  } else {
    heading.style.color = "var(--danger)";
    heading.textContent = "Could not read the protocol.";
    detail.textContent = state.lastError || "";
    panels.failedPanel.append(heading, detail, retryBtn);
  }
}

function updateReadButtonEnabled() {
  readBtn.disabled = state.current === "reading" || protocolInput.value.trim().length === 0;
}

// --- Stage log: a paced queue over real events ---------------------
//
// Rule 1 (never before its event): a render function is only enqueued once
// the underlying SSE event has actually arrived — nothing here is scripted
// or optimistic.
// Rule 2 (may linger, >=150ms): the queue drains itself at a fixed minimum
// pace, so a warm/cached run that produces every event within milliseconds
// still reads at human speed instead of flashing past.
function createPacedLog() {
  const queue = [];
  let draining = false;

  async function drain() {
    if (draining) return;
    draining = true;
    while (queue.length) {
      queue.shift()();
      await sleep(MIN_LINE_MS);
    }
    draining = false;
  }

  return {
    enqueue(renderFn) {
      queue.push(renderFn);
      drain();
    },
    async whenDrained() {
      while (draining || queue.length) await sleep(20);
    },
  };
}

function addLogBlock(lines, extraClass) {
  const block = document.createElement("div");
  block.className = "stage-log-block";
  for (const text of lines) {
    const p = document.createElement("p");
    p.className = "stage-log-line mono" + (extraClass ? " " + extraClass : "");
    p.textContent = text;
    block.appendChild(p);
  }
  panels.stageLog.appendChild(block);
  return block;
}

function appendLogSubline(block, text, extraClass) {
  const p = document.createElement("p");
  p.className = "stage-log-line stage-log-subline mono" + (extraClass ? " " + extraClass : "");
  p.textContent = text;
  block.appendChild(p);
}

// "Network dies mid-stream": freeze the log in place — turn the last
// real event's line to the error colour and offer Retry — rather than
// swapping to the full failed panel and hiding what already happened. Only
// called when the stream had genuinely produced at least one real event;
// see startReading's streamStarted check.
function freezeStageLogWithError() {
  const lines = panels.stageLog.querySelectorAll(".stage-log-line");
  if (lines.length) lines[lines.length - 1].classList.add("is-error");

  const retryBtn = document.createElement("button");
  retryBtn.type = "button";
  retryBtn.className = "btn btn-quiet";
  retryBtn.textContent = "Retry";
  retryBtn.style.marginTop = "0.75rem";
  retryBtn.addEventListener("click", startReading);
  panels.stageLog.appendChild(retryBtn);
}

// Home button (brand mark) needs to actually stop an in-flight run, not just hide its
// eventual result — otherwise clicking it mid-"reading" would reset the screen while a
// live Claude call and PubChem grounding kept running in the background, then silently
// yank the user back to "read"/"failed" once it finished. AbortController cancels the
// fetch; app/main.py's stream endpoint already handles a dropped client connection via
// asyncio.CancelledError ("let it propagate so work stops") — this isn't new backend
// behaviour, just the first thing on the frontend to actually use it.
let activeAbortController = null;

async function startReading() {
  const text = protocolInput.value;
  state.protocolText = text;
  state.chemicalRecords = [];
  state.extractionDetail = null;
  setState("reading");
  clearChildren(panels.stageLog);

  const log = createPacedLog();
  let extractionBlock = null;
  let finalBrief = null;
  let sawUnrecoverableError = false;
  let streamStarted = false; // did at least one real SSE event arrive? gates freeze-in-place vs the full failed panel

  activeAbortController = new AbortController();
  try {
    const res = await fetch("/brief/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ protocol_text: text }),
      signal: activeAbortController.signal,
    });
    if (!res.ok || !res.body) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `Request failed (${res.status})`);
    }

    for await (const { event, data } of parseSSEStream(res)) {
      streamStarted = true;
      if (event === "stage" && data.stage === "extraction" && data.status === "started") {
        log.enqueue(() => {
          extractionBlock = addLogBlock(["Reading the protocol…"]);
        });
      } else if (event === "stage" && data.stage === "extraction" && data.status === "done") {
        state.extractionDetail = data.detail;
        log.enqueue(() =>
          appendLogSubline(
            extractionBlock,
            `↳ ${data.detail.chemicals} chemical${data.detail.chemicals === 1 ? "" : "s"} · ` +
              `${data.detail.steps} step${data.detail.steps === 1 ? "" : "s"} · ` +
              `${data.detail.mixtures} mixture${data.detail.mixtures === 1 ? "" : "s"} recognised · ` +
              `${data.detail.unresolved} unresolved`
          )
        );
      } else if (event === "chemical") {
        state.chemicalRecords.push(data);
        log.enqueue(() => {
          const suffix = data.found ? `[CID ${data.cid}]` : "[no PubChem record]";
          addLogBlock([`Grounding ${data.name}…  ${suffix}`], data.found ? undefined : "is-gap");
        });
      } else if (event === "stage" && data.stage === "interactions" && data.status === "done") {
        log.enqueue(() => {
          const block = addLogBlock([`Checking ${data.detail.pairs_checked} co-present pairs against CAMEO…`]);
          appendLogSubline(block, `↳ ${data.detail.hazards_found} hazard${data.detail.hazards_found === 1 ? "" : "s"} found`);
        });
      } else if (event === "stage" && data.stage === "brief" && data.status === "done") {
        log.enqueue(() => {
          const block = addLogBlock(["Composing the brief…  (no model call)"]);
          appendLogSubline(block, `↳ ${data.detail.statements} statements · every one sourced`);
        });
      } else if (event === "error") {
        if (!data.recoverable) sawUnrecoverableError = true;
        log.enqueue(() => addLogBlock([`⚠ ${data.message}`], "is-error"));
      } else if (event === "result") {
        finalBrief = data;
      }
    }

    await log.whenDrained();

    if (finalBrief) {
      // Hold the finished stage log briefly before swapping it for the rendered brief.
      // Its last line — "Composing the brief…  (no model call)  ↳ N statements · every one
      // sourced" — is the one that shows the render is deterministic; without this pause it
      // renders and is hidden ~150ms later by the transition to "read", a flash the viewer
      // never registers. This is load-bearing for the demo narration of that exact step.
      await sleep(BRIEF_HOLD_MS);
      state.brief = finalBrief;
      if (state.extractionDetail && state.extractionDetail.chemicals === 0) {
        // Not an error, an explanation — routes through the 'failed' state's
        // bench-unlock behaviour but with distinct copy (see renderFailedPanel).
        state.failureKind = "no_chemicals";
        setState("failed");
      } else {
        setState(finalBrief.incomplete ? "incomplete" : "read");
      }
    } else {
      throw new Error(sawUnrecoverableError ? "The protocol could not be read." : "Stream ended unexpectedly.");
    }
  } catch (err) {
    // A deliberate abort (home button, mid-run) is not a failure — resetToEmpty() has
    // already put the screen back in "empty" and cleared the log; this run just needs to
    // stop quietly, not report an error over top of a screen the user already left.
    if (err instanceof DOMException && err.name === "AbortError") return;
    await log.whenDrained();
    state.lastError = err instanceof Error ? err.message : String(err);
    if (streamStarted) {
      // The stream produced real events before dying — freeze the log in
      // place rather than hiding it behind the full failed panel.
      freezeStageLogWithError();
    } else {
      state.failureKind = "error";
      setState("failed");
    }
  } finally {
    activeAbortController = null;
  }
}

// Home button (the brand mark) — resets the whole tool back to its first-load state.
// Works from any state, including mid-"reading" (aborts the in-flight run first, see
// startReading's activeAbortController). Every field state.js started with is put back,
// not just the visible state — a fresh read after a reset must behave exactly like the
// very first one, not carry anything over from whatever was on screen before.
function resetToEmpty() {
  activeAbortController?.abort();
  activeAbortController = null;

  protocolInput.value = "";
  state.protocolText = "";
  state.brief = null;
  state.extractionDetail = null;
  state.chemicalRecords = [];
  state.lastError = null;
  state.failureKind = "error";

  clearChildren(panels.stageLog);
  clearChildren(panels.receipt);
  clearChildren(panels.incompleteBanner);
  clearChildren(panels.briefOutput);
  clearChildren(panels.failedPanel);
  clearChildren(benchStepsEl);
  clearChildren(benchGutterEl);

  setState("empty");
  updateReadButtonEnabled();
  protocolInput.focus();
}

// The brief has to leave the screen. Runs on the native print event
// (button click OR the browser's own Ctrl+P / File>Print), so it fires no
// matter how printing was triggered.
function prepareForPrint() {
  // Nothing is ever lost, only folded — force every collapsible
  // group open for the printed copy, remembering prior state to restore.
  document.querySelectorAll(".chemical-block, .source-group, .gap-aggregate").forEach((el) => {
    el.dataset.wasOpen = el.open ? "1" : "0";
    el.open = true;
  });

  // On screen the no-data/could-not-check pairs collapse behind ONE disclosure toggle;
  // force-opening every group for print (above) would otherwise produce N full
  // duplicate-shaped cards — each with its own honest-omission sentence and citation
  // chip — right after it, relocating the same flood from screen to paper. Compact each
  // aggregate to the SAME grouping the
  // screen already computed: the first pair keeps its full sentence + chip as the
  // category's one representative citation; every other row collapses to the compact
  // "name + name" label render.js precomputed into data-compact-label at normal render
  // time (never re-derived here — this only decides which of two already-rendered
  // things to show). Each row's full HTML is snapshotted first so restoreAfterPrint can
  // put it back exactly; screen itself is never touched, since this only runs between
  // beforeprint/afterprint.
  document.querySelectorAll(".gap-aggregate").forEach((aggregate) => {
    const rows = aggregate.querySelectorAll(".gap-aggregate-row");
    rows.forEach((row, i) => {
      row.dataset.originalHtml = row.innerHTML;
      if (i === 0) return; // keep the first row of THIS aggregate full
      const label = row.dataset.compactLabel || "";
      clearChildren(row);
      const p = document.createElement("p");
      p.className = "mono gap-aggregate-compact-line";
      p.textContent = label;
      row.appendChild(p);
    });
  });

  // A printed chip can't be clicked — turn it into a numbered reference and
  // collect a references list, so the paper copy is still checkable, not just
  // decorative.
  const chips = Array.from(document.querySelectorAll(".chip[href]"));
  if (!chips.length) return;

  const lines = chips.map((chip, i) => {
    const n = i + 1;
    chip.dataset.printLabel = chip.textContent;
    chip.textContent = `[${n}]`;
    return `${n}. ${chip.dataset.printLabel} - ${chip.getAttribute("href")}`;
  });

  const list = document.createElement("div");
  list.id = "print-footnotes";
  const heading = document.createElement("p");
  heading.className = "eyebrow";
  heading.textContent = "SOURCES";
  list.appendChild(heading);
  for (const line of lines) {
    const p = document.createElement("p");
    p.className = "mono";
    p.textContent = line;
    list.appendChild(p);
  }
  document.querySelector(".sheet").appendChild(list);
}

function restoreAfterPrint() {
  document.querySelectorAll(".gap-aggregate-row[data-original-html]").forEach((row) => {
    row.innerHTML = row.dataset.originalHtml;
    delete row.dataset.originalHtml;
  });
  document.querySelectorAll(".chemical-block, .source-group, .gap-aggregate").forEach((el) => {
    el.open = el.dataset.wasOpen === "1";
    delete el.dataset.wasOpen;
  });
  document.querySelectorAll(".chip[data-print-label]").forEach((chip) => {
    chip.textContent = chip.dataset.printLabel;
    delete chip.dataset.printLabel;
  });
  const footnotes = document.getElementById("print-footnotes");
  if (footnotes) footnotes.remove();
}

window.addEventListener("beforeprint", prepareForPrint);
window.addEventListener("afterprint", restoreAfterPrint);
printBtn.addEventListener("click", () => window.print());

// The interaction-table panel — reachable from the no-data section's chip and
// from each hazard card's "View the full interaction table" link (render.js wires
// both to this same function). Fetched once and cached: it's static reference data,
// identical for every brief, not something that needs refetching per open.
let interactionMatrixCache = null;

async function openInteractionTablePanel() {
  if (!interactionMatrixCache) {
    try {
      const res = await fetch("/interaction-matrix");
      interactionMatrixCache = res.ok ? await res.json() : [];
    } catch {
      interactionMatrixCache = [];
    }
    renderInteractionTable(interactionTableBody, interactionMatrixCache);
  }
  interactionTableDialog.showModal();
}

interactionTableClose.addEventListener("click", () => interactionTableDialog.close());
// Native <dialog> click target is the dialog element itself when the click lands on
// its backdrop area (outside the rendered content box) — close on that, not on any
// click bubbling up from inside the panel's own content.
interactionTableDialog.addEventListener("click", (event) => {
  if (event.target === interactionTableDialog) interactionTableDialog.close();
});

protocolInput.addEventListener("input", updateReadButtonEnabled);
demoBtn.addEventListener("click", () => {
  protocolInput.value = DEMO_PROTOCOL;
  updateReadButtonEnabled();
  protocolInput.focus();
});
readBtn.addEventListener("click", startReading);
brandHomeBtn.addEventListener("click", resetToEmpty);

render();
