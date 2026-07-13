// Renders a Brief into the DOM. Pure rendering — no state machine, no fetch,
// no logic beyond "what does this data look like." Reading hierarchy order:
// scan layer -> interaction hazards -> per-chemical controls (glove notice
// attached where PPE is shown) -> gaps rendered inline with the chemical they
// concern, not swept into a footer.
//
// Known scope limit, an accepted fallback: real GHS
// pictogram SVGs are not wired through the data model yet (BriefStatement
// never carries ghs.pictogram_urls) — hazard_identity text already includes
// the pictogram labels as words, which is the sanctioned fallback.

const SAFETY_NOTE_KINDS = ["ppe", "first_aid", "disposal", "storage"];

// First-letter capitalization only — chemical names are correctly lowercase
// mid-sentence (they're not proper nouns); this exists only for the collapsed-row
// name, which leads its own block and needs a capital when it starts a sentence
// (mirrors app/brief.py's _cap()). Never String.prototype
// equivalent of .capitalize() — this leaves everything after the first character
// untouched.
export function cap(text) {
  return text ? text[0].toUpperCase() + text.slice(1) : text;
}

// Display-layer sanitization of a chemical name. Multi-part reagents
// occasionally leak Stage-1 scratch-work into the name field —
// "Phenol (25 parts (of 25:24:1 mixture))", "Ethanol (cold (temperature
// qualifier, not concentration))" — where a nested parenthetical or a reasoning
// note is really the model thinking out loud, not part of the name. Flatten those
// away here, at render time only: the underlying data is untouched, and the durable
// fix is Stage-1 prompt discipline, not this. Deliberately conservative so it never
// mangles a legitimate name: it only drops a parenthetical that is itself nested, or
// one that carries an explicit reasoning marker ("qualifier", ", not ..."). A plain
// oxidation state like "iron(III) chloride" or a lone "(anhydrous)" is left alone.
export function sanitizeName(name) {
  if (!name) return name;
  let out = name;
  let prev;
  do {
    prev = out;
    // An outer parenthetical containing another parenthetical is scratch-work — drop it whole.
    out = out.replace(/\s*\([^()]*\([^()]*\)[^()]*\)/g, "");
  } while (out !== prev);
  // A lingering reasoning-style qualifier that leaked without nesting.
  out = out.replace(/\s*\([^()]*(?:qualifier|,\s*not\b)[^()]*\)/gi, "");
  return out.trim();
}

// Per-chemical safety-note excerpts are grouped by source/audience, not
// dumped flat — a per-chemical wall of 50+ statements is the thing this
// section exists to fix. Render order locked: NIOSH -> ERG ->
// GHS classification -> P-codes -> other (anything not NIOSH/ERG-attributed,
// e.g. HSDB/ICSC — not named in the spec, placed last, open since it's
// substantive safety content, not audience-mismatched noise).
const AUDIENCE_GROUPS = [
  { key: "niosh", title: "NIOSH Pocket Guide", subtitle: "occupational exposure guidance", openByDefault: true },
  { key: "erg", title: "ERG", subtitle: "emergency response, transport incidents", openByDefault: false },
];

const GAP_HEADING = {
  no_data: "NO AUTHORITATIVE DATA",
  interaction_no_data: "NO AUTHORITATIVE DATA",
  grounding_incomplete: "GROUNDING INCOMPLETE",
  unresolved_mention: "UNRESOLVED MENTION",
  // Not a gap — a real CAMEO classification (a "free grounding win", not an absence of
  // data) — but reuses the gap-card's quiet, non-hazard visual treatment since it's
  // still non-hazard content, distinct heading so it never reads as missing data.
  reactive_classification: "REACTIVE-GROUP CLASSIFICATION",
  // Also not a gap — a correct, expected absence (proteins aren't small molecules)
  // reusing the same quiet visual treatment, distinct heading so it never reads as a
  // resolution failure the way a genuine "no PubChem record" does.
  not_small_molecule: "PROTEIN: NOT A SMALL MOLECULE",
};

function clearChildren(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
}

// Same accessible-tooltip pattern already proven in thread.js's unverified marker
// (aria-describedby, reveal on hover/focus, Escape to dismiss) — generalised here for
// scan-layer jargon a biologist reader can't be assumed to already know. Returns the
// tooltip element; caller appends it near `el`.
let infoTipCounter = 0;
function attachTooltip(el, tipText) {
  const tipId = `info-tip-${++infoTipCounter}`;
  const tip = document.createElement("span");
  tip.id = tipId;
  tip.className = "info-tip";
  tip.setAttribute("role", "tooltip");
  tip.hidden = true;
  tip.textContent = tipText;

  el.classList.add("jargon-term");
  el.tabIndex = 0;
  el.setAttribute("aria-describedby", tipId);
  const show = () => {
    tip.hidden = false;
  };
  const hide = () => {
    tip.hidden = true;
  };
  el.addEventListener("mouseenter", show);
  el.addEventListener("mouseleave", hide);
  el.addEventListener("focus", show);
  el.addEventListener("blur", hide);
  el.addEventListener("keydown", (e) => {
    if (e.key === "Escape") hide();
  });
  return tip;
}

