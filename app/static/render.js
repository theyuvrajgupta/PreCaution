// Renders a Brief into the DOM. Pure rendering — no state machine, no fetch,
// no logic beyond "what does this data look like." Reading hierarchy order
// per UI_Design_Spec.md §15: scan layer -> interaction hazards -> per-chemical
// controls (glove notice attached where PPE is shown) -> gaps rendered inline
// with the chemical they concern, not swept into a footer.
//
// Known scope limit, an accepted cut-order fallback (§2.11 / §G): real GHS
// pictogram SVGs are not wired through the data model yet (BriefStatement
// never carries ghs.pictogram_urls) — hazard_identity text already includes
// the pictogram labels as words, which is the sanctioned fallback.

const SAFETY_NOTE_KINDS = ["ppe", "first_aid", "disposal", "storage"];

// §20.2: per-chemical safety-note excerpts are grouped by source/audience, not
// dumped flat — a per-chemical wall of 50+ statements is the thing this
// section exists to fix. Render order locked to the spec: NIOSH -> ERG ->
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
};

function clearChildren(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
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
  el.textContent = statement.source_ref;
  if (statement.source_url) {
    el.href = statement.source_url;
    el.target = "_blank";
    el.rel = "noopener";
  }
  return el;
}

function renderHazardCard(statement) {
  const card = document.createElement("div");
  card.className = "card hazard-card rise-in";

  const signal = document.createElement("p");
  signal.className = "signal card-signal";
  signal.textContent = "DANGER";

  const steps = document.createElement("p");
  steps.className = "mono card-steps";
  steps.textContent = formatStepRange(statement.step_numbers);

  card.append(signal, steps);

  // §item-1 audit: lead_in is authored (which chemicals, combined directly or one
  // carried over) — rendered separately, above the quote, with NO chip. Never
  // concatenated into the chipped block; see app/interaction_matrix.py.
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
  const gaps = brief.statements.filter((s) => s.kind === "interaction_no_data").length;
  const limitations = brief.statements.filter((s) => s.kind === "limitation_disclosure").length;

  // Item 4: "0 chemicals without hazard data" (counting !found) contradicted the body,
  // which correctly shows gap cards for chemicals that WERE found (a real PubChem
  // record, a real CID) but had specific sections missing — water, nitrogen, PBS were
  // all `found`. Say the two distinct things separately, using the distinction the
  // backend already makes: grounding_error (fetch failed, status genuinely unknown —
  // see Brief.incomplete_chemicals) vs. a confirmed record with no GHS section.
  const failedGrounding = brief.incomplete_chemicals.length;
  const noGhs = chemicalRecords.filter((c) => (c.missing_sections || []).includes("GHS Classification")).length;

  const el = document.createElement("div");
  el.className = "scan-layer rise-in";

  const signal = document.createElement("p");
  signal.className = "signal";
  if (hazards.length > 0) {
    signal.style.color = "var(--danger)";
    signal.textContent = "▰ DANGER";
  } else {
    signal.style.color = "var(--muted-paper)";
    signal.textContent = "▰ NO INTERACTION HAZARDS FOUND";
  }

  const line1 = document.createElement("p");
  line1.className = "mono scan-line";
  line1.textContent =
    `${hazards.length} interaction hazard${hazards.length === 1 ? "" : "s"} · ` +
    `${extractionDetail.steps} steps · ${extractionDetail.chemicals} chemicals`;

  const line2 = document.createElement("p");
  line2.className = "mono scan-line";
  line2.textContent =
    `${limitations} PPE limitation${limitations === 1 ? "" : "s"} · ` +
    `${failedGrounding} chemical${failedGrounding === 1 ? "" : "s"} failed grounding · ` +
    `${noGhs} chemical${noGhs === 1 ? "" : "s"} with no GHS hazard classification`;

  const line2b = document.createElement("p");
  line2b.className = "mono scan-line";
  line2b.textContent = `${gaps} pair${gaps === 1 ? "" : "s"} without interaction data`;

  el.append(signal, line1, line2, line2b);

  const unresolved = extractionDetail.unresolved || 0;
  if (unresolved > 0) {
    const line3 = document.createElement("p");
    line3.className = "mono scan-line";
    line3.textContent = `${unresolved} mention${unresolved === 1 ? "" : "s"} not resolved to a chemical`;
    el.appendChild(line3);
  }

  return el;
}

