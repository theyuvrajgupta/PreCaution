from app.interaction_matrix import lookup_verdict


def test_piranha_pair_found_and_sourced():
    verdict = lookup_verdict("Oxidizing Agents, Strong", "Acids, Strong Oxidizing")
    assert verdict is not None
    assert "explosion" in verdict.hazard_types
    assert verdict.source.url == "https://cameochemicals.noaa.gov/react/44"


def test_lookup_is_order_independent():
    a = lookup_verdict("Oxidizing Agents, Strong", "Acids, Strong Oxidizing")
    b = lookup_verdict("Acids, Strong Oxidizing", "Oxidizing Agents, Strong")
    assert a is not None and b is not None
    assert a.summary == b.summary


def test_azide_acid_pair_found_and_sourced():
    verdict = lookup_verdict("Azo, Diazo, Azido, Hydrazine, and Azide Compounds", "Acids, Strong Oxidizing")
    assert verdict is not None
    assert "toxic_gas" in verdict.hazard_types
    assert verdict.source.url == "https://cameochemicals.noaa.gov/react/8"


def test_unknown_pair_returns_none_not_a_safety_claim():
    # A pair that is chemically plausible but not in our seed table.
    assert lookup_verdict("Alcohols and Glycols", "Water and Aqueous Solutions") is None
