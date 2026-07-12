// The carryover thread (UI_Design_Spec.md §6.1) and the unverified marker
// (§6.1a) — the two elements explicitly marked "never cut."
//
// Renders the bench pane's step-by-step view (replacing the free-text
// textarea once a Brief exists) plus a 44px gutter overlay: a token where a
// chemical is added, a quiet line while it carries over, a red diamond at
// hazard onset, and a continuous hot line while that hazard persists — never
// one diamond per step, because the hazard persists, it does not recur.
//
// A textarea can't expose per-line layout, so once a Brief is available the
// bench pane switches from the raw pasted text to these structured rows —
// each one IS a `step_context` BriefStatement (always `unverified: true`,
// text sourced from Claude's read of the protocol, not a lookup), which is
// why the unverified marker lives here rather than duplicated in the brief.

import { cap } from "./render.js";

const UNVERIFIED_TIP = "Claude read this from the protocol text. It was not looked up. Verify the step attribution.";

// Fix 2 (pre-recording polish pass): the row already carries the full tooltip above
// (UNVERIFIED_TIP, revealed on hover/focus of the whole row via aria-describedby — the
// UI_Design_Spec.md §6.1a/§9-locked copy, untouched). But the "ᴜɴᴠ" glyph itself was
// aria-hidden and carried no title of its own — a cold reader has no way to decode three
// unexplained letters without first discovering the row is interactive. This gives the
// marker glyph its own independent title/aria-label so hovering or inspecting it directly
// explains itself, without altering the existing row-level tooltip or its locked wording.
const UNV_MARKER_TIP =
  "Unverified: Claude's read of the protocol text, not a database lookup. The only claim in this brief marked this way.";

// Fix 3: a compact, always-visible key for the gutter's visual language — the thread,
// diamonds, and chemical-id tokens encode real meaning (§6.1) but were previously
// explained only in the design spec, never on the page itself.
const GUTTER_LEGEND =
  "Gutter: line = chemical carried across steps · diamond = hazard onset · c1–c6 = chemical tokens.";

function clearChildren(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
}

// §22: every step row is a step_context statement (always unverified=true), but
// marking all of them made the marker wallpaper — when everything is flagged,
// nothing is. Reserve the visible dotted-underline + ᴜɴᴠ marker for the one
// claim that's genuinely load-bearing: a step where a NEW hazard forms because
// something carried over from an earlier step (not freshly added there) — e.g.
// "the spent piranha is still in the carboy." That specific attribution is
// exactly what rests on Claude's read of the protocol, not a lookup (§3.3).
// Every other row gets a quiet section-level note instead (see buildStepRow).
function computeLoadBearingSteps(steps, onsetAt) {
  const byNumber = new Map(steps.map((s) => [s.number, s]));
  const loadBearing = new Set();
  for (const [stepNum, hazardsHere] of onsetAt) {
    const step = byNumber.get(stepNum);
    if (!step) continue;
    for (const s of hazardsHere) {
      const involved = new Set(s.chemical_ids);
      if (step.chemicals.some((c) => involved.has(c.chemical_id) && c.origin !== "added")) {
        loadBearing.add(stepNum);
      }
    }
  }
  return loadBearing;
}