// A bare "CID 784" chip assumes the reader already knows what a CID is. Prefixed
// display-only, at render time — the underlying source_ref data is untouched, and this
// only ever matches the bare-CID shape, never a CAMEO/EU CLP/other source_ref (those are
// sourced content and must render exactly as composed).
function chipLabel(sourceRef) {
  return /^CID \d+$/.test(sourceRef) ? `PubChem ${sourceRef}` : sourceRef;
}

// "1, 2, and 3" — used by the scan-layer locator line below. No existing
// equivalent in this file (app/brief.py has its own
// Python-side _join_with_or for a different purpose; this is a small, self-contained JS
// counterpart, "and" rather than "or").
function joinWithAnd(items) {
  if (items.length === 1) return String(items[0]);
  if (items.length === 2) return `${items[0]} and ${items[1]}`;
  return `${items.slice(0, -1).join(", ")}, and ${items[items.length - 1]}`;
}

function formatStepRange(numbers) {
  if (!numbers || !numbers.length) return "";
  if (numbers.length === 1) return `Step ${numbers[0]}`;
  const sorted = [...numbers].sort((a, b) => a - b);
  const contiguous = sorted.every((n, i) => i === 0 || n === sorted[i - 1] + 1);
  return contiguous ? `Steps ${sorted[0]}–${sorted[sorted.length - 1]}` : `Steps ${sorted.join(", ")}`;
}

function renderChip(statement) {
  const el = statement.source_url ? document.createElement("a") : document.createElement("span");
  el.className = "chip mono";
  el.textContent = chipLabel(statement.source_ref);
  if (statement.source_url) {
    el.href = statement.source_url;
    el.target = "_blank";
    el.rel = "noopener";
  }
  return el;
}

// The "PreCaution interaction table" reference is a real button-chip, not an
// inert <span> — it looks like every other clickable chip, so it must actually
// do something. It's the one citation this app CAN make genuinely clickable
// in-app (the table is local data, not a remote URL), so it gets its own
// button-chip, wired by
// the caller (app.js owns opening/fetching the panel; this module only
// builds DOM — see the file header's "no fetch, no logic" rule).
function renderInteractionTableChip(onOpenInteractionTable) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "chip chip-action mono";
  btn.textContent = "preCaution interaction table";
  btn.addEventListener("click", () => onOpenInteractionTable());
  return btn;
}

function renderHazardCard(statement, onOpenInteractionTable) {
  const card = document.createElement("div");
  card.className = "card hazard-card rise-in";

  const signal = document.createElement("p");
  signal.className = "signal card-signal";
  signal.textContent = "DANGER";

  const steps = document.createElement("p");
  steps.className = "mono card-steps";
  steps.textContent = formatStepRange(statement.step_numbers);

  card.append(signal, steps);

  // lead_in is authored (which chemicals, combined directly or one carried over) —
  // rendered separately, above the quote, with NO chip. Never concatenated into the
  // chipped block; see app/interaction_matrix.py.
  if (statement.lead_in) {
    const leadIn = document.createElement("p");
    leadIn.className = "card-lead-in";
    leadIn.textContent = statement.lead_in;
    card.appendChild(leadIn);
  }

  // The chipped block: ONLY the CAMEO quote, and its chip, together.
  const quoteBody = document.createElement("p");
  quoteBody.className = "card-body card-quote";
  quoteBody.textContent = statement.text;
  card.append(quoteBody, renderChip(statement));

  // Authored nominal note (e.g. a common name) — separate line, no chip, never a
  // hazard claim (enforced by tests/test_interaction_matrix.py).
  if (statement.hazard_note) {
    const note = document.createElement("p");
    note.className = "card-note";
    note.textContent = statement.hazard_note;
    card.appendChild(note);
  }

  // Second entry point into the interaction-table panel (the first is the
  // no-data section's chip below) — a judge reading a real finding should be
  // able to reach the same small table this verdict was looked up in.
  const viewTableBtn = document.createElement("button");
  viewTableBtn.type = "button";
  viewTableBtn.className = "mono interaction-table-link";
  viewTableBtn.textContent = "View the full interaction table";
  viewTableBtn.addEventListener("click", () => onOpenInteractionTable());
  card.appendChild(viewTableBtn);

  return card;
}

function renderGapCard(statement) {
  const card = document.createElement("div");
  card.className = "card gap-card rise-in";

  const heading = document.createElement("p");
  heading.className = "signal gap-heading";
  heading.textContent = GAP_HEADING[statement.kind] || "NO DATA";

  const body = document.createElement("p");
  body.className = "card-body";
  body.textContent = statement.text;

  card.append(heading, body, renderChip(statement));
  return card;
}

