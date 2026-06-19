import argparse

from skyflip.settings_profiles import (
    delete_settings_profile,
    get_active_settings_profile,
    list_settings_profiles,
    load_active_settings_profile,
    load_settings_profile,
    save_settings_profile,
)


def make_args(**overrides):
    values = {
        "days": 7,
        "min_profit": 5_000,
        "min_profit_percent": 4,
        "min_sales_per_day": 2,
        "max_median_sell_time_hours": 12,
        "cache_ttl": 300,
        "sections": "craft,bazaar-spread",
        "limit_per_section": 10,
        "spread_limit": None,
        "min_spread_profit_per_unit": 0,
        "min_spread_volume_week": 25_000,
        "max_spread_depth_ratio": 1.25,
        "max_estimated_buy_minutes": None,
        "max_estimated_sell_minutes": None,
        "max_estimated_bottleneck_minutes": 240.0,
        "min_speed_confidence": 35.0,
        "conservative_speed": True,
        "max_craft_cost": None,
        "max_capital_percent_per_flip": 35,
        "use_buy_order_cost": False,
        "recipes_file": "data/craft_recipes.json",
        "bazaar_conversions_file": "data/bazaar_conversions.json",
        "ah_watchlist_file": "data/ah_watchlist.json",
        "conversion_mode": "realistic",
        "show_rejected": False,
        "allow_restricted_profile": False,
        "refresh_interval": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_settings_profiles_save_load_delete(monkeypatch, tmp_path):
    monkeypatch.setenv("SKYFLIP_SETTINGS_PROFILES_FILE", str(tmp_path / "profiles.json"))
    original = make_args(min_profit=12_345, min_profit_percent=6, sections="craft")

    save_settings_profile(original, "Early Bazaar")

    assert "Early Bazaar" in list_settings_profiles()
    assert get_active_settings_profile() == "Early Bazaar"

    target = make_args(min_profit=1, min_profit_percent=1, sections="ah-underpriced")
    assert load_settings_profile(target, "Early Bazaar")
    assert target.min_profit == 12_345
    assert target.min_profit_percent == 6
    assert target.sections == "craft"

    assert delete_settings_profile("Early Bazaar")
    assert list_settings_profiles() == {}
    assert get_active_settings_profile() is None


def test_active_settings_profile_reloads(monkeypatch, tmp_path):
    monkeypatch.setenv("SKYFLIP_SETTINGS_PROFILES_FILE", str(tmp_path / "profiles.json"))
    original = make_args(min_profit=88_000, sections="craft")
    save_settings_profile(original, "Saved")

    target = make_args(min_profit=1, sections="bazaar-order")

    assert load_active_settings_profile(target) == "Saved"
    assert target.min_profit == 88_000
    assert target.sections == "craft"


def test_settings_profiles_persist_bazaar_speed_preset_fields(monkeypatch, tmp_path):
    monkeypatch.setenv("SKYFLIP_SETTINGS_PROFILES_FILE", str(tmp_path / "profiles.json"))
    original = make_args(
        max_estimated_bottleneck_minutes=480,
        min_speed_confidence=20,
        conservative_speed=False,
    )

    save_settings_profile(original, "Risky Bazaar")

    target = make_args(max_estimated_bottleneck_minutes=120, min_speed_confidence=60, conservative_speed=True)
    assert load_settings_profile(target, "Risky Bazaar")
    assert target.max_estimated_bottleneck_minutes == 480
    assert target.min_speed_confidence == 20
    assert target.conservative_speed is False
