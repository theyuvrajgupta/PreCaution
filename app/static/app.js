// PreCaution — state machine (UI_Design_Spec.md §13) + stage log (§14) +
// brief rendering (§15, delegated to render.js).
//
// Five states, one explicit variable, no routing:
//   empty -> reading -> read
//                    -> incomplete   (grounding failed for >=1 chemical)
//                    -> failed       (pipeline could not run at all)
//
// Global invariant: the pasted protocol text is never lost — not on error,
// not on retry, not on a source-chip click.

import { parseSSEStream } from "./stream.js";
import { renderBrief } from "./render.js";
import { renderThread } from "./thread.js";

const DEMO_PROTOCOL = `1. Prepare piranha solution by slowly adding 30 mL of 30% hydrogen peroxide to 90 mL of concentrated sulfuric acid in a glass beaker inside the fume hood.
2. Submerge glass coverslips in the piranha solution for 15 minutes to strip organic residue.
3. Rinse the coverslips thoroughly with deionized water and dry under a stream of nitrogen.
4. Pour the spent piranha solution into the acid waste carboy.
5. Rinse the glassware used for the protein purification buffer (PBS with 0.02% sodium azide) and add that rinse to the same acid waste carboy.
`;

const MIN_LINE_MS = 150; // §14.2 rule 2: a line may linger, minimum 150ms visible

const appEl = document.getElementById("app");
const protocolInput = document.getElementById("protocol-input");
const demoBtn = document.getElementById("demo-btn");
const readBtn = document.getElementById("read-btn");
const benchControlsEl = document.getElementById("bench-controls");
const benchGutterEl = document.getElementById("bench-gutter");
const benchStepsEl = document.getElementById("bench-steps");
const printBtn = document.getElementById("print-btn");

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
  // 'error' (extraction/network failure) | 'no_chemicals' (§16.2: not an error,
  // an explanation) — which flavor of the 'failed' state to render.
  failureKind: "error",
};

function setState(next) {
  state.current = next;
  appEl.dataset.state = next;
  // The bench pane's sticky thread is anchored within a container as tall as the
  // (usually much longer) paper pane, so it can track scroll across the whole brief
  // (§19.4). But that means whatever scroll position accumulated while the stage log
  // was streaming carries straight into the freshly-rendered brief — the thread would
  // open mid-stuck, below the fold, instead of at the top. A brief that just finished
  // loading should always be seen from its own top.
  if (next === "read" || next === "incomplete") {
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
  // pane swaps from the raw pasted text to structured step rows the thread (§6.1)
  // can attach to. The pasted text itself is never discarded — just hidden.
  protocolInput.hidden = showThread;
  benchControlsEl.hidden = showThread;
  benchStepsEl.hidden = !showThread;
  if (!showThread) clearChildren(benchGutterEl);
}

function render() {
  // §17: only meaningful once there's a brief on the page.
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
  panels.receipt.textContent =
    `Protocol read — ${state.extractionDetail.chemicals} chemicals · ` +
    `${state.extractionDetail.steps} steps · ${state.brief.statements.length} statements`;
}

// Full exact copy per UI_Design_Spec.md §16.1 — this is intrinsic to what
// distinguishes the Incomplete state from Read, so it's built now rather
// than deferred; positioning "above the scan layer" is already true (this
// panel renders before brief-output in DOM order) — non-dismissibility is
// inherent (no close control is offered).
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

// Covers UI_Design_Spec.md §16.2's "Extraction call fails (502)" case (and any
// failure before the stream produced a single real event — see §D's freeze-
// in-place handling in startReading for the mid-stream case, which does NOT
// route through here) plus "No chemicals identified" — not an error, an
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
    detail.textContent = "PreCaution reads chemical protocols; this doesn't look like one.";

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

// --- Stage log: a paced queue over real events (§14.2) ---------------------
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

// §16.2 "Network dies mid-stream": freeze the log in place — turn the last
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

  try {
    const res = await fetch("/brief/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ protocol_text: text }),
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
            `↳ ${data.detail.chemicals} chemicals · ${data.detail.steps} steps · ` +
              `${data.detail.mixtures} mixture(s) recognised · ${data.detail.unresolved} unresolved`
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
          appendLogSubline(block, `↳ ${data.detail.hazards_found} hazard(s) found`);
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
      state.brief = finalBrief;
      if (state.extractionDetail && state.extractionDetail.chemicals === 0) {
        // §16.2: not an error, an explanation — routes through the 'failed' state's
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
    await log.whenDrained();
    state.lastError = err instanceof Error ? err.message : String(err);
    if (streamStarted) {
      // §16.2: the stream produced real events before dying — freeze the log in
      // place rather than hiding it behind the full failed panel.
      freezeStageLogWithError();
    } else {
      state.failureKind = "error";
      setState("failed");
    }
  }
}

// §17: the brief has to leave the screen. Runs on the native print event
// (button click OR the browser's own Ctrl+P / File>Print), so it fires no
// matter how printing was triggered.
function prepareForPrint() {
  // §20/§17: nothing is ever lost, only folded — force every collapsible
  // group open for the printed copy, remembering prior state to restore.
  document.querySelectorAll(".chemical-block, .source-group, .gap-aggregate").forEach((el) => {
    el.dataset.wasOpen = el.open ? "1" : "0";
    el.open = true;
  });

  // §21 print follow-up: on screen the no-data/could-not-check pairs collapse behind
  // ONE disclosure toggle, but force-opening every group for print (above) used to mean
  // N full duplicate-shaped cards — each with its own honest-omission sentence and its
  // own citation chip — right after it, exactly the flood §21 exists to prevent, just
  // relocated from screen to paper. Compact each aggregate to the SAME grouping the
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

  // §6.2/§17: a printed chip can't be clicked — turn it into a numbered
  // reference and collect a references list, so the paper copy is still
  // checkable, not just decorative.
  const chips = Array.from(document.querySelectorAll(".chip[href]"));
  if (!chips.length) return;

  const lines = chips.map((chip, i) => {
    const n = i + 1;
    chip.dataset.printLabel = chip.textContent;
    chip.textContent = `[${n}]`;
    return `${n}. ${chip.dataset.printLabel} — ${chip.getAttribute("href")}`;
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

protocolInput.addEventListener("input", updateReadButtonEnabled);
demoBtn.addEventListener("click", () => {
  protocolInput.value = DEMO_PROTOCOL;
  updateReadButtonEnabled();
  protocolInput.focus();
});
readBtn.addEventListener("click", startReading);

render();