function renderGloveNotice(statement) {
  const card = document.createElement("div");
  card.className = "card gap-card glove-notice rise-in";

  const heading = document.createElement("p");
  heading.className = "signal gap-heading";
  heading.textContent = "PPE DATA STOPS HERE";

  const body = document.createElement("p");
  body.className = "card-body";
  body.textContent = statement.text;

  card.append(heading, body, renderChip(statement));
  return card;
}

function renderControlLine(statement) {
  const p = document.createElement("p");
  p.className = "control-line rise-in";
  const text = document.createElement("span");
  text.textContent = statement.text + " ";
  p.append(text, renderChip(statement));
  return p;
}

function renderScanLayer(brief, chemicalRecords, extractionDetail) {
  const hazards = brief.statements.filter((s) => s.kind === "interaction_hazard");
  const limitations = brief.statements.filter((s) => s.kind === "limitation_disclosure").length;

  // Item 4: "0 chemicals without hazard data" (counting !found) contradicted the body,
  // which correctly shows gap cards for chemicals that WERE found (a real PubChem
  // record, a real CID) but had specific sections missing — water, nitrogen, PBS were
  // all `found`. Say the two distinct things separately.
  //
  // "grounded" is counted directly from chemicalRecords, not as total - failedGrounding:
  // the latter silently counts a clean "no PubChem record" chemical (found=False,
  // grounding_error=None) as "grounded" while its own per-chemical row shows a gap card.
  // Five mutually-exclusive states, always summing to extractionDetail.chemicals —
  // grounded / fallback-sourced (real hazard data, just not from PubChem) / no record /
  // protein (expected non-small-molecule absence) / failed grounding (network status
  // unknown).
  const grounded = chemicalRecords.filter((c) => c.found).length;
  const failedGrounding = chemicalRecords.filter((c) => c.grounding_error).length;
  const fallbackSourced = chemicalRecords.filter((c) => !c.found && c.fallback_source).length;
  const notSmallMolecule = chemicalRecords.filter((c) => !c.found && !c.fallback_source && c.not_small_molecule).length;
  const noRecord = chemicalRecords.filter(
    (c) => !c.found && !c.fallback_source && !c.not_small_molecule && !c.grounding_error
  ).length;
  const noGhs = chemicalRecords.filter((c) => (c.missing_sections || []).includes("GHS Classification")).length;

  const el = document.createElement("div");
  el.className = "scan-layer rise-in";

  // "OVERALL ASSESSMENT" invited exactly the misreading a bare DANGER headline already
  // risked — a verdict on the whole
  // protocol, not a pointer to specific findings. "AT A GLANCE" frames this block as a
  // scannable overview (it also covers line2's grounding/PPE counts, which aren't
  // interaction findings at all) rather than a grade.
  const overallEyebrow = document.createElement("p");
  overallEyebrow.className = "eyebrow";
  overallEyebrow.textContent = "AT A GLANCE";
  el.appendChild(overallEyebrow);

  const hazardSteps = [...new Set(hazards.map((h) => Math.min(...(h.step_numbers || []))))].sort(
    (a, b) => a - b
  );

  if (hazards.length > 0) {
    // The severity word is bound to a readable count on the same line, at headline
    // weight — a bare "DANGER" standing alone reads as a verdict on the entire
    // protocol; "DANGER · N interaction hazards" grammatically points at a bounded,
    // countable subject instead.
    const row = document.createElement("div");
    row.className = "scan-signal-row";

    const verdict = document.createElement("span");
    verdict.className = "signal scan-verdict";
    verdict.style.color = "var(--danger)";
    verdict.textContent = "▰ DANGER";

    const count = document.createElement("span");
    count.className = "scan-count";
    count.textContent = `${hazards.length} interaction hazard${hazards.length === 1 ? "" : "s"}`;

    row.append(verdict, count);
    el.appendChild(row);

    // Localizes the finding: "2 of 5 steps" is what answers "is this whole procedure
    // dangerous?" — no. Onset steps, not each hazard's full persistence span (a hazard's
    // step_numbers includes every step it's still co-present through via the hot thread,
    // e.g. the piranha pair is [1,2,3,4,5] because it persists in the vessel — unioning
    // that across hazards would wrongly say "steps 1-5 of 5", MORE alarming and LESS
    // localized than today). Mirrors the onset logic thread.js already uses for diamonds.
    if (hazardSteps.length > 0) {
      const stepWord = hazardSteps.length === 1 ? "step" : "steps";
      const verb = hazardSteps.length === 1 ? "carries" : "carry";
      const locator = document.createElement("p");
      locator.className = "scan-locator";
      locator.textContent =
        `${hazardSteps.length} of ${extractionDetail.steps} step${extractionDetail.steps === 1 ? "" : "s"} ` +
        `${verb} an established hazard: ${stepWord} ${joinWithAnd(hazardSteps)}.`;
      el.appendChild(locator);
    }
  } else {
    // Most protocols a Gladstone researcher pastes will hit none of our matrix entries,
    // making this the single most likely page a judge sees. "NO INTERACTION HAZARDS
    // FOUND" reads in the same spirit as the banned "no risks found" — an absence-of-
    // finding headline, not a checked-and-found-nothing one. Reworded, and paired with an
    // explicit caveat line below so this can never be misread as a safety claim.
    const signal = document.createElement("p");
    signal.className = "signal";
    signal.style.color = "var(--muted-paper)";
    signal.textContent = "▰ NO ESTABLISHED INTERACTION HAZARDS";
    el.appendChild(signal);

    const caveat = document.createElement("p");
    caveat.className = "scan-line";
    caveat.style.color = "var(--muted-paper)";
    caveat.textContent = "This is not a finding of safety. See below.";
    el.appendChild(caveat);
  }

  // The hazard count now lives in the headline row above — kept once, not repeated here
  // (same dedup pattern as the receipt line).
  const line1 = document.createElement("p");
  line1.className = "scan-line";
  // The chemical count is chemicalRecords.length — the exact list the per-chemical panel
  // below renders and that line2's five states partition — NOT extractionDetail.chemicals
  // (the raw Stage-1 mention count). The two can disagree (a non-grounded mention counted
  // but never given a panel row); keying both off the same list makes them reconcile by
  // construction. Singular/plural conditional on both counts.
  const stepCount = extractionDetail.steps;
  const chemCount = chemicalRecords.length;
  line1.textContent =
    `${stepCount} step${stepCount === 1 ? "" : "s"} · ${chemCount} chemical${chemCount === 1 ? "" : "s"}`;

  // "grounded", "PPE limitation", and "no GHS hazard classification" assume a chemist
  // reader. Tooltipped in place rather than rephrased — the underlying terms ("grounding",
  // "PPE") are used consistently elsewhere in the app and are worth keeping recognisable,
  // just explained on first read.
  //
  // "no PubChem record" is always shown (including its zero), the closest analog to the
  // "0 chemicals without hazard data" case this strip's "always print the zero" rule was
  // built for. "protein"/"fallback-sourced" are narrower categories whose zero is
  // uninteresting when a protocol has no biologicals (e.g. the piranha demo touches
  // neither), so they're broken out below and rendered only when non-zero.
  const line2 = document.createElement("p");
  line2.className = "scan-line";

  const groundedTerm = document.createElement("span");
  groundedTerm.textContent = "grounded";
  const groundedTip = attachTooltip(groundedTerm, "Grounded = matched to a live PubChem record.");

  const limitTerm = document.createElement("span");
  limitTerm.textContent = `PPE limitation${limitations === 1 ? "" : "s"}`;
  const limitTip = attachTooltip(
    limitTerm,
    "A PPE limitation flags a chemical whose protective-equipment guidance is general, not glove-material-specific. See the notice attached to its PPE section below."
  );

  const failedTerm = document.createElement("span");
  failedTerm.textContent = "grounding";
  const failedTip = attachTooltip(failedTerm, "Grounded = matched to a live PubChem record.");

  const noGhsTerm = document.createElement("span");
  noGhsTerm.textContent = "no GHS hazard classification";
  const noGhsTip = attachTooltip(noGhsTerm, "No GHS classification found in PubChem.");

  const noRecordTerm = document.createElement("span");
  noRecordTerm.textContent = "no PubChem record";
  const noRecordTip = attachTooltip(
    noRecordTerm,
    "PubChem has no record under this name. Not a safety claim either way: verify the name or consult its SDS directly."
  );

  line2.append(
    `${grounded} chemical${grounded === 1 ? "" : "s"} `,
    groundedTerm,
    groundedTip,
    ` · ${limitations} `,
    limitTerm,
    limitTip,
    ` · ${failedGrounding} chemical${failedGrounding === 1 ? "" : "s"} failed `,
    failedTerm,
    failedTip,
    ` · ${noGhs} chemical${noGhs === 1 ? "" : "s"} with `,
    noGhsTerm,
    noGhsTip,
    ` · ${noRecord} chemical${noRecord === 1 ? "" : "s"} with `,
    noRecordTerm,
    noRecordTip
  );

  el.append(line1, line2);

  // Only rendered when at least one of these applies — see comment above line2.
  if (notSmallMolecule > 0 || fallbackSourced > 0) {
    const line2b = document.createElement("p");
    line2b.className = "scan-line";
    const parts = [];

    if (notSmallMolecule > 0) {
      const proteinTerm = document.createElement("span");
      proteinTerm.textContent = "protein (no small-molecule record)";
      const proteinTip = attachTooltip(
        proteinTerm,
        "Proteins and antibodies aren't small molecules, so PubChem's small-molecule database doesn't cover them by design. This is expected, not a resolution miss."
      );
      parts.push(`${notSmallMolecule} `, proteinTerm, proteinTip);
    }

    if (fallbackSourced > 0) {
      if (parts.length) parts.push(" · ");
      const fallbackTerm = document.createElement("span");
      fallbackTerm.textContent = "hazard data from a non-PubChem source";
      const fallbackTip = attachTooltip(
        fallbackTerm,
        "PubChem has no record for this chemical, but a hand-verified supplier SDS does. See its own citation below, distinct from PubChem."
      );
      parts.push(`${fallbackSourced} chemical${fallbackSourced === 1 ? "" : "s"} with `, fallbackTerm, fallbackTip);
    }

    line2b.append(...parts);
    el.appendChild(line2b);
  }

  // The "N pairs checked / N pairs could not be checked" counts would otherwise repeat
  // here AND in the dedicated "every other pair" section below —
  // removed from the strip, kept once, in the section that already has room to explain
  // what "checked" means and expand into the full list.

  const unresolved = extractionDetail.unresolved || 0;
  if (unresolved > 0) {
    const line3 = document.createElement("p");
    line3.className = "scan-line";
    line3.textContent = `${unresolved} mention${unresolved === 1 ? "" : "s"} not resolved to a chemical`;
    el.appendChild(line3);
  }

  return el;
}

