import argparse
import json

from skyflip.settings_profiles import (
    delete_settings_profile,
    delete_module_settings_preset,
    get_active_settings_profile,
    list_module_settings_presets,
    list_settings_profiles,
    load_active_settings_profile,
    load_module_settings_preset,
    load_settings_profile,
    save_module_settings_preset,
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
        "accessories_file": "data/accessories.json",
        "max_accessory_price": None,
        "max_accessory_recommendations": 15,
        "max_accessory_ah_checks": 60,
        "accessory_sort": "score",
        "accessory_rarity": "",
        "accessory_view": "recommended",
        "accessory_search": None,
        "accessory_ascending": False,
        "show_owned": False,
        "show_locked": False,
        "only_craftable": False,
        "only_ah": False,
        "include_locked_accessories": False,
        "include_uncertain_accessories": True,
        "include_manual_unlocks": True,
        "include_ah_accessories": True,
        "include_craftable_accessories": True,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_settings_profiles_save_load_delete(monkeypatch, tmp_path):
    path = tmp_path / "profiles.json"
    monkeypatch.setenv("SKYFLIP_SETTINGS_PROFILES_FILE", str(path))
    original = make_args(min_profit=12_345, min_profit_percent=6, sections="craft")

    save_settings_profile(original, "Early Bazaar")

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == 1
    assert raw["source"] == "skyflip"
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


def test_old_settings_profile_store_still_loads(monkeypatch, tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text(
        json.dumps(
            {
                "active_profile": "Legacy",
                "profiles": {
                    "Legacy": {
                        "min_profit": 44_000,
                        "sections": "craft",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SKYFLIP_SETTINGS_PROFILES_FILE", str(path))
    target = make_args(min_profit=1, sections="bazaar-order")

    assert load_active_settings_profile(target) == "Legacy"
    assert target.min_profit == 44_000
    assert target.sections == "craft"


def test_module_settings_presets_save_load_delete(monkeypatch, tmp_path):
    monkeypatch.setenv("SKYFLIP_SETTINGS_PROFILES_FILE", str(tmp_path / "profiles.json"))
    original = make_args(
        spread_limit=7,
        min_spread_profit_per_unit=1_500,
        min_speed_confidence=65,
        conservative_speed=True,
        min_profit=99_999,
    )

    save_module_settings_preset(original, "bazaar", "Tight spread")

    presets = list_module_settings_presets("bazaar")
    assert presets["Tight spread"]["spread_limit"] == 7
    assert presets["Tight spread"]["min_speed_confidence"] == 65
    assert "min_profit" not in presets["Tight spread"]

    target = make_args(spread_limit=20, min_spread_profit_per_unit=0, min_speed_confidence=10, conservative_speed=False)
    assert load_module_settings_preset(target, "bazaar", "Tight spread")
    assert target.spread_limit == 7
    assert target.min_spread_profit_per_unit == 1_500
    assert target.min_speed_confidence == 65
    assert target.conservative_speed is True

    assert delete_module_settings_preset("bazaar", "Tight spread")
    assert list_module_settings_presets("bazaar") == {}
