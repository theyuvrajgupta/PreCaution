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

const UNVERIFIED_TIP = "Claude read this from the protocol text. It was not looked up. Verify the step attribution.";

function clearChildren(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
}

function buildStepRow(step) {
  const row = document.createElement("div");
  row.className = "bench-step rise-in";
  row.dataset.step = String(step.number);

  const text = document.createElement("p");
  text.className = "bench-step-text unverified";
  text.tabIndex = 0;

  const num = document.createElement("span");
  num.className = "mono bench-step-number";
  num.textContent = `${step.number}.`;

  const tipId = `unv-tip-${step.number}`;
  const marker = document.createElement("span");
  marker.className = "mono unverified-marker";
  marker.textContent = "ᴜɴᴠ";
  marker.setAttribute("aria-hidden", "true");

  text.append(num, ` ${step.text} `, marker);
  text.setAttribute("aria-describedby", tipId);

  const tip = document.createElement("span");
  tip.id = tipId;
  tip.className = "unverified-tip";
  tip.setAttribute("role", "tooltip");
  tip.hidden = true;
  tip.textContent = UNVERIFIED_TIP;

  row.append(text, tip);

  if (step.vessel) {
    const vessel = document.createElement("p");
    vessel.className = "mono bench-step-vessel";
    vessel.textContent = `⌐ vessel: ${step.vessel}`;
    row.appendChild(vessel);
  }

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

function drawGutter(paneEl, gutterEl, stepsEl, steps, statements) {
  clearChildren(gutterEl);
  const hazards = statements.filter((s) => s.kind === "interaction_hazard" && s.step_numbers.length);

  const onsetAt = new Map(); // stepNumber -> [statement, ...]
  const hotSegments = new Set(); // "n-n+1"
  for (const s of hazards) {
    const nums = [...s.step_numbers].sort((a, b) => a - b);
    const onset = nums[0];
    if (!onsetAt.has(onset)) onsetAt.set(onset, []);
    onsetAt.get(onset).push(s);
    for (let i = 0; i < nums.length - 1; i++) {
      if (nums[i + 1] === nums[i] + 1) hotSegments.add(`${nums[i]}-${nums[i + 1]}`);
    }
  }

  const paneTop = paneEl.getBoundingClientRect().top;
  const centerByStep = new Map();
  for (const row of stepsEl.querySelectorAll(".bench-step")) {
    const rect = row.getBoundingClientRect();
    centerByStep.set(Number(row.dataset.step), rect.top - paneTop + rect.height / 2);
  }

  const byNumber = new Map(steps.map((s) => [s.number, s]));

  // Base spine: only where a chemical genuinely carried forward — never implied
  // continuity the data doesn't actually assert. "residual" counts as still
  // present, same as "carried_over": app/interactions.py's co-presence check
  // (which is what step_numbers on a hazard statement is built from) doesn't
  // distinguish them either, so the thread must not draw a gap the hazard
  // statement itself doesn't assert — that would visually contradict its own
  // "Steps N-M" claim. Only "added" (a fresh token, not a continuation) is excluded.
  const carriesForward = (fromNum, toNum) => {
    const fromStep = byNumber.get(fromNum);
    const toStep = byNumber.get(toNum);
    if (!fromStep || !toStep) return false;
    const fromIds = new Set(fromStep.chemicals.map((c) => c.chemical_id));
    return toStep.chemicals.some((c) => c.origin !== "added" && fromIds.has(c.chemical_id));
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

  // Tokens: chemicals entering ("added") this step.
  for (const step of steps) {
    const added = step.chemicals.filter((c) => c.origin === "added");
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
  // one per step it persists through.
  for (const [stepNum, hazardsHere] of onsetAt) {
    const center = centerByStep.get(stepNum);
    if (center === undefined) continue;
    hazardsHere.forEach((s, i) => {
      const diamond = document.createElement("div");
      diamond.className = "gutter-diamond";
      diamond.style.top = `${center + 15 + i * 13}px`;
      gutterEl.appendChild(diamond);
    });
  }
}

// Decorative in the accessibility tree — everything the thread encodes (which
// chemical entered where, which pair became hazardous, how long it persisted)
// is also stated in text elsewhere in the brief. Never encode meaning only here.
export function renderThread(paneEl, gutterEl, stepsEl, { steps, statements }) {
  clearChildren(stepsEl);
  clearChildren(gutterEl);
  if (!steps || !steps.length) return;

  for (const step of steps) {
    stepsEl.appendChild(buildStepRow(step));
  }
  pruneUnchangedVesselTicks(stepsEl, steps);
  drawGutter(paneEl, gutterEl, stepsEl, steps, statements);
}