// A pair's compact print label ("water + nitrogen") — precomputed once, here,
// at normal render time, from the same chemical_ids the screen aggregation
// already grouped by. Never re-derived from statement.text (which is prose,
// not structured data) and never recomputed later at print time: app.js's
// prepareForPrint reads this back from data-compact-label, it doesn't
// rebuild it. See app.js's print-time row compaction for why this exists.
function pairLabel(statement, chemicalNameById) {
  const names = statement.chemical_ids.map((id) => chemicalNameById.get(id) || id);
  return cap(names.join(" + "));
}

// At six chemicals the interaction-hazard section produced ~8 "no established
// data" gap cards (every co-present pair Stage 3 checks, not just the interesting
// ones) sitting between the two real findings. Aggregate them into one expandable
// card instead of one-card-per-pair — still fully inspectable, no longer burying
// the real hazards. The real hazards themselves are never touched by this.
//
// Every row here always renders full (text + its own chip) — screen is untouched by
// the print fix below. Print-time compaction (first row stays full as the category's
// one representative citation, the rest collapse to their precomputed
// data-compact-label) happens transiently in app.js's prepareForPrint/
// restoreAfterPrint, not here — this function only supplies the label to compact to.
function renderGapAggregate(gaps, heading, chemicalNameById, onOpenInteractionTable) {
  const details = document.createElement("details");
  details.className = "card gap-card gap-aggregate rise-in";

  const summary = document.createElement("summary");
  summary.className = "gap-aggregate-summary";
  const headingEl = document.createElement("span");
  headingEl.className = "signal gap-heading";
  headingEl.textContent = heading;
  summary.appendChild(headingEl);
  details.appendChild(summary);

  for (const s of gaps) {
    const row = document.createElement("div");
    row.className = "gap-aggregate-row";
    row.dataset.compactLabel = pairLabel(s, chemicalNameById);
    const body = document.createElement("p");
    body.className = "card-body";
    body.textContent = s.text;
    // Every interaction_no_data statement cites the same generic source ("PreCaution
    // interaction table", no source_url — see app/brief.py::_interaction_statement),
    // so this is never renderChip's generic span/link — always the dedicated,
    // genuinely-clickable button (see its own comment above).
    row.append(body, renderInteractionTableChip(onOpenInteractionTable));
    details.appendChild(row);
  }
  return details;
}

