import argparse
import json
import time
from types import SimpleNamespace

import skyflip.dashboard_menu as dashboard_menu
from skyflip.cli import build_parser
from skyflip.dashboard_menu import (
    _MenuState,
    _cycle_section_sort,
    _budget_source_menu,
    _profile_freshness_label,
    _restricted_profile_note,
    _section_sort_key,
    _show_result_section,
    _sorted_section_data,
    load_sort_preferences,
    run_dashboard_menu,
    save_sort_preferences,
    should_open_dashboard_menu,
)
from skyflip.profile_parser import PlayerProfile
from skyflip.settings_profiles import list_settings_profiles, save_module_settings_preset, save_settings_profile
from skyflip.user_config import BUDGET_SOURCE_PURSE, HypixelUserConfig, load_user_config, profile_cache_path, save_user_config


def test_dashboard_command_without_arguments_opens_menu_mode():
    args = build_parser().parse_args(["dashboard"])

    assert should_open_dashboard_menu(args)
    assert args.profile_file is None
    assert args.budget is None


def test_dashboard_command_with_required_values_runs_directly():
    args = build_parser().parse_args(
        [
            "dashboard",
            "--profile-file",
            "profile.json",
            "--player-name",
            "PalaMC",
            "--budget",
            "21600000",
        ]
    )

    assert not should_open_dashboard_menu(args)


def test_result_section_sort_can_cycle_and_sort_spreads():
    state = _MenuState()
    data = SimpleNamespace(
        bazaar_spreads=[
            SimpleNamespace(product_id="LOW_MARGIN", profit_percent=8, coins_per_hour=500_000, estimated_total_profit=100_000, profit_per_minute=8_000, capital_required=1_000_000, risk="Low"),
            SimpleNamespace(product_id="HIGH_MARGIN", profit_percent=40, coins_per_hour=100_000, estimated_total_profit=80_000, profit_per_minute=2_000, capital_required=200_000, risk="Medium"),
        ]
    )

    assert _section_sort_key(state, "bazaar-spread") == "default"
    _cycle_section_sort(state, "bazaar-spread", 1)
    assert _section_sort_key(state, "bazaar-spread") == "coins-hour"

    state.section_sorts["bazaar-spread"] = "percent"
    sorted_data = _sorted_section_data(data, "bazaar-spread", "percent")

    assert [item.product_id for item in sorted_data.bazaar_spreads] == ["HIGH_MARGIN", "LOW_MARGIN"]
    assert [item.product_id for item in data.bazaar_spreads] == ["LOW_MARGIN", "HIGH_MARGIN"]


def test_result_section_sort_preferences_round_trip(tmp_path):
    path = tmp_path / "dashboard_sorts.json"

    save_sort_preferences({"bazaar-spread": "percent", "craft": "unknown", "bad": "score"}, path)

    assert load_sort_preferences(path) == {"bazaar-spread": "percent"}


