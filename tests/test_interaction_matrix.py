from app.interaction_matrix import known_pairs, lookup_verdict


def test_piranha_pair_found_and_sourced():
    verdict = lookup_verdict("Oxidizing Agents, Strong", "Acids, Strong Oxidizing")
    assert verdict is not None
    assert "explosion" in verdict.hazard_types
    # The pairwise reactivity-documentation page, not the generic single-group datasheet —
    # the generic page doesn't carry pair-specific predictions at all (2026-07-10 audit).
    assert verdict.source.url == "https://cameochemicals.noaa.gov/reactivity/documentation/RG44-RG2"


def test_lookup_is_order_independent():
    a = lookup_verdict("Oxidizing Agents, Strong", "Acids, Strong Oxidizing")
    b = lookup_verdict("Acids, Strong Oxidizing", "Oxidizing Agents, Strong")
    assert a is not None and b is not None
    assert a.quote == b.quote


def test_azide_acid_pair_found_and_sourced():
    verdict = lookup_verdict("Azo, Diazo, Azido, Hydrazine, and Azide Compounds", "Acids, Strong Oxidizing")
    assert verdict is not None
    assert "toxic_gas" in verdict.hazard_types
    assert verdict.source.url == "https://cameochemicals.noaa.gov/reactivity/documentation/RG8-RG2"


def test_unknown_pair_returns_none_not_a_safety_claim():
    # A pair that is chemically plausible but not in our seed table.
    assert lookup_verdict("Alcohols and Glycols", "Water and Aqueous Solutions") is None


def test_quote_never_contains_authored_prose():
    """The item-1 audit fix, locked in: every verdict's quote must be free of strings that
    were authored by us rather than fetched from CAMEO. We can't re-fetch CAMEO in a test,
    so this checks the specific failure mode instead — the quote must never contain the
    `note` text (the mechanism that let authored prose leak under the chip last time) and
    must never name a specific commercial/informal mixture name, since CAMEO's reactive-
    group pages describe classes of chemicals, never named specific mixtures.
    """
    banned_phrases = ["piranha solution", "piranha", "this protocol", "this demo"]
    for group_a, group_b in known_pairs():
        verdict = lookup_verdict(group_a, group_b)
        assert verdict is not None
        lowered = verdict.quote.lower()
        for phrase in banned_phrases:
            assert phrase not in lowered, f"quote for {group_a} + {group_b} contains authored phrase {phrase!r}"
        if verdict.note:
            assert verdict.note not in verdict.quote


def test_note_is_never_a_hazard_claim():
    """§ item 1: 'note may never contain a safety claim. Nominal facts only.' A cheap,
    real guard: none of the hazard-signalling verbs/nouns this table's quotes use should
    appear in a note — a note that starts describing danger has drifted into being an
    unsourced hazard claim, which is exactly the bug this restructuring exists to prevent.
    """
    danger_words = ["explos", "toxic", "danger", "hazard", "flammable", "violent", "react"]
    for group_a, group_b in known_pairs():
        verdict = lookup_verdict(group_a, group_b)
        assert verdict is not None
        if verdict.note is None:
            continue
        lowered = verdict.note.lower()
        for word in danger_words:
            assert word not in lowered, f"note for {group_a} + {group_b} contains hazard word {word!r}: {verdict.note!r}"
