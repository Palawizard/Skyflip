import argparse

from skyflip.module_recommendations import recommend_module_presets
from skyflip.profile_parser import PlayerProfile


def _args(**overrides):
    values = {"budget": None}
    values.update(overrides)
    return argparse.Namespace(**values)


def test_low_budget_recommends_conservative_market_presets():
    profile = PlayerProfile("PalaMC", "id", purse=1_000_000, bank=500_000, inventory_api_enabled=True, magical_power=180)

    recommendations = recommend_module_presets(profile, _args())

    assert recommendations["bazaar"].preset.key == "safe"
    assert recommendations["craft"].preset.key == "safe"
    assert recommendations["compression"].preset.key == "conservative"
    assert recommendations["ah-bin"].preset.key == "strict"


def test_high_budget_low_unlock_keeps_craft_safe_but_not_bazaar():
    profile = PlayerProfile("PalaMC", "id", purse=120_000_000, bank=10_000_000, inventory_api_enabled=True, magical_power=220)

    recommendations = recommend_module_presets(profile, _args())

    assert recommendations["bazaar"].preset.key == "risky"
    assert recommendations["craft"].preset.key == "safe"
    assert "Few known unlock gates" in " ".join(recommendations["craft"].reasons)


def test_restricted_profile_recommends_low_risk_presets():
    profile = PlayerProfile(
        "PalaMC",
        "id",
        purse=80_000_000,
        bank=20_000_000,
        profile_mode="ironman",
        inventory_api_enabled=True,
        magical_power=300,
    )

    recommendations = recommend_module_presets(profile, _args())

    assert recommendations["bazaar"].preset.key == "safe"
    assert recommendations["craft"].preset.key == "safe"
    assert recommendations["compression"].preset.key == "conservative"
    assert recommendations["ah-bin"].preset.key == "strict"


def test_low_magical_power_recommends_budget_accessories():
    profile = PlayerProfile(
        "PalaMC",
        "id",
        purse=30_000_000,
        bank=0,
        inventory_api_enabled=True,
        magical_power=80,
        item_ids=["VACCINE_TALISMAN"],
    )

    recommendations = recommend_module_presets(profile, _args())

    assert recommendations["accessories"].preset.key == "budget"
    assert "Magical Power is 80" in " ".join(recommendations["accessories"].reasons)


def test_missing_inventory_api_recommends_budget_accessories():
    profile = PlayerProfile("PalaMC", "id", purse=50_000_000, bank=0, inventory_api_enabled=False, magical_power=300)

    recommendations = recommend_module_presets(profile, _args())

    assert recommendations["accessories"].preset.key == "budget"
    assert "Inventory API data is missing" in " ".join(recommendations["accessories"].reasons)


def test_recommendations_include_two_to_four_reasons():
    profile = PlayerProfile(
        "PalaMC",
        "id",
        purse=20_000_000,
        bank=5_000_000,
        inventory_api_enabled=True,
        magical_power=200,
        skills={"combat": 18},
        collection_tiers={"WHEAT": 4},
    )

    recommendations = recommend_module_presets(profile, _args())

    assert all(2 <= len(recommendation.reasons) <= 4 for recommendation in recommendations.values())