def test_dashboard_menu_starts_with_modules(monkeypatch, capsys):
    inputs = iter(["q"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    args = make_menu_args(profile_file="profile.json", player_name="PalaMC", budget=1_000_000)

    assert run_dashboard_menu(args, resolve_uuid=lambda http, name: None) == 0
    output = capsys.readouterr().out
    assert "Bazaar Flip" in output
    assert "AH Craft Flips" in output
    assert "Accessories Helper" in output
    assert "Sections" not in output


def test_bazaar_module_routes_to_bazaar_sections(monkeypatch, tmp_path, capsys):
    profile = tmp_path / "PalaMC_Test_20260617_selected_profile.json"
    profile.write_text('{"profile":{"members":{"abc":{"player_name":"PalaMC","coin_purse":123}}}}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    inputs = iter(["1", "s", "1", "", "2", "2", "", "b", "b", "q"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    def fake_collect_dashboard_data(args, *, resolve_uuid):
        return SimpleNamespace(
            profile=PlayerProfile("PalaMC", "abc", 123, 0),
            budget=args.budget,
            craft=[],
            bazaar_spreads=[],
            bazaar_orders=[],
            conversions=[],
            ah_underpriced=[],
            talisman_helper=None,
            rejected=[],
            warnings=[],
            cache_ttl=args.cache_ttl,
        )

    monkeypatch.setattr("skyflip.dashboard_menu.collect_dashboard_data", fake_collect_dashboard_data)
    args = make_menu_args(profile_file=str(profile), sections="craft")

    assert run_dashboard_menu(args, resolve_uuid=lambda http, name: None) == 0
    output = capsys.readouterr().out
    assert "Bazaar Flip Results" in output
    assert "Bazaar spread flips" in output
    assert "Best Bazaar Spread Flips" in output
    assert "No results loaded. Press 1 to refresh." not in output
    assert "Refresh all results" not in output
    assert "Results  Bazaar spread: [0]  Bazaar order: [0]" in output
    assert "craft,bazaar-spread,bazaar-order" == args.sections


def test_module_recommended_settings_can_apply_preset(monkeypatch, tmp_path, capsys):
    profile = tmp_path / "PalaMC_Test_20260617_selected_profile.json"
    profile.write_text(
        '{"profile":{"members":{"abc":{"player_name":"PalaMC","coin_purse":50000000,'
        '"inventory":{},"accessory_bag_storage":{"highest_magical_power":80}}}}}',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    inputs = iter(["3", "s", "3", "a", "", "4", "", "b", "q"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    args = make_menu_args(profile_file=str(profile), budget=None, max_accessory_price=None)

    assert run_dashboard_menu(args, resolve_uuid=lambda http, name: None) == 0
    assert args.accessory_view == "recommended"
    assert args.accessory_sort == "coin-per-mp"
    assert args.max_accessory_price == 500_000
    output = capsys.readouterr().out
    assert "Why this recommendation?" in output
    assert "Applied Budget preset" in output
    assert "Applied preset" in output


def test_module_custom_preset_can_load_from_menu(monkeypatch, tmp_path, capsys):
    profile = tmp_path / "PalaMC_Test_20260617_selected_profile.json"
    profile.write_text('{"profile":{"members":{"abc":{"player_name":"PalaMC","coin_purse":123}}}}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SKYFLIP_SETTINGS_PROFILES_FILE", str(tmp_path / "settings_profiles.json"))
    preset_args = make_menu_args(spread_limit=6, min_speed_confidence=70, min_profit=123_456)
    save_module_settings_preset(preset_args, "bazaar", "Tight spread")
    inputs = iter(["1", "s", "6", "l", "1", "", "b", "b", "q"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    args = make_menu_args(profile_file=str(profile), spread_limit=20, min_speed_confidence=10, min_profit=5_000)

    assert run_dashboard_menu(args, resolve_uuid=lambda http, name: None) == 0
    assert args.spread_limit == 6
    assert args.min_speed_confidence == 70
    assert args.min_profit == 5_000
    output = capsys.readouterr().out
    assert "Loaded Tight spread preset" in output


def test_dashboard_menu_can_refresh_and_open_result_section(monkeypatch, tmp_path, capsys):
    profile = tmp_path / "PalaMC_Test_20260617_selected_profile.json"
    profile.write_text('{"profile":{"members":{"abc":{"player_name":"PalaMC","coin_purse":123}}}}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    inputs = iter(["2", "s", "1", "", "2", "2", "", "b", "b", "q"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    captured = {"calls": 0}

    def fake_collect_dashboard_data(args, *, resolve_uuid):
        captured["calls"] += 1
        captured["args"] = args
        return SimpleNamespace(
            profile=PlayerProfile("PalaMC", "abc", 123, 0),
            budget=args.budget,
            craft=[],
            bazaar_spreads=[],
            bazaar_orders=[],
            conversions=[],
            ah_underpriced=[],
            rejected=[],
            warnings=[],
            cache_ttl=args.cache_ttl,
        )

    monkeypatch.setattr("skyflip.dashboard_menu.collect_dashboard_data", fake_collect_dashboard_data)
    args = argparse.Namespace(
        profile_file=str(profile),
        player_name=None,
        budget=None,
        sections="craft,bazaar-spread,bazaar-order,bazaar-compression,ah-underpriced",
        refresh_interval=None,
        once=False,
        menu=False,
        days=7,
        min_profit=20_000,
        min_profit_percent=8,
        min_sales_per_day=2,
        max_median_sell_time_hours=12,
        cache_ttl=300,
        limit_per_section=10,
        spread_limit=None,
        min_spread_profit_per_unit=0,
        min_spread_volume_week=50_000,
        max_spread_depth_ratio=0.75,
        max_craft_cost=None,
        max_capital_percent_per_flip=35,
        use_buy_order_cost=False,
        recipes_file="data/craft_recipes.json",
        bazaar_conversions_file="data/bazaar_conversions.json",
        ah_watchlist_file="data/ah_watchlist.json",
        conversion_mode="realistic",
        export_json=None,
        export_csv=None,
        show_rejected=False,
        allow_restricted_profile=False,
    )

    assert run_dashboard_menu(args, resolve_uuid=lambda http, name: None) == 0
    assert captured["args"].profile_file == str(profile)
    assert captured["args"].player_name == "PalaMC"
    assert captured["args"].budget == 123


def test_result_section_redraw_does_not_refresh_results(monkeypatch, capsys):
    args = make_menu_args(profile_file="profile.json", player_name="PalaMC", budget=1_000_000)
    state = _MenuState(
        latest=SimpleNamespace(
            profile=PlayerProfile("PalaMC", "abc", 123, 0),
            budget=1_000_000,
            craft=[],
            bazaar_spreads=[],
            bazaar_orders=[],
            conversions=[],
            ah_underpriced=[],
            talisman_helper=None,
            rejected=[],
            warnings=[],
            cache_ttl=args.cache_ttl,
        ),
        last_refresh="2026-06-20 12:00:00",
    )
    draw_count = 0

    def fake_redraw_loop(draw_screen):
        nonlocal draw_count
        draw_screen()
        draw_screen()
        draw_count += 2
        return "enter"

    def fail_refresh(*args, **kwargs):
        raise AssertionError("redraw must not refresh results")

    monkeypatch.setattr(dashboard_menu, "_interactive_menu_enabled", lambda: True)
    monkeypatch.setattr(dashboard_menu, "_read_key_with_redraw", fake_redraw_loop)
    monkeypatch.setattr(dashboard_menu, "collect_dashboard_data", fail_refresh)

    _show_result_section(args, state, "craft")

    assert draw_count == 2
    output = capsys.readouterr().out
    assert "Craft flips" in output
    assert "Refresh results first" not in output


def test_talisman_result_section_renders_once_without_redraw_loop(monkeypatch, capsys):
    args = make_menu_args(profile_file="profile.json", player_name="PalaMC", budget=1_000_000)
    state = _MenuState(
        latest=SimpleNamespace(
            profile=PlayerProfile("PalaMC", "abc", 123, 0),
            budget=1_000_000,
            craft=[],
            bazaar_spreads=[],
            bazaar_orders=[],
            conversions=[],
            ah_underpriced=[],
            talisman_helper=None,
            rejected=[],
            warnings=[],
            cache_ttl=args.cache_ttl,
        ),
        last_refresh="2026-06-20 12:00:00",
    )
    key_reads = []

    def fail_redraw_loop(draw_screen):
        raise AssertionError("talisman result page should not redraw every frame")

    def fake_read_key(*, timeout=None):
        key_reads.append(timeout)
        return "enter"

    monkeypatch.setattr(dashboard_menu, "_interactive_menu_enabled", lambda: True)
    monkeypatch.setattr(dashboard_menu, "_read_key_with_redraw", fail_redraw_loop)
    monkeypatch.setattr(dashboard_menu, "_read_key", fake_read_key)

    _show_result_section(args, state, "talisman")

    assert key_reads == [None]
    output = capsys.readouterr().out
    assert "Talisman Helper was not loaded" in output


def test_static_result_section_scrolls_with_arrow_keys(monkeypatch, capsys):
    keys = iter(["down", "down", "up", "enter"])
    writes = []

    def fake_read_key(*, timeout=None):
        return next(keys)

    def draw_screen():
        for index in range(1, 8):
            print(f"line {index}")

    monkeypatch.setattr(dashboard_menu, "get_terminal_size", lambda: SimpleNamespace(height=4))
    monkeypatch.setattr(dashboard_menu, "_read_key", fake_read_key)
    monkeypatch.setattr(dashboard_menu, "_write_redraw_frame", lambda frame: writes.append(frame))

    choice = dashboard_menu._read_result_section_key(draw_screen, static_render=True, footer="Up/Down scroll")

    assert choice == "enter"
    assert len(writes) == 4
    assert "line 1" in writes[0]
    assert "line 3" in writes[2]
    assert "line 2" in writes[3]
    assert "Up/Down scroll" in writes[-1]
    assert capsys.readouterr().out == ""


def test_profile_freshness_labels_cache_states(monkeypatch, tmp_path):
    monkeypatch.setenv("SKYFLIP_CONFIG_DIR", str(tmp_path))
    args = make_menu_args(profile_file=None, profile_cache_ttl=60)
    fresh_state = _MenuState(
        latest=SimpleNamespace(profile=PlayerProfile("PalaMC", "abc", 1, 2, profile_source="api", profile_fetched_at=time.time()))
    )
    stale_state = _MenuState(
        latest=SimpleNamespace(profile=PlayerProfile("PalaMC", "abc", 1, 2, profile_source="api-cache", profile_fetched_at=time.time() - 120))
    )

    assert _profile_freshness_label(args, fresh_state) == "fresh"
    assert _profile_freshness_label(args, stale_state) == "stale"
    assert _profile_freshness_label(args, _MenuState()) == "unavailable"

    profile_cache_path().parent.mkdir(parents=True, exist_ok=True)
    profile_cache_path().write_text(json.dumps({"created_at": time.time(), "payload": {"profile": {}}}), encoding="utf-8")
    assert _profile_freshness_label(args, _MenuState()) == "cached"


def test_restricted_profile_note_keeps_accessories_visible():
    profile = PlayerProfile("PalaMC", "abc", 1, 2, profile_mode="ironman")

    note = _restricted_profile_note(profile, None)

    assert "Bazaar Flip" in note
    assert "AH Craft Flips" in note
    assert "Accessories Helper remains available" in note


def test_budget_source_menu_persists_api_choice(monkeypatch, tmp_path):
    monkeypatch.setenv("SKYFLIP_CONFIG_DIR", str(tmp_path))
    save_user_config(HypixelUserConfig("PalaMC", "abc", "Apple", "one"))
    inputs = iter(["1"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    args = make_menu_args(budget=123)

    _budget_source_menu(args, _MenuState(latest=SimpleNamespace(profile=PlayerProfile("PalaMC", "abc", 1_000, 2_000))))

    assert load_user_config().budget_source == BUDGET_SOURCE_PURSE
    assert args.budget is None


def test_r_refreshes_inside_settings_without_leaving(monkeypatch, tmp_path):
    profile = tmp_path / "PalaMC_Test_20260617_selected_profile.json"
    profile.write_text('{"profile":{"members":{"abc":{"player_name":"PalaMC","coin_purse":123}}}}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    inputs = iter(["s", "r", "b", "q"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    captured = {"calls": 0}

    def fake_collect_dashboard_data(args, *, resolve_uuid):
        captured["calls"] += 1
        return SimpleNamespace(
            profile=PlayerProfile("PalaMC", "abc", 123, 0),
            budget=args.budget,
            craft=[],
            bazaar_spreads=[],
            bazaar_orders=[],
            conversions=[],
            ah_underpriced=[],
            rejected=[],
            warnings=[],
            cache_ttl=args.cache_ttl,
        )

    monkeypatch.setattr("skyflip.dashboard_menu.collect_dashboard_data", fake_collect_dashboard_data)
    args = make_menu_args(profile_file=str(profile))

    assert run_dashboard_menu(args, resolve_uuid=lambda http, name: None) == 0
    assert captured["calls"] == 1


def test_r_refreshes_current_result_section(monkeypatch, tmp_path, capsys):
    profile = tmp_path / "PalaMC_Test_20260617_selected_profile.json"
    profile.write_text('{"profile":{"members":{"abc":{"player_name":"PalaMC","coin_purse":123}}}}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    inputs = iter(["2", "s", "1", "", "2", "r", "b", "b", "q"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    captured = {"calls": 0}

    def fake_collect_dashboard_data(args, *, resolve_uuid):
        captured["calls"] += 1
        return SimpleNamespace(
            profile=PlayerProfile("PalaMC", "abc", 123, 0),
            budget=args.budget,
            craft=[],
            bazaar_spreads=[],
            bazaar_orders=[],
            conversions=[],
            ah_underpriced=[],
            rejected=[],
            warnings=[],
            cache_ttl=args.cache_ttl,
        )

    monkeypatch.setattr("skyflip.dashboard_menu.collect_dashboard_data", fake_collect_dashboard_data)
    args = make_menu_args(profile_file=str(profile))

    assert run_dashboard_menu(args, resolve_uuid=lambda http, name: None) == 0
    assert captured["calls"] == 2
    output = capsys.readouterr().out
    assert "Refresh all results" not in output


def test_module_results_refresh_only_scans_that_module(monkeypatch, tmp_path):
    profile = tmp_path / "PalaMC_Test_20260617_selected_profile.json"
    profile.write_text('{"profile":{"members":{"abc":{"player_name":"PalaMC","coin_purse":123}}}}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    inputs = iter(["1", "s", "2", "r", "b", "b", "q"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    captured_sections = []

    def fake_collect_dashboard_data(args, *, resolve_uuid):
        captured_sections.append(args.sections)
        return SimpleNamespace(
            profile=PlayerProfile("PalaMC", "abc", 123, 0),
            budget=args.budget,
            craft=[],
            bazaar_spreads=[],
            bazaar_orders=[],
            conversions=[],
            ah_underpriced=[],
            talisman_helper=None,
            rejected=[],
            warnings=[],
            cache_ttl=args.cache_ttl,
        )

    monkeypatch.setattr("skyflip.dashboard_menu.collect_dashboard_data", fake_collect_dashboard_data)
    args = make_menu_args(profile_file=str(profile), sections="craft,bazaar-spread,bazaar-order")

    assert run_dashboard_menu(args, resolve_uuid=lambda http, name: None) == 0
    assert captured_sections == ["bazaar-spread,bazaar-order", "bazaar-spread,bazaar-order"]
    assert args.sections == "craft,bazaar-spread,bazaar-order"


def test_module_row_details_show_manual_verification(monkeypatch, tmp_path, capsys):
    profile = tmp_path / "PalaMC_Test_20260617_selected_profile.json"
    profile.write_text('{"profile":{"members":{"abc":{"player_name":"PalaMC","coin_purse":123}}}}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    inputs = iter(["1", "s", "2", "d", "1", "", "b", "b", "b", "q"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    def fake_collect_dashboard_data(args, *, resolve_uuid):
        return SimpleNamespace(
            profile=PlayerProfile("PalaMC", "abc", 123, 0),
            budget=args.budget,
            craft=[],
            bazaar_spreads=[
                SimpleNamespace(
                    product_id="ENCHANTED_CARROT",
                    final_score=90,
                    risk="Medium",
                    should_test_first=True,
                    manual_action="Suggested manual action: place a small order.",
                    reason="wide spread",
                    capital_required=100_000,
                    estimated_total_profit=25_000,
                    profit_percent=12.5,
                    confidence_score=72,
                )
            ],
            bazaar_orders=[],
            conversions=[],
            ah_underpriced=[],
            talisman_helper=None,
            rejected=[],
            warnings=["Bazaar spread section failed: sample warning"],
            cache_ttl=args.cache_ttl,
        )

    monkeypatch.setattr("skyflip.dashboard_menu.collect_dashboard_data", fake_collect_dashboard_data)
    args = make_menu_args(profile_file=str(profile), sections="craft,bazaar-spread,bazaar-order")

    assert run_dashboard_menu(args, resolve_uuid=lambda http, name: None) == 0
    output = capsys.readouterr().out
    assert "Filters:" in output
    assert "Verify" in output
    assert "top order walls" in output
    assert "Warnings: 1" in output


def test_dashboard_menu_can_save_named_settings_profile(monkeypatch, tmp_path):
    profile = tmp_path / "PalaMC_Test_20260617_selected_profile.json"
    profile.write_text('{"profile":{"members":{"abc":{"player_name":"PalaMC","coin_purse":123}}}}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SKYFLIP_SETTINGS_PROFILES_FILE", str(tmp_path / "settings_profiles.json"))
    inputs = iter(["s", "3", "s", "Early", "", "b", "b", "q"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    args = argparse.Namespace(
        profile_file=None,
        player_name=None,
        budget=None,
        sections="craft,bazaar-spread,bazaar-order,bazaar-compression,ah-underpriced",
        refresh_interval=None,
        once=False,
        menu=False,
        days=7,
        min_profit=12_345,
        min_profit_percent=6,
        min_sales_per_day=2,
        max_median_sell_time_hours=12,
        cache_ttl=300,
        limit_per_section=10,
        spread_limit=None,
        min_spread_profit_per_unit=0,
        min_spread_volume_week=50_000,
        max_spread_depth_ratio=0.75,
        max_craft_cost=None,
        max_capital_percent_per_flip=35,
        use_buy_order_cost=False,
        recipes_file="data/craft_recipes.json",
        bazaar_conversions_file="data/bazaar_conversions.json",
        ah_watchlist_file="data/ah_watchlist.json",
        conversion_mode="realistic",
        export_json=None,
        export_csv=None,
        show_rejected=False,
        allow_restricted_profile=False,
    )

    assert run_dashboard_menu(args, resolve_uuid=lambda http, name: None) == 0
    profiles = list_settings_profiles()
    assert profiles["Early"]["min_profit"] == 12_345
    assert profiles["Early"]["min_profit_percent"] == 6


def test_dashboard_menu_can_edit_main_settings(monkeypatch, tmp_path):
    profile = tmp_path / "PalaMC_Test_20260617_selected_profile.json"
    profile.write_text('{"profile":{"members":{"abc":{"player_name":"PalaMC","coin_purse":123}}}}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    inputs = iter([
        "s", "1", "15",
        "2",
        "4", "1", "300",
        "3", "2", "3", "4", "5", "b",
        "b", "q",
        "b", "q",
    ])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    args = argparse.Namespace(
        profile_file=None,
        player_name=None,
        budget=None,
        sections="craft,bazaar-spread,bazaar-order,bazaar-compression,ah-underpriced",
        refresh_interval=None,
        once=False,
        menu=False,
        days=7,
        min_profit=5_000,
        min_profit_percent=4,
        min_sales_per_day=2,
        max_median_sell_time_hours=12,
        cache_ttl=300,
        limit_per_section=10,
        spread_limit=None,
        min_spread_profit_per_unit=0,
        min_spread_volume_week=50_000,
        max_spread_depth_ratio=0.75,
        max_craft_cost=None,
        max_capital_percent_per_flip=35,
        use_buy_order_cost=False,
        recipes_file="data/craft_recipes.json",
        bazaar_conversions_file="data/bazaar_conversions.json",
        ah_watchlist_file="data/ah_watchlist.json",
        conversion_mode="realistic",
        export_json=None,
        export_csv=None,
        show_rejected=True,
        allow_restricted_profile=False,
    )

    assert run_dashboard_menu(args, resolve_uuid=lambda http, name: None) == 0
    assert args.limit_per_section == 15
    assert args.cache_ttl == 300
    assert args.show_rejected is False
    assert args.sections == "craft"


def test_dashboard_menu_can_edit_craft_settings(monkeypatch, tmp_path):
    profile = tmp_path / "PalaMC_Test_20260617_selected_profile.json"
    profile.write_text('{"profile":{"members":{"abc":{"player_name":"PalaMC","coin_purse":123}}}}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    inputs = iter(["2", "s", "5", "1", "2500000", "2", "8", "data/custom_recipes.json", "b", "b", "q"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    args = argparse.Namespace(
        profile_file=None,
        player_name=None,
        budget=None,
        sections="craft,bazaar-spread,bazaar-order,bazaar-compression,ah-underpriced",
        refresh_interval=None,
        once=False,
        menu=False,
        days=7,
        min_profit=5_000,
        min_profit_percent=4,
        min_sales_per_day=2,
        max_median_sell_time_hours=12,
        cache_ttl=300,
        limit_per_section=10,
        spread_limit=None,
        min_spread_profit_per_unit=0,
        min_spread_volume_week=50_000,
        max_spread_depth_ratio=0.75,
        max_craft_cost=None,
        max_capital_percent_per_flip=35,
        use_buy_order_cost=False,
        recipes_file="data/craft_recipes.json",
        bazaar_conversions_file="data/bazaar_conversions.json",
        ah_watchlist_file="data/ah_watchlist.json",
        conversion_mode="realistic",
        export_json=None,
        export_csv=None,
        show_rejected=False,
        allow_restricted_profile=False,
    )

    assert run_dashboard_menu(args, resolve_uuid=lambda http, name: None) == 0
    assert args.max_craft_cost == 2_500_000
    assert args.use_buy_order_cost is True
    assert args.recipes_file == "data/custom_recipes.json"


def test_dashboard_menu_reloads_active_settings_profile(monkeypatch, tmp_path, capsys):
    profile = tmp_path / "PalaMC_Test_20260617_selected_profile.json"
    profile.write_text('{"profile":{"members":{"abc":{"player_name":"PalaMC","coin_purse":123}}}}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SKYFLIP_SETTINGS_PROFILES_FILE", str(tmp_path / "settings_profiles.json"))
    preset_args = argparse.Namespace(
        days=7,
        min_profit=91_000,
        min_profit_percent=13,
        min_sales_per_day=2,
        max_median_sell_time_hours=12,
        cache_ttl=300,
        sections="craft",
        limit_per_section=10,
        spread_limit=None,
        min_spread_profit_per_unit=0,
        min_spread_volume_week=50_000,
        max_spread_depth_ratio=0.75,
        max_craft_cost=None,
        max_capital_percent_per_flip=35,
        use_buy_order_cost=False,
        recipes_file="data/craft_recipes.json",
        bazaar_conversions_file="data/bazaar_conversions.json",
        ah_watchlist_file="data/ah_watchlist.json",
        conversion_mode="realistic",
        show_rejected=False,
        allow_restricted_profile=False,
        refresh_interval=None,
    )
    save_settings_profile(preset_args, "Strict craft")
    inputs = iter(["q"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    args = argparse.Namespace(
        profile_file=None,
        player_name=None,
        budget=None,
        sections="craft,bazaar-spread,bazaar-order,bazaar-compression,ah-underpriced",
        refresh_interval=None,
        once=False,
        menu=False,
        days=7,
        min_profit=5_000,
        min_profit_percent=4,
        min_sales_per_day=2,
        max_median_sell_time_hours=12,
        cache_ttl=300,
        limit_per_section=10,
        spread_limit=None,
        min_spread_profit_per_unit=0,
        min_spread_volume_week=50_000,
        max_spread_depth_ratio=0.75,
        max_craft_cost=None,
        max_capital_percent_per_flip=35,
        use_buy_order_cost=False,
        recipes_file="data/craft_recipes.json",
        bazaar_conversions_file="data/bazaar_conversions.json",
        ah_watchlist_file="data/ah_watchlist.json",
        conversion_mode="realistic",
        export_json=None,
        export_csv=None,
        show_rejected=False,
        allow_restricted_profile=False,
    )

    assert run_dashboard_menu(args, resolve_uuid=lambda http, name: None) == 0
    assert args.min_profit == 91_000
    assert args.min_profit_percent == 13
    assert args.sections == "craft"
    output = capsys.readouterr().out
    assert "Preset:  Strict craft" in output


def make_menu_args(**overrides):
    values = {
        "profile_file": None,
        "player_name": None,
        "budget": None,
        "sections": "craft,bazaar-spread,bazaar-order,bazaar-compression,ah-underpriced",
        "refresh_interval": None,
        "once": False,
        "menu": False,
        "days": 7,
        "min_profit": 5_000,
        "min_profit_percent": 4,
        "min_sales_per_day": 2,
        "max_median_sell_time_hours": 12,
        "cache_ttl": 300,
        "limit_per_section": 10,
        "spread_limit": None,
        "min_spread_profit_per_unit": 0,
        "min_spread_volume_week": 50_000,
        "max_spread_depth_ratio": 0.75,
        "max_estimated_buy_minutes": None,
        "max_estimated_sell_minutes": None,
        "max_estimated_bottleneck_minutes": 240,
        "min_speed_confidence": 35,
        "conservative_speed": True,
        "max_craft_cost": None,
        "max_capital_percent_per_flip": 35,
        "use_buy_order_cost": False,
        "recipes_file": "data/craft_recipes.json",
        "bazaar_conversions_file": "data/bazaar_conversions.json",
        "ah_watchlist_file": "data/ah_watchlist.json",
        "conversion_mode": "realistic",
        "export_json": None,
        "export_csv": None,
        "show_rejected": False,
        "allow_restricted_profile": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)