// Without framing, the no-data cards would start immediately after the two hazard cards
// and read as if the brief had just run out of things to say. This wraps
// them with an explicit header ("checked", not "no data" — an action, not an error),
// one plain-language sentence on what "no established interaction" actually means,
// and two separately-labelled sub-groups: "checked, found nothing recorded" is a
// materially different epistemic state from "couldn't even look" (BriefStatement.
// gap_status, unchanged — see the split below), and a newcomer needs that told to
// them, not inferred from two similarly-shaped aggregate cards back to back.
function renderNoDataSubgroup(label, gaps, aggregateHeading, chemicalNameById, onOpenInteractionTable) {
  const group = document.createElement("div");
  group.className = "no-data-subgroup";

  const labelEl = document.createElement("p");
  labelEl.className = "eyebrow no-data-subgroup-label";
  labelEl.textContent = label;
  group.appendChild(labelEl);

  group.appendChild(renderGapAggregate(gaps, aggregateHeading, chemicalNameById, onOpenInteractionTable));
  return group;
}

// Nothing else marks the transition from the real findings (hazard cards) into this
// section — a reader could read it as
// more of the same kind of thing, or miss that it's a different epistemic category
// entirely (checked-and-clear vs. checked-and-hazardous). hasHazardsAbove gates BOTH the
// divider rule and the framing sentence's opening clause, since the zero-hazard case has
// nothing above this section to bridge from — a "those are the hazards" lead-in would be
// false there, so it keeps the original, unbridged copy.
function renderNoDataSection(checked, uncheckable, chemicalNameById, onOpenInteractionTable, hasHazardsAbove) {
  const wrap = document.createElement("div");
  wrap.className = "no-data-section" + (hasHazardsAbove ? " has-divider" : "");

  // "CHECKED — NO ESTABLISHED INTERACTION" claimed something false for half its own
  // contents: this section holds both pairs that WERE checked (no match) and pairs that
  // COULD NOT be checked (insufficient reactive-group data) — "checked" doesn't honestly
  // describe the "could not be checked" subgroup. Neutral heading, same hasHazardsAbove
  // gate as the framing sentence below ("other" only makes sense when there are hazard
  // cards above to be other than).
  const heading = document.createElement("p");
  heading.className = "signal no-data-heading";
  heading.textContent = hasHazardsAbove ? "EVERY OTHER PAIR" : "EVERY PAIR IN THIS PROTOCOL";
  wrap.appendChild(heading);

  const framing = document.createElement("p");
  framing.className = "no-data-framing";
  framing.textContent = hasHazardsAbove
    ? "Those are the interaction hazards this protocol's checked data established. Everything " +
      "below was checked against the same reference data. None showed an established " +
      "interaction. That is not the same as safe: it means no authoritative source in our set " +
      "describes them. Treat with normal caution and consult the SDS."
    : "We checked these combinations against our reference data and found no established " +
      "interaction. That is not the same as safe: it means no authoritative source in our set " +
      "describes them. Treat with normal caution and consult the SDS.";
  wrap.appendChild(framing);

  if (checked.length) {
    wrap.appendChild(
      renderNoDataSubgroup(
        "Checked · no match in reference set",
        checked,
        `${checked.length} pair${checked.length === 1 ? "" : "s"} checked against our reference set · none matched`,
        chemicalNameById,
        onOpenInteractionTable
      )
    );
  }
  if (uncheckable.length) {
    wrap.appendChild(
      renderNoDataSubgroup(
        "Could not be checked · reactive-group data unavailable",
        uncheckable,
        `${uncheckable.length} pair${uncheckable.length === 1 ? "" : "s"} could not be checked · reactive-group data unavailable`,
        chemicalNameById,
        onOpenInteractionTable
      )
    );
  }

  return wrap;
}

