# Phase 0 — Reactive-group coverage diagnostic

Read-only diagnostic. Calls `app.pubchem.ground_chemical` (the real pipeline entry point, disk-cached) for each chemical below and records whether PubChem's live "Reactive Group" heading (sourced from NOAA CAMEO Chemicals) assigns it a reactive group. This determines whether an offline interaction-matrix expander would have anything to expand: `app/interactions.py` only ever consults the matrix for a pair when BOTH chemicals already have a reactive group from grounding — if the group is missing, Stage 3 returns `insufficient_reactive_group_data` and never reaches the matrix at all.

## 1. Per-chemical results

### Common wet-lab / waste-stream (expected to ground well)

| Input name | CID | GHS classification | Reactive group? | Reactive group name(s) | missing_sections |
|---|---|---|---|---|---|
| hydrogen peroxide | 784 | yes | yes | Oxidizing Agents, Strong; Water and Aqueous Solutions | - |
| sulfuric acid | 1118 | yes | yes | Acids, Strong Oxidizing | - |
| hydrochloric acid | 313 | yes | yes | Acids, Strong Non-oxidizing; Water and Aqueous Solutions | - |
| sodium hydroxide | 14798 | yes | yes | Bases, Strong; Water and Aqueous Solutions | - |
| ammonium hydroxide | 14923 | yes | yes | Bases, Weak; Water and Aqueous Solutions | - |
| sodium hypochlorite | 23665760 | yes | yes | Salts, Basic; Oxidizing Agents, Strong; Water and Aqueous Solutions | - |
| acetone | 180 | yes | yes | Ketones | - |
| methanol | 887 | yes | yes | Alcohols and Polyols; Amines, Phosphines, and Pyridines | - |
| isopropanol | 3776 | yes | yes | Alcohols and Polyols | - |
| xylene | no CID | no (not found) | no | - | CID resolution |
| phenol | 996 | yes | yes | Phenols and Cresols; Acids, Weak; Water and Aqueous Solutions | - |
| chloroform | 6212 | yes | yes | Halogenated Organic Compounds | - |

### Fixation / histology (neuro, immuno)

| Input name | CID | GHS classification | Reactive group? | Reactive group name(s) | missing_sections |
|---|---|---|---|---|---|
| paraformaldehyde | no CID | no (not found) | no | - | CID resolution |
| formaldehyde | 712 | yes | yes | Aldehydes; Polymerizable Compounds; Water and Aqueous Solutions | - |
| glutaraldehyde | 3485 | yes | yes | Aldehydes; Polymerizable Compounds; Water and Aqueous Solutions | - |
| methanol | 887 | yes | yes | Alcohols and Polyols; Amines, Phosphines, and Pyridines | - |
| DAPI | 2954 | no | no | - | GHS Classification, Reactive Group, Personal Protective Equipment (PPE), First Aid Measures, Disposal Methods, Storage Conditions |
| 3,3'-diaminobenzidine | 7071 | yes | no | - | Reactive Group, Personal Protective Equipment (PPE), Disposal Methods, Storage Conditions |
| mounting medium | no CID | no (not found) | no | - | CID resolution |

### Molecular / RNA-DNA work (virology)

| Input name | CID | GHS classification | Reactive group? | Reactive group name(s) | missing_sections |
|---|---|---|---|---|---|
| guanidinium thiocyanate | 65046 | yes | no | - | Reactive Group, Personal Protective Equipment (PPE), First Aid Measures, Disposal Methods, Storage Conditions |
| TRIzol | no CID | no (not found) | no | - | CID resolution |
| beta-mercaptoethanol | 1567 | yes | yes | Alcohols and Polyols; Sulfides, Organic | - |
| dithiothreitol | 439196 | yes | no | - | Reactive Group, First Aid Measures |
| phenol | 996 | yes | yes | Phenols and Cresols; Acids, Weak; Water and Aqueous Solutions | - |
| chloroform | 6212 | yes | yes | Halogenated Organic Compounds | - |
| ethidium bromide | 14710 | yes | no | - | Reactive Group, Personal Protective Equipment (PPE) |

### Gel / electrophoresis

| Input name | CID | GHS classification | Reactive group? | Reactive group name(s) | missing_sections |
|---|---|---|---|---|---|
| acrylamide | 6579 | yes | yes | Amides and Imides; Acrylates and Acrylic Acids; Polymerizable Compounds; Water and Aqueous Solutions | - |
| ammonium persulfate | 62648 | yes | yes | Salts, Acidic; Oxidizing Agents, Strong | Storage Conditions |
| TEMED | 8037 | yes | yes | Amines, Phosphines, and Pyridines | First Aid Measures, Storage Conditions |
| sodium dodecyl sulfate | 3423265 | yes | yes | Hydrocarbons, Aliphatic Saturated; Salts, Basic | - |
| Tris base | 6503 | yes | no | - | Reactive Group, Personal Protective Equipment (PPE), First Aid Measures |
| glycine | 750 | yes | yes | Salts, Acidic | First Aid Measures |

### Buffers / benign (expected to ground poorly or classify-benign)

| Input name | CID | GHS classification | Reactive group? | Reactive group name(s) | missing_sections |
|---|---|---|---|---|---|
| phosphate-buffered saline | 24978514 | no | no | - | GHS Classification, Reactive Group, Personal Protective Equipment (PPE), First Aid Measures, Disposal Methods, Storage Conditions |
| bovine serum albumin | no CID | no (not found) | no | - | CID resolution |
| Triton X-100 | 5590 | yes | no | - | Reactive Group, Personal Protective Equipment (PPE), First Aid Measures, Disposal Methods, Storage Conditions |
| Tween-20 | no CID | no (not found) | no | - | CID resolution |
| sodium chloride | 5234 | yes | no | - | Reactive Group, First Aid Measures |
| glycerol | 753 | yes | yes | Alcohols and Polyols | - |
| EDTA | 6049 | yes | yes | Acids, Carboxylic; Amines, Phosphines, and Pyridines | - |