function buildStepRow(step, isLoadBearing, checkFlags) {
  const row = document.createElement("div");
  row.className = "bench-step rise-in";
  row.dataset.step = String(step.number);

  const text = document.createElement("p");
  text.className = "bench-step-text" + (isLoadBearing ? " unverified" : "");
  text.tabIndex = 0;

  const num = document.createElement("span");
  num.className = "mono bench-step-number";
  num.textContent = `${step.number}.`;

  text.append(num, ` ${step.text} `);

  if (isLoadBearing) {
    const tipId = `unv-tip-${step.number}`;
    const marker = document.createElement("span");
    marker.className = "mono unverified-marker";
    marker.textContent = "ᴜɴᴠ";
    // Independently decodable on its own — not aria-hidden, carries its own title (mouse
    // hover) and aria-label (screen readers get the label text instead of "u n v"). The
    // row's own fuller tooltip (below) still exists unchanged for keyboard/focus users.
    marker.title = UNV_MARKER_TIP;
    marker.setAttribute("aria-label", UNV_MARKER_TIP);
    text.appendChild(marker);
    text.setAttribute("aria-describedby", tipId);

    const tip = document.createElement("span");
    tip.id = tipId;
    tip.className = "unverified-tip";
    tip.setAttribute("role", "tooltip");
    tip.hidden = true;
    tip.textContent = UNVERIFIED_TIP;
    row.append(text, tip);

    const show = () => {
      tip.hidden = false;
    };
    const hide = () => {
      tip.hidden = true;
    };
    text.addEventListener("mouseenter", show);
    text.addEventListener("mouseleave", hide);
    text.addEventListener("focus", show);
    text.addEventListener("blur", hide);
    text.addEventListener("keydown", (e) => {
      if (e.key === "Escape") hide();
    });
  } else {
    row.appendChild(text);
  }

  if (step.vessel) {
    const vessel = document.createElement("p");
    vessel.className = "mono bench-step-vessel";
    vessel.textContent = `⌐ ${cap(`vessel: ${step.vessel}`)}`;
    row.appendChild(vessel);
  }

  // Omission-detection (Build_Spec.md's omission-detection phase, §2e): CHECK is a
  // SIBLING to the UNV marker above, never a reuse of it — UNV's scarcity (the one
  // load-bearing carryover attribution) is itself load-bearing for the demo narration,
  // and reusing it here would dilute that. Same family (quiet, secondary, clearly-
  // Claude's-read), same position/weight/contrast as the Vessel line just above (not
  // lighter — the global UI rule bans washed-out grey, and --muted-bench is already the
  // "full readable contrast, secondary weight" token this pane uses for exactly this
  // register). The CHECK word itself borrows --caution-bench (already used for GHS
  // Warning signal words) so it reads as "worth a look," not danger-red and not a plain
  // grey word indistinguishable from body text — reusing an existing token, not a new
  // colour. Never fires on a step that already carries an interaction hazard (2d) —
  // enforced in app/brief.py before this ever sees the flag, not here.
  for (const flag of checkFlags) {
    const check = document.createElement("p");
    check.className = "bench-step-check";
    const marker = document.createElement("span");
    marker.className = "mono step-check-marker";
    marker.textContent = "CHECK";
    check.append(marker, ` · ${flag.text}`);
    row.appendChild(check);
  }

  return row;
}

// A vessel changed FROM the previous step — only then does the tick earn its
// place; an unchanging vessel repeated on every row would be noise, not signal.
function pruneUnchangedVesselTicks(stepsEl, steps) {
  let prevVessel = null;
  for (const step of steps) {
    const row = stepsEl.querySelector(`.bench-step[data-step="${step.number}"]`);
    const vesselLine = row && row.querySelector(".bench-step-vessel");
    if (vesselLine) {
      if (step.vessel === prevVessel) vesselLine.remove();
      else prevVessel = step.vessel;
    }
  }
}

// Omission-detection (Build_Spec.md §2e): stepNumber -> [flag, ...]. Brief.statements
// mixes every kind together (hazard_identity, interaction_hazard, omission_flag, ...) —
// this is the one place that pulls just the omission_flag kind out for the bench pane,
// mirroring computeOnsetAndHotSegments's shape immediately below for the hazard kind.
function computeCheckFlagsByStep(statements) {
  const byStep = new Map();
  for (const s of statements) {
    if (s.kind !== "omission_flag") continue;
    for (const stepNum of s.step_numbers) {
      if (!byStep.has(stepNum)) byStep.set(stepNum, []);
      byStep.get(stepNum).push(s);
    }
  }
  return byStep;
}

