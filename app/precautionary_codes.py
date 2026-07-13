"""Static GHS precautionary-statement (P-code) text, demo-scoped.

Bare P-codes ("P210, P220, P260…") are not guidance to a newcomer — this maps
each code PubChem returns for the demo protocol's three chemicals to its
official UN GHS wording, so the brief can say what the code actually means.

Deterministic, no model call — same rule as app/interaction_matrix.py.
Sourced (not recalled), cross-referenced against two independent
secondary references that both cite the UN GHS "Purple Book" (Annex 3) and
ECHA/CLP Regulation 1272/2008 (the same authority PubChem's own GHS
classifications already cite in this project — see app/pubchem.py):
  - https://en.wikipedia.org/wiki/GHS_precautionary_statements
  - https://www.chemsafetypro.com/Topics/GHS/GHS_precautionary_statement_p_code.html
The two sources agreed verbatim on every code below except minor phrasing
drift on P271/P420 across GHS revisions; the more commonly-cited revision's
wording was kept in those cases.

Deliberately scoped to only the codes the demo's three chemicals actually
emit — same "small seed set, extend deliberately" pattern as the interaction
matrix. Extending this table
for a new chemical must go through the same fetch-and-cross-reference
process, never general GHS recall.

P370+P378 and P501 are officially templated statements in the UN GHS text
itself (a blank for chemical- or jurisdiction-specific detail, e.g. "Use ...
for extinction") — not something we were unable to find. The general-form
completions below are the standard ones used when no chemical-specific
extinguishing media or disposal regulation is being cited.
"""

PRECAUTIONARY_STATEMENTS: dict[str, str] = {
    "P210": "Keep away from heat, hot surfaces, sparks, open flames and other ignition sources. No smoking.",
    "P220": "Keep away from clothing and other combustible materials.",
    "P260": "Do not breathe dust/fume/gas/mist/vapours/spray.",
    "P261": "Avoid breathing dust/fume/gas/mist/vapours/spray.",
    "P264": "Wash hands thoroughly after handling.",
    "P270": "Do not eat, drink or smoke when using this product.",
    "P271": "Use only outdoors or in a well-ventilated area.",
    "P273": "Avoid release to the environment.",
    "P280": "Wear protective gloves/protective clothing/eye protection/face protection.",
    "P283": "Wear fire-resistant or flame-retardant clothing.",
    "P301+P316": "IF SWALLOWED: Get emergency medical help immediately.",
    "P301+P317": "IF SWALLOWED: Get medical help.",
    "P301+P330+P331": "IF SWALLOWED: Rinse mouth. Do NOT induce vomiting.",
    "P302+P361+P354": "IF ON SKIN: Take off immediately all contaminated clothing. Immediately rinse with water for several minutes.",
    "P304+P340": "IF INHALED: Remove person to fresh air and keep comfortable for breathing.",
    "P305+P354+P338": "IF IN EYES: Immediately rinse with water for several minutes. Remove contact lenses, if present and easy to do. Continue rinsing.",
    "P306+P360": "IF ON CLOTHING: Rinse immediately contaminated clothing and skin with plenty of water before removing clothes.",
    "P316": "Get emergency medical help immediately.",
    "P317": "Get medical help.",
    "P321": "Specific treatment (see information on this label and safety data sheet).",
    "P330": "Rinse mouth.",
    "P363": "Wash contaminated clothing before reuse.",
    "P370+P378": "In case of fire: use appropriate media to extinguish.",
    "P371+P380+P375": "In case of major fire and large quantities: Evacuate area. Fight fire remotely due to the risk of explosion.",
    "P391": "Collect spillage.",
    "P405": "Store locked up.",
    "P420": "Store separately.",
    "P501": "Dispose of contents/container in accordance with local/regional/national/international regulations.",
}


def resolve_precautionary_code(code: str) -> str | None:
    """Code -> official text, or None if not in our (deliberately small) table.

    Never invent text for an unresolved code — the caller must fall back to
    rendering the bare code, per the honest-omission rule.
    """
    return PRECAUTIONARY_STATEMENTS.get(code)