function renderInteractionSection(brief, chemicalRecords, onOpenInteractionTable) {
  const section = document.createElement("section");
  section.className = "interaction-section";

  const eyebrow = document.createElement("p");
  eyebrow.className = "eyebrow";
  eyebrow.textContent = "INTERACTION HAZARDS";
  section.appendChild(eyebrow);

  const hazards = brief.statements
    .filter((s) => s.kind === "interaction_hazard")
    .sort((a, b) => (a.step_numbers[0] ?? 0) - (b.step_numbers[0] ?? 0));
  const gaps = brief.statements.filter((s) => s.kind === "interaction_no_data");

  // "we checked this pair and nothing matched" and "we could not even determine a
  // reactive group to check" are different
  // epistemic states — the row text already says which, but a reader scanning just the
  // aggregate heading needs the same distinction, not one umbrella "no established
  // interaction data" covering both. Split by BriefStatement.gap_status.
  const checked = gaps.filter((s) => s.gap_status === "no_established_data");
  const uncheckable = gaps.filter((s) => s.gap_status === "insufficient_reactive_group_data");

  const chemicalNameById = new Map();
  for (const record of chemicalRecords) {
    for (const id of record.chemical_ids) chemicalNameById.set(id, sanitizeName(record.name));
  }

  for (const s of hazards) section.appendChild(renderHazardCard(s, onOpenInteractionTable));
  if (checked.length || uncheckable.length) {
    section.appendChild(
      renderNoDataSection(checked, uncheckable, chemicalNameById, onOpenInteractionTable, hazards.length > 0)
    );
  }

  return section;
}