// onset: stepNumber -> [statement, ...] (where a pair NEWLY returns hazard_found).
// hotSegments: "n-n+1" pairs of adjacent steps the hazard persists across.
// Shared by buildStepRow's load-bearing check and drawGutter's diamonds/hot line —
// computed once per render, not duplicated.
function computeOnsetAndHotSegments(statements) {
  const hazards = statements.filter((s) => s.kind === "interaction_hazard" && s.step_numbers.length);
  const onsetAt = new Map();
  const hotSegments = new Set();
  for (const s of hazards) {
    const nums = [...s.step_numbers].sort((a, b) => a - b);
    const onset = nums[0];
    if (!onsetAt.has(onset)) onsetAt.set(onset, []);
    onsetAt.get(onset).push(s);
    for (let i = 0; i < nums.length - 1; i++) {
      if (nums[i + 1] === nums[i] + 1) hotSegments.add(`${nums[i]}-${nums[i + 1]}`);
    }
  }
  return { onsetAt, hotSegments };
}

function drawGutter(gutterEl, stepsEl, steps, onsetAt, hotSegments) {
  clearChildren(gutterEl);

  // Marks are positioned absolute relative to gutterEl itself (its own top edge,
  // not the pane's) — gutterEl and stepsEl are now flex siblings inside the same
  // sticky wrapper (#bench-sticky, §19.4), so this offset stays correct whether
  // the wrapper is in normal flow or currently pinned: both move by the same
  // viewport delta together, so the difference between their rects never changes.
  const paneTop = gutterEl.getBoundingClientRect().top;
  const centerByStep = new Map();
  for (const row of stepsEl.querySelectorAll(".bench-step")) {
    const rect = row.getBoundingClientRect();
    centerByStep.set(Number(row.dataset.step), rect.top - paneTop + rect.height / 2);
  }

  const byNumber = new Map(steps.map((s) => [s.number, s]));

  // Mirrors app/interactions.py::_find_added_step exactly (same rule, same shape,
  // ported rather than approximated): the EARLIEST step where this chemical's
  // origin was "added" is its true point of introduction. A LATER step re-tagging
  // it "added" — e.g. the spent piranha poured from the beaker into the waste
  // carboy — is a vessel transition, not a second onset. Kept as a literal port
  // so a future change to the Python rule has an obvious JS counterpart to update
  // in lockstep, rather than two heuristics that happen to agree today.
  const findEarliestAddedStep = (chemicalId) => {
    for (const step of steps) {
      if (step.chemicals.some((c) => c.chemical_id === chemicalId && c.origin === "added")) {
        return step.number;
      }
    }
    return null;
  };

  // Base spine: only where a chemical genuinely carried forward — never implied
  // continuity the data doesn't actually assert. "residual"/"carried_over" both
  // count, same as app/interactions.py's own co-presence check (which is what
  // step_numbers on a hazard statement is built from — it has no vessel-name gate
  // at all, so a pair keeps returning hazard_found straight through a transfer).
  // A step that re-tags the chemical "added" ALSO counts, unless this is that
  // chemical's true first onset per findEarliestAddedStep above — otherwise the
  // thread draws a gap the hazard data doesn't assert (confirmed live: this used
  // to break the spine at the step 3->4 vessel transfer on the locked demo, even
  // though the piranha hazard's own step_numbers=[1,2,3,4,5] is fully contiguous).
  const carriesForward = (fromNum, toNum) => {
    const fromStep = byNumber.get(fromNum);
    const toStep = byNumber.get(toNum);
    if (!fromStep || !toStep) return false;
    const fromIds = new Set(fromStep.chemicals.map((c) => c.chemical_id));
    return toStep.chemicals.some((c) => {
      if (!fromIds.has(c.chemical_id)) return false;
      if (c.origin !== "added") return true;
      return findEarliestAddedStep(c.chemical_id) !== toNum;
    });
  };

  const sortedNums = [...centerByStep.keys()].sort((a, b) => a - b);
  for (let i = 0; i < sortedNums.length - 1; i++) {
    const a = sortedNums[i];
    const b = sortedNums[i + 1];
    if (!carriesForward(a, b)) continue;
    const hot = hotSegments.has(`${a}-${b}`);
    const line = document.createElement("div");
    line.className = "gutter-line" + (hot ? " gutter-line-hot" : "");
    line.style.top = `${centerByStep.get(a)}px`;
    line.style.height = `${centerByStep.get(b) - centerByStep.get(a)}px`;
    gutterEl.appendChild(line);
  }

  // Tokens: chemicals entering ("added") this step. Sorted by chemical_id — extraction's
  // chemicals_present order is Claude's read of the protocol text and isn't guaranteed
  // stable across runs, which made the gutter token order flip between otherwise-identical
  // renders (cosmetic, but the demo is recorded exactly once).
  for (const step of steps) {
    const added = step.chemicals
      .filter((c) => c.origin === "added")
      .slice()
      .sort((a, b) => a.chemical_id.localeCompare(b.chemical_id));
    const center = centerByStep.get(step.number);
    if (center === undefined) continue;
    added.forEach((c, i) => {
      const token = document.createElement("div");
      token.className = "gutter-token";
      token.style.top = `${center + (i - (added.length - 1) / 2) * 12}px`;
      const dot = document.createElement("span");
      dot.className = "gutter-token-dot";
      const label = document.createElement("span");
      label.className = "mono gutter-token-label";
      label.textContent = c.chemical_id;
      token.append(dot, label);
      gutterEl.appendChild(token);
    });
  }

  // Diamonds: hazard onset — one per pair that newly returns hazard_found, never
  // one per step it persists through. The FIRST diamond at a step sits at exactly
  // `center` — the same coordinate the line segments above/below terminate at
  // (line.style.top / centerByStep.get(...) have no offset) — so the thread meets
  // the diamond cleanly at both ends instead of stopping short with a gap (fix,
  // 2026-07-13: a stray "+18" base offset here had every diamond floating 18px
  // below the row centre the line actually connects to). A SECOND diamond at the
  // same step (two pairs onsetting on the same row) still needs real separation
  // from the first — a rotated 13px square's diagonal is ~18.4px — so only i>0
  // gets pushed further down, never the first.
  for (const [stepNum, hazardsHere] of onsetAt) {
    const center = centerByStep.get(stepNum);
    if (center === undefined) continue;
    hazardsHere.forEach((s, i) => {
      const diamond = document.createElement("div");
      diamond.className = "gutter-diamond";
      diamond.style.top = `${center + i * 19}px`;
      gutterEl.appendChild(diamond);
    });
  }
}