// §21: at six chemicals the interaction-hazard section produced ~8 "no established
// data" gap cards (every co-present pair Stage 3 checks, not just the interesting
// ones) sitting between the two real findings. Aggregate them into one expandable
// card instead of one-card-per-pair — still fully inspectable, no longer burying
// the real hazards. The real hazards themselves are never touched by this.
function renderGapAggregate(gaps) {
  const details = document.createElement("details");
  details.className = "card gap-card gap-aggregate rise-in";

  const summary = document.createElement("summary");
  summary.className = "gap-aggregate-summary";
  const heading = document.createElement("span");
  heading.className = "signal gap-heading";
  heading.textContent = `${gaps.length} pair${gaps.length === 1 ? "" : "s"} checked · no established interaction data`;
  summary.appendChild(heading);
  details.appendChild(summary);

  for (const s of gaps) {
    const row = document.createElement("div");
    row.className = "gap-aggregate-row";
    const body = document.createElement("p");
    body.className = "card-body";
    body.textContent = s.text;
    row.append(body, renderChip(s));
    details.appendChild(row);
  }
  return details;
}

function renderInteractionSection(brief) {
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

  for (const s of hazards) section.appendChild(renderHazardCard(s));
  if (gaps.length) section.appendChild(renderGapAggregate(gaps));

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
    // falls (§6.6/§20.2: "attached to the PPE group, always visible, never collapsed").
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
  const gaps = own.filter((s) => s.kind === "no_data" || s.kind === "grounding_incomplete");

  // §20.2: one collapsed row per chemical — collapsed by default so the brief stops
  // reading as a wall of text. Never truncated: everything is still here, just folded.
  const row = document.createElement("details");
  row.className = "chemical-block";

  const summary = document.createElement("summary");
  summary.className = "chemical-summary";

  // §6.3/§G: the real GHS SVGs PubChem returns, 28px, never recoloured — the
  // only shape and colour on the page besides the signal badges, anchoring
  // the eye at each per-chemical row.
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
  nameEl.textContent = record.concentration ? `${record.name} (${record.concentration})` : record.name;
  summary.appendChild(nameEl);
  if (hazardIdentity && hazardIdentity.signal_word) {
    const badge = document.createElement("span");
    badge.className = "signal signal-badge signal-badge-" + hazardIdentity.signal_word.toLowerCase();
    badge.textContent = hazardIdentity.signal_word.toUpperCase();
    summary.appendChild(badge);
  }
  const cidEl = document.createElement("span");
  cidEl.className = "mono";
  cidEl.textContent = record.found ? `CID ${record.cid}` : "no PubChem record";
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

  const gloveState = { statement: brief.statements.find((s) => s.kind === "limitation_disclosure"), rendered: false };

  for (const record of chemicalRecords) {
    section.appendChild(renderChemicalRow(record, brief, gloveState));
  }

  // No chemical had PPE data at all (all missing) — still show the disclosure once,
  // never silently skip it.
  if (!gloveState.rendered && gloveState.statement) {
    section.appendChild(renderGloveNotice(gloveState.statement));
  }

  return section;
}

// §16.2/§D: chemical-looking phrases Stage 1 couldn't confidently resolve —
// surfaced as gap cards, never silently dropped, same honest-omission weight
// as a missing grounding heading.
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

export function renderBrief(container, { brief, chemicalRecords, extractionDetail }) {
  clearChildren(container);
  container.appendChild(renderScanLayer(brief, chemicalRecords, extractionDetail));
  container.appendChild(renderInteractionSection(brief));
  const unresolvedSection = renderUnresolvedSection(brief);
  if (unresolvedSection) container.appendChild(unresolvedSection);
  container.appendChild(renderChemicalsSection(brief, chemicalRecords));
}