function renderSourceGroup(title, subtitle, statements, openByDefault, gloveState) {
  const details = document.createElement("details");
  details.className = "source-group";
  details.open = openByDefault;

  const summary = document.createElement("summary");
  summary.className = "source-group-summary";
  const titleEl = document.createElement("span");
  titleEl.className = "source-group-title";
  titleEl.textContent = title;
  summary.appendChild(titleEl);
  if (subtitle) {
    const subEl = document.createElement("span");
    subEl.className = "source-group-subtitle mono";
    subEl.textContent = subtitle;
    summary.appendChild(subEl);
  }
  details.appendChild(summary);

  for (const s of statements) {
    details.appendChild(renderControlLine(s));
    // The glove-limitation disclosure is one global statement, never per-chemical —
    // attach it once, right after the first PPE line it's relevant to, wherever that
    // falls — attached to the PPE group, always visible, never collapsed.
    if (s.kind === "ppe" && gloveState.statement && !gloveState.rendered) {
      details.appendChild(renderGloveNotice(gloveState.statement));
      gloveState.rendered = true;
    }
  }
  return details;
}

function renderChemicalRow(record, brief, gloveState) {
  const own = brief.statements.filter((s) => s.chemical_ids.some((id) => record.chemical_ids.includes(id)));
  const hazardIdentity = own.find((s) => s.kind === "hazard_identity");
  const precautionary = own.filter((s) => s.kind === "precautionary");
  const safetyNotes = own.filter((s) => SAFETY_NOTE_KINDS.includes(s.kind));
  const gaps = own.filter(
    (s) =>
      s.kind === "no_data" ||
      s.kind === "grounding_incomplete" ||
      s.kind === "reactive_classification" ||
      s.kind === "not_small_molecule"
  );

  // One collapsed row per chemical — collapsed by default so the brief stops
  // reading as a wall of text. Never truncated: everything is still here, just folded.
  const row = document.createElement("details");
  row.className = "chemical-block";

  const summary = document.createElement("summary");
  summary.className = "chemical-summary";

  // The real GHS SVGs PubChem returns, 28px, never recoloured — the only shape
  // and colour on the page besides the signal badges, anchoring the eye at each
  // per-chemical row.
  if (hazardIdentity) {
    const urls = hazardIdentity.pictogram_urls || [];
    const labels = hazardIdentity.pictogram_labels || [];
    urls.forEach((url, i) => {
      const img = document.createElement("img");
      img.className = "ghs-pictogram";
      img.src = url;
      img.alt = labels[i] || "GHS pictogram";
      img.width = 28;
      img.height = 28;
      summary.appendChild(img);
    });
  }

  const nameEl = document.createElement("span");
  nameEl.className = "step-title";
  // Concentration is captured at extraction and shown here so it isn't silently
  // dropped — it never changes any hazard verdict (see README limitations).
  // Sanitize the FULL composed label (name + concentration), not just the name: the
  // Stage-1 scratch-work often rides in on the concentration field ("25 parts (of
  // 25:24:1 mixture)"), which the display then wraps in its own parens, producing the
  // nested-paren mess ("Phenol (25 parts (of 25:24:1 mixture))"). Sanitizing after
  // composing catches it wherever it came from; a real concentration like "(3 M)" or
  // "(0.02%)" has no nesting or reasoning marker, so it survives untouched.
  const composedName = record.concentration ? `${record.name} (${record.concentration})` : record.name;
  nameEl.textContent = cap(sanitizeName(composedName));
  summary.appendChild(nameEl);
  if (hazardIdentity && hazardIdentity.signal_word) {
    const badge = document.createElement("span");
    badge.className = "signal signal-badge signal-badge-" + hazardIdentity.signal_word.toLowerCase();
    badge.textContent = hazardIdentity.signal_word.toUpperCase();
    // Distinguish this from the app's own hazard labels (the plain-text DANGER on
    // the overall banner and on interaction cards) — this one is PubChem's official GHS
    // signal word, not a PreCaution-authored severity call. The pill shape/fill already
    // differs visually from those plain-text labels; the tooltip makes the distinction
    // explicit rather than relying on a reader noticing the shape difference.
    badge.title = "GHS signal word: official hazard classification, from PubChem.";
    summary.appendChild(badge);
  }
  // Four distinct states — a genuine resolution miss ("no PubChem record")
  // reads as a real gap; a protein/antibody correctly has no small-molecule record at all
  // (expected, not a miss); a fallback-sourced chemical has real hazard data, just not
  // from PubChem. None of these should read the same as each other.
  const cidEl = document.createElement("span");
  cidEl.className = "mono";
  if (record.found) {
    cidEl.textContent = `CID ${record.cid}`;
  } else if (record.fallback_source) {
    cidEl.textContent = `${record.fallback_source} (not PubChem)`;
  } else if (record.not_small_molecule) {
    cidEl.textContent = "protein: no small-molecule record";
  } else {
    cidEl.textContent = "no PubChem record";
  }
  summary.appendChild(cidEl);
  row.appendChild(summary);

  const body = document.createElement("div");
  body.className = "chemical-body";

  for (const { key, title, subtitle, openByDefault } of AUDIENCE_GROUPS) {
    const group = safetyNotes.filter((s) => s.audience === key);
    if (group.length) body.appendChild(renderSourceGroup(title, subtitle, group, openByDefault, gloveState));
  }
  if (hazardIdentity) {
    body.appendChild(renderSourceGroup("GHS classification", "H-codes, signal word, pictograms", [hazardIdentity], true, gloveState));
  }
  if (precautionary.length) {
    body.appendChild(renderSourceGroup("Precautionary statements", "P-codes, resolved", precautionary, true, gloveState));
  }
  const other = safetyNotes.filter((s) => s.audience === "other");
  if (other.length) body.appendChild(renderSourceGroup("Other sources", null, other, true, gloveState));
  for (const s of gaps) body.appendChild(renderGapCard(s));

  row.appendChild(body);
  return row;
}

