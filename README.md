# PreCaution

**Procedure-aware lab safety brief generator.** Paste a written experimental protocol, get back a short, verifiable, experiment-specific safety brief — not another 80-page SDS dump.

Built for the **Built with Claude: Life Sciences Hackathon** (Builder Track, "Build Beyond the Bench"), in partnership with the Gladstone Institutes.

## The problem

New researchers — rotation students, new grad students, new postdocs — are constantly handed protocols they didn't write, using chemicals they've never personally handled. The safety information they need already exists, but it's buried in 16-section Safety Data Sheets and 100+ page institutional manuals that nobody has time to map onto their specific procedure. Under time pressure, people guess or skip the check. The American Chemical Society names "poor planning and risk assessment of new experiments" as a top cause of academic lab incidents.

## What it does

Paste a free-text protocol. PreCaution reads it and produces a safety brief with two halves:

1. **Interaction hazards** — the dangers that emerge from the *procedure*, not just the ingredients. Not "chemical A is toxic" and "chemical B is flammable" said separately, but "in step 4 you combine A and B, and that releases a poisonous gas."
2. **Step-level guidance** — the PPE, waste disposal, and emergency response those exact steps demand.

Every hazard claim links back to an authoritative public source so it can be verified, not just trusted.

## What it is NOT

PreCaution is strictly a **defensive safety-checking tool**. It reads a procedure a scientist already plans to run and warns them about it — it does not design experiments, suggest chemical syntheses, or help make anything. It does not replace institutional EHS sign-off; it prepares a scientist to work safely and have a better-informed conversation with their safety officer.

## How it's grounded

Hallucinated hazard data is the top reason scientists abandon AI safety tools, so nothing here is left to a model's memory:

- **Per-chemical hazards** — resolved via [PubChem](https://pubchem.ncbi.nlm.nih.gov/) (PUG-REST for name → CID, PUG-View for GHS classification and Laboratory Chemical Safety Summary data). Every hazard links to its PubChem record.
- **Interaction verdicts** — never free-form model output. Claude identifies which chemicals meet in which step and classifies them into reactive groups; the actual danger verdict comes from a compact, hand-encoded reactive-group matrix built from [NOAA CAMEO Chemicals](https://cameochemicals.noaa.gov/) (primary authority) with the EPA hazardous-waste compatibility chart (EPA-600/2-80-076) as backstop.
- **Missing data is never silent.** If no authoritative hazard data exists for a chemical, the brief says so explicitly rather than implying it's safe.

## Status

🚧 Active build, hackathon week of July 7–13, 2026.

## License

[MIT](LICENSE)