// Decorative in the accessibility tree — everything the thread encodes (which
// chemical entered where, which pair became hazardous, how long it persisted)
// is also stated in text elsewhere in the brief. Never encode meaning only here.
export function renderThread(gutterEl, stepsEl, { steps, statements }) {
  clearChildren(stepsEl);
  clearChildren(gutterEl);
  if (!steps || !steps.length) return;

  const { onsetAt, hotSegments } = computeOnsetAndHotSegments(statements);
  const loadBearingSteps = computeLoadBearingSteps(steps, onsetAt);
  const checkFlagsByStep = computeCheckFlagsByStep(statements);

  // §22: one quiet section-level note instead of marking every row — the
  // per-row marker is reserved for the load-bearing claim(s) only.
  // Phase 3a: names what the reading actually did (resolving a named mixture into its
  // reagents, tracking a chemical into a shared vessel across steps) rather than just
  // saying "Claude read this" in the abstract — this structural read is Claude's one
  // high-leverage job in the pipeline, and it was invisible in the UI before this.
  // Trim pass (2026-07-13): three lines of prose before step 1 even starts was too much
  // of a toll-gate — same two concrete facts (mixture resolution, vessel tracking), one
  // sentence instead of a wind-up clause plus a payoff clause.
  const note = document.createElement("p");
  note.className = "bench-steps-note";
  note.textContent =
    "Claude resolved this protocol into its reagents and tracked each chemical's vessel across steps. " +
    "A structural read, not a lookup.";
  stepsEl.appendChild(note);

  // Fix 3: subtle, one line, placed once — not repeated per row, not a boxed panel.
  const legend = document.createElement("p");
  legend.className = "mono bench-steps-legend";
  legend.textContent = GUTTER_LEGEND;
  stepsEl.appendChild(legend);

  for (const step of steps) {
    const checkFlags = checkFlagsByStep.get(step.number) ?? [];
    stepsEl.appendChild(buildStepRow(step, loadBearingSteps.has(step.number), checkFlags));
  }
  pruneUnchangedVesselTicks(stepsEl, steps);
  drawGutter(gutterEl, stepsEl, steps, onsetAt, hotSegments);
}