function renderChemicalsSection(brief, chemicalRecords) {
  const section = document.createElement("section");
  section.className = "chemicals-section";

  const eyebrow = document.createElement("p");
  eyebrow.className = "eyebrow";
  eyebrow.textContent = "PER-CHEMICAL CONTROLS";
  section.appendChild(eyebrow);

  // brief.statements only contains a limitation_disclosure when at least one chemical
  // actually has a PPE statement to attach it to (app/brief.py) — so gloveState.statement
  // being present already guarantees renderChemicalRow's per-chemical loop below will
  // find that PPE statement and render the notice next to it. No fallback needed.
  const gloveState = { statement: brief.statements.find((s) => s.kind === "limitation_disclosure"), rendered: false };

  for (const record of chemicalRecords) {
    section.appendChild(renderChemicalRow(record, brief, gloveState));
  }

  return section;
}

// Chemical-looking phrases Stage 1 couldn't confidently resolve — surfaced as
// gap cards, never silently dropped, same honest-omission weight as a missing
// grounding heading.
function renderUnresolvedSection(brief) {
  const unresolved = brief.statements.filter((s) => s.kind === "unresolved_mention");
  if (!unresolved.length) return null;

  const section = document.createElement("section");
  section.className = "unresolved-section";
  const eyebrow = document.createElement("p");
  eyebrow.className = "eyebrow";
  eyebrow.textContent = "UNRESOLVED MENTIONS";
  section.appendChild(eyebrow);
  for (const s of unresolved) section.appendChild(renderGapCard(s));
  return section;
}

export function renderBrief(container, { brief, chemicalRecords, extractionDetail, onOpenInteractionTable }) {
  clearChildren(container);
  container.appendChild(renderScanLayer(brief, chemicalRecords, extractionDetail));
  container.appendChild(renderInteractionSection(brief, chemicalRecords, onOpenInteractionTable));
  const unresolvedSection = renderUnresolvedSection(brief);
  if (unresolvedSection) container.appendChild(unresolvedSection);
  container.appendChild(renderChemicalsSection(brief, chemicalRecords));
}

// https://cameochemicals.noaa.gov/reactivity/documentation/RG44-RG2 -> "NOAA CAMEO
// documentation/RG44-RG2" — mirrors app/brief.py::_cameo_react_label exactly (same
// mechanical last-two-path-segments derivation), so an interaction-table panel row's
// chip reads identically to the matching hazard card's chip for the same entry.
function cameoLabel(url) {
  if (!url) return null;
  const parts = url.replace(/\/+$/, "").split("/");
  if (parts.length < 2) return null;
  return `NOAA CAMEO ${parts[parts.length - 2]}/${parts[parts.length - 1]}`;
}

// The in-app interaction-table panel (app.js fetches GET /interaction-matrix and
// calls this) — a pure render of whatever the endpoint returns, same "no fetch, no
// logic" boundary as the rest of this file. Deliberately no sorting/filtering/
// pagination: it's a small, honest table, shown in full, once.
export function renderInteractionTable(container, verdicts) {
  clearChildren(container);
  if (!verdicts.length) {
    const p = document.createElement("p");
    p.className = "mono";
    p.textContent = "No entries in the local interaction table yet.";
    container.appendChild(p);
    return;
  }
  for (const v of verdicts) {
    const row = document.createElement("div");
    row.className = "interaction-table-row";

    const pair = document.createElement("p");
    pair.className = "interaction-table-pair";
    pair.textContent = `${v.group_a} × ${v.group_b}`;
    row.appendChild(pair);

    const summary = document.createElement("p");
    summary.className = "interaction-table-summary";
    summary.textContent = v.categories;
    row.appendChild(summary);

    if (v.note) {
      const note = document.createElement("p");
      note.className = "interaction-table-note";
      note.textContent = v.note;
      row.appendChild(note);
    }

    const source = v.source || {};
    const chip = document.createElement("a");
    chip.className = "chip mono";
    chip.textContent = cameoLabel(source.url) || source.source_name || "Source";
    if (source.url) {
      chip.href = source.url;
      chip.target = "_blank";
      chip.rel = "noopener";
    }
    row.appendChild(chip);

    container.appendChild(row);
  }
}