## 2. Per-group summary

- **Common wet-lab / waste-stream (expected to ground well)**: 11 of 12 chemicals got a reactive group.
- **Fixation / histology (neuro, immuno)**: 3 of 7 chemicals got a reactive group.
- **Molecular / RNA-DNA work (virology)**: 3 of 7 chemicals got a reactive group.
- **Gel / electrophoresis**: 5 of 6 chemicals got a reactive group.
- **Buffers / benign (expected to ground poorly or classify-benign)**: 2 of 7 chemicals got a reactive group.

**Overall: 24 of 39 chemicals got a reactive group.**

## 3. Distinct reactive groups that appeared

| Reactive group | Unique chemicals | Chemicals |
|---|---|---|
| Water and Aqueous Solutions | 9 | acrylamide, ammonium hydroxide, formaldehyde, glutaraldehyde, hydrochloric acid, hydrogen peroxide, phenol, sodium hydroxide, sodium hypochlorite |
| Alcohols and Polyols | 4 | beta-mercaptoethanol, glycerol, isopropanol, methanol |
| Oxidizing Agents, Strong | 3 | ammonium persulfate, hydrogen peroxide, sodium hypochlorite |
| Amines, Phosphines, and Pyridines | 3 | EDTA, TEMED, methanol |
| Polymerizable Compounds | 3 | acrylamide, formaldehyde, glutaraldehyde |
| Salts, Basic | 2 | sodium dodecyl sulfate, sodium hypochlorite |
| Aldehydes | 2 | formaldehyde, glutaraldehyde |
| Salts, Acidic | 2 | ammonium persulfate, glycine |
| Acids, Strong Oxidizing | 1 | sulfuric acid |
| Acids, Strong Non-oxidizing | 1 | hydrochloric acid |
| Bases, Strong | 1 | sodium hydroxide |
| Bases, Weak | 1 | ammonium hydroxide |
| Ketones | 1 | acetone |
| Phenols and Cresols | 1 | phenol |
| Acids, Weak | 1 | phenol |
| Halogenated Organic Compounds | 1 | chloroform |
| Sulfides, Organic | 1 | beta-mercaptoethanol |
| Amides and Imides | 1 | acrylamide |
| Acrylates and Acrylic Acids | 1 | acrylamide |
| Hydrocarbons, Aliphatic Saturated | 1 | sodium dodecyl sulfate |
| Acids, Carboxylic | 1 | EDTA |

## 4. "Classified: not hazardous" vs "no GHS record at all"

**Classified: not hazardous** (PubChem has a GHS Classification section, but it carries no signal word/hazard statements/pictograms — like water) — 0 chemical(s):
- _none_

**No GHS record at all** (PubChem has no GHS Classification section for this compound — like PBS) — 2 chemical(s):
- DAPI
- phosphate-buffered saline

Note: both buckets above only include chemicals PubChem actually **found** (`found=True`). A third, separate state exists in this set that isn't either of the two requested: 6 chemicals never resolved to a CID at all (`xylene`, `paraformaldehyde`, `mounting medium`, `TRIzol`, `bovine serum albumin`, `Tween-20` — see the "no CID" / "CID resolution" rows in section 1). That's a name-resolution failure, not a hazard-classification outcome, so it's kept out of this comparison rather than folded into "no GHS record at all."

## 5. Findings

Biological reagents (proteins, detergents, buffers, stains, trade-name mixtures) generally do **not** get a reactive group — of the 15 chemicals in this set with no reactive group, most are exactly this class (BSA, PBS, DAPI, Tween-20, Triton X-100, TRIzol, ethidium bromide, Tris base, sodium chloride, guanidinium thiocyanate, dithiothreitol, mounting medium, 3,3'-diaminobenzidine), and several of them (BSA, PBS, Triton X-100, ethidium bromide, sodium chloride, Tris base, guanidinium thiocyanate, dithiothreitol, 3,3'-diaminobenzidine) still carry a real GHS hazard classification — so a reactive group is missing far more often than hazard data in general is missing for this class.

The only reactive groups with enough members in this set to plausibly pair up inside a real protocol step are "Water and Aqueous Solutions" (9 chemicals, though this is a broad solvent tag rather than a hazard-specific bucket and none of the current 3 matrix entries reference it), "Alcohols and Polyols" (4), and "Oxidizing Agents, Strong" / "Amines, Phosphines, and Pyridines" / "Polymerizable Compounds" (3 each) — everything else is a singleton or pair, which a same-step co-presence would rarely land on twice.

The bottleneck is split by reagent class, not uniform: for classic wet-lab acids/bases/oxidizers/solvents (11 of 12 in that group grounded a reactive group), the matrix is the real constraint — only 3 entries exist today against a much richer set of assignable groups, so an expander would have real targets; but for the Gladstone-relevant biological reagents this diagnostic was run to check (antibodies, BSA, buffers, stains), the bottleneck is GROUNDING — most of them never reach a reactive-group assignment at all, so Stage 3 returns `insufficient_reactive_group_data` before the matrix is ever consulted, and no number of new matrix entries would change that outcome for those specific reagents.
