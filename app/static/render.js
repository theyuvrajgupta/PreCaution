// Renders a Brief into the DOM. Pure rendering — no state machine, no fetch,
// no logic beyond "what does this data look like." Reading hierarchy order
// per UI_Design_Spec.md §15: scan layer -> interaction hazards -> per-chemical
// controls (glove notice attached where PPE is shown) -> gaps rendered inline
// with the chemical they concern, not swept into a footer.
//
// Known scope limits, both accepted cut-order fallbacks (§2.11): real GHS
// pictogram SVGs are not wired through the data model yet (BriefStatement
// never carries ghs.pictogram_urls) — hazard_identity text already includes
// the pictogram labels as words, which is the sanctioned fallback. The
// carryover thread and the unverified marker are a separate pass (§2.3).

const PER_CHEMICAL_KINDS = ["hazard_identity", "precautionary", "ppe", "first_aid", "disposal", "storage"];

const GAP_HEADING = {
  no_data: "NO AUTHORITATIVE DATA",
  interaction_no_data: "NO AUTHORITATIVE DATA",
  grounding_incomplete: "GROUNDING INCOMPLETE",
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

  const body = document.createElement("p");
  body.className = "card-body";
  body.textContent = statement.text;

  card.append(signal, steps, body, renderChip(statement));
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
  const withoutData = chemicalRecords.filter((c) => !c.found).length;

  const el = document.createElement("div");
  el.className = "scan-layer rise-in";

  const signal = document.createElement("p");
  signal.className = "signal";
  if (hazards.length > 0) {
    signal.style.color = "var(--danger)";
    signal.textContent = "▰ DANGER";
  } else {
    signal.style.color = "var(--muted)";
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
    `${withoutData} chemical${withoutData === 1 ? "" : "s"} without hazard data`;

  el.append(signal, line1, line2);
  return el;
}

function renderInteractionSection(brief) {
  const section = document.createElement("section");
  section.className = "interaction-section";

  const eyebrow = document.createElement("p");
  eyebrow.className = "eyebrow";
  eyebrow.textContent = "INTERACTION HAZARDS";
  section.appendChild(eyebrow);

  const relevant = brief.statements.filter(
    (s) => s.kind === "interaction_hazard" || s.kind === "interaction_no_data"
  );
  const sorted = [...relevant].sort((a, b) => {
    if (a.kind !== b.kind) return a.kind === "interaction_hazard" ? -1 : 1; // hazards before gaps
    return (a.step_numbers[0] ?? 0) - (b.step_numbers[0] ?? 0); // then by first step
  });

  for (const statement of sorted) {
    section.appendChild(
      statement.kind === "interaction_hazard" ? renderHazardCard(statement) : renderGapCard(statement)
    );
  }
  return section;
}

function renderChemicalsSection(brief, chemicalRecords) {
  const section = document.createElement("section");
  section.className = "chemicals-section";

  const eyebrow = document.createElement("p");
  eyebrow.className = "eyebrow";
  eyebrow.textContent = "PER-CHEMICAL CONTROLS";
  section.appendChild(eyebrow);

  const gloveStatement = brief.statements.find((s) => s.kind === "limitation_disclosure");
  let gloveRendered = false;

  for (const record of chemicalRecords) {
    const block = document.createElement("div");
    block.className = "chemical-block";

    const title = document.createElement("p");
    title.className = "step-title";
    title.textContent = record.name;
    block.appendChild(title);

    const own = brief.statements.filter((s) => s.chemical_ids.some((id) => record.chemical_ids.includes(id)));
    const controls = own.filter((s) => PER_CHEMICAL_KINDS.includes(s.kind));
    const gaps = own.filter((s) => s.kind === "no_data" || s.kind === "grounding_incomplete");

    for (const s of controls) {
      block.appendChild(renderControlLine(s));
      // Glove notice is always exactly one statement, never per-chemical — attach it
      // once, right after the first PPE line it's relevant to (§6.6: "attached where
      // PPE is shown").
      if (s.kind === "ppe" && gloveStatement && !gloveRendered) {
        block.appendChild(renderGloveNotice(gloveStatement));
        gloveRendered = true;
      }
    }
    for (const s of gaps) {
      block.appendChild(renderGapCard(s));
    }

    section.appendChild(block);
  }

  // No chemical had PPE data at all (all missing) — still show the disclosure once,
  // never silently skip it.
  if (!gloveRendered && gloveStatement) {
    section.appendChild(renderGloveNotice(gloveStatement));
  }

  return section;
}

export function renderBrief(container, { brief, chemicalRecords, extractionDetail }) {
  clearChildren(container);
  container.appendChild(renderScanLayer(brief, chemicalRecords, extractionDetail));
  container.appendChild(renderInteractionSection(brief));
  container.appendChild(renderChemicalsSection(brief, chemicalRecords));
}
