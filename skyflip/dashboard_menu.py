from __future__ import annotations

import argparse
import sys
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

from .dashboard import DEFAULT_SECTIONS, collect_dashboard_data
from .dashboard_results import (
    detail_lines,
    empty_state_hint,
    merge_module_data,
    module_candidate_rows,
    module_rejections,
    module_summary_lines,
    module_warnings,
    normalize_risk,
)
from .dashboard_modules import DASHBOARD_MODULES, DashboardModule
from .module_presets import ModulePreset, apply_module_preset, list_module_presets
from .module_recommendations import recommend_module_preset
from .cache import FileCache
from .http import HttpClient
from .onboarding import change_profile, ensure_profile_configuration, refresh_profile_now, reset_profile_configuration_with_confirmation
from .profile_fetcher import load_api_profile
from .profile_parser import load_profile
from .settings_profiles import (
    delete_module_settings_preset,
    delete_settings_profile,
    get_active_settings_profile,
    list_module_settings_presets,
    list_settings_profiles,
    load_active_settings_profile,
    load_module_settings_preset,
    load_settings_profile,
    save_module_settings_preset,
    save_settings_profile,
)
from .terminal import print_dashboard_section, print_dashboard_status
from .terminal_layout import get_terminal_size
from .user_config import (
    BUDGET_SOURCE_CUSTOM,
    BUDGET_SOURCE_PURSE,
    BUDGET_SOURCE_PURSE_BANK,
    HypixelUserConfig,
    budget_from_profile,
    budget_source_label,
    cache_age_seconds,
    load_user_config,
    save_user_config,
)
from .dashboard_menu_sorting import (
    _cycle_section_sort,
    _draw_sort_hint,
    _section_sort_key,
    _sorted_section_data,
    load_sort_preferences,
    save_sort_preferences,
)
from .dashboard_menu_ui import (
    SECTION_LABELS,
    _ask_float,
    _ask_int,
    _ask_optional_float,
    _badge,
    _capture_redraw_frame,
    _clear_screen,
    _coins,
    _draw_header,
    _draw_menu,
    _draw_simple_header,
    _draw_settings,
    _enter_terminal_app_mode,
    _ensure_talisman_attrs,
    _exit_terminal_app_mode,
    _interactive_menu_enabled,
    _muted,
    _optional_coins,
    _parse_sections,
    _pause,
    _pause_with_redraw,
    _read_key,
    _read_key_with_redraw,
    _section_count,
    _section_hint,
    _section_name,
    _section_summary,
    _select_menu,
    _short_path,
    _value,
    _write_redraw_frame,
)




TALISMAN_SORTS = ("score", "cost", "coin-per-mp", "rarity", "name", "status")
TALISMAN_SORT_LABELS = {
    "score": "score",
    "cost": "cost",
    "coin-per-mp": "coin/MP",
    "rarity": "rarity",
    "name": "name",
    "status": "status",
}
RARITY_SORT = {
    "common": 1,
    "uncommon": 2,
    "rare": 3,
    "epic": 4,
    "legendary": 5,
    "mythic": 6,
    "special": 7,
    "very special": 8,
}


@dataclass
class _MenuState:
    latest: object | None = None
    last_refresh: str | None = None
    status_message: str | None = None
    auto_refresh: bool = False
    stop_event: threading.Event | None = None
    thread: threading.Thread | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    section_sorts: dict[str, str] = field(default_factory=dict)
    persist_sort_preferences: bool = False
    module_presets: dict[str, str] = field(default_factory=dict)
    module_setup_seen: set[str] = field(default_factory=set)


def run_dashboard_menu(args: argparse.Namespace, *, resolve_uuid: Callable) -> int:
    _configure_output()
    _apply_detected_defaults(args)
    active_profile = load_active_settings_profile(args)
    if active_profile:
        setattr(args, "active_settings_profile", active_profile)
    state = _MenuState(section_sorts=load_sort_preferences(), persist_sort_preferences=True)
    if active_profile:
        state.status_message = f"Loaded settings preset: {active_profile}"
    terminal_app_mode = _enter_terminal_app_mode()
    try:
        while True:
            choice = _main_menu_choice(args, state)
            module = _module_from_choice(choice)
            if module is not None:
                _module_menu(args, state, module, resolve_uuid)
                continue
            if choice in {"r", "refresh"}:
                if not _ensure_required(args):
                    _pause()
                    continue
                _refresh_results(args, state, resolve_uuid=resolve_uuid, announce=True)
                continue
            if choice in {"s", "settings"}:
                _settings_menu(args, state, resolve_uuid)
                continue
            if choice in {"p", "profile"}:
                _profile_menu(args, state, resolve_uuid)
                state.latest = None
                state.last_refresh = None
                continue
            if choice in {"a", "auto", "automatic"}:
                if not _ensure_required(args):
                    _pause()
                    continue
                _toggle_auto_refresh(args, state, resolve_uuid=resolve_uuid)
                continue
            if choice in {"q", "quit", "exit"}:
                _stop_auto_refresh(state)
                return 0
            print("Unknown action.")
    finally:
        _stop_auto_refresh(state)
        _exit_terminal_app_mode(terminal_app_mode)


def should_open_dashboard_menu(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "menu", False)) or args.budget is None


def _configure_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _apply_detected_defaults(args: argparse.Namespace) -> None:
    if not args.sections:
        args.sections = ",".join(DEFAULT_SECTIONS)
    if not args.player_name and args.profile_file:
        args.player_name = _infer_player_name(Path(args.profile_file))
    if args.budget is None and args.profile_file:
        try:
            profile = load_profile(args.profile_file, player_name=args.player_name)
            args.budget = max(0.0, profile.available_coins)
            if not args.player_name:
                args.player_name = profile.player_name
        except Exception:
            pass


def _main_menu_choice(args: argparse.Namespace, state: _MenuState) -> str:
    module_entries = [
        (str(index), module.title, _module_hint(module))
        for index, module in enumerate(DASHBOARD_MODULES, 1)
    ]
    return _select_menu(
        "Modules",
        [
            *module_entries,
            ("r", "Refresh results", "scan enabled modules now"),
            ("s", "Settings", "filters, limits, scanned sections"),
            ("p", "Profile / budget", "profile JSON, player name, budget"),
            ("a", f"Automatic Refresh {'ON' if state.auto_refresh else 'OFF'}", "background refresh every 5 minutes"),
            ("q", "Quit", "leave dashboard"),
        ],
        args=args,
        state=state,
        show_counts=True,
        prompt="Choose an action",
    )


def _module_from_choice(choice: str) -> DashboardModule | None:
    if choice.isdigit():
        index = int(choice)
        if 1 <= index <= len(DASHBOARD_MODULES):
            return DASHBOARD_MODULES[index - 1]
    normalized = choice.strip().lower()
    for module in DASHBOARD_MODULES:
        if normalized in {module.key, module.title.lower()}:
            return module
    return None


def _module_hint(module: DashboardModule) -> str:
    hints = {
        "bazaar": "spread and order flips",
        "craft": "craft/list candidates",
        "accessories": "missing accessories and Magical Power",
        "compression": "manual conversion flips",
        "ah-bin": "manual underpriced BIN checks",
    }
    return hints.get(module.key, "")


def _module_menu(args: argparse.Namespace, state: _MenuState, module: DashboardModule, resolve_uuid: Callable) -> None:
    if module.key not in state.module_setup_seen:
        _module_first_setup_menu(args, state, module, resolve_uuid)
        state.module_setup_seen.add(module.key)
    while True:
        choice = _select_menu(
            module.title,
            [
                ("1", "Refresh", "scan enabled modules now"),
                ("2", "Results", "open this module's results"),
                ("3", "Recommended settings", "baseline values for this module"),
                ("4", "Active settings", "current values for this module"),
                ("5", "Advanced settings", "edit detailed controls"),
                ("6", "Custom presets", "save or load this module"),
                ("b", "Back", "return to modules"),
            ],
            args=args,
            state=state,
            show_counts=True,
            count_sections=module.sections,
            prompt="Choose an action",
            note=_restricted_profile_note(_profile_from_state(state), module),
        )
        if choice in {"b", "back", ""}:
            return
        if choice in {"1", "r", "refresh"}:
            _ensure_module_sections(args, module)
            if not _ensure_required(args):
                _pause()
                continue
            _refresh_module_results(args, state, module, resolve_uuid=resolve_uuid, announce=True)
            continue
        if choice in {"2", "results"}:
            _ensure_module_sections(args, module)
            _module_results_menu(args, state, module, resolve_uuid)
            continue
        if choice == "3":
            _module_recommended_settings_menu(args, state, module, resolve_uuid)
            continue
        if choice == "4":
            _module_settings_view(args, state, module, title="Active settings", rows=_active_module_settings(args, state, module))
            continue
        if choice == "5":
            _module_advanced_settings_menu(args, state, module, resolve_uuid)
            continue
        if choice == "6":
            _module_custom_presets_menu(args, state, module, resolve_uuid)
            continue
        print("Unknown action.")


def _results_sections_menu(args: argparse.Namespace, state: _MenuState) -> None:
    keys = ["summary", *DEFAULT_SECTIONS, "warnings", "rejected"]
    while True:
        data = state.latest
        if data is None:
            print("No results loaded. Refresh results first.")
            return
        entries = [
            (str(index), f"{SECTION_LABELS.get(key, _section_name(key))} {_badge(str(_section_count(data, key)))}", _section_hint(key))
            for index, key in enumerate(keys, 1)
        ]
        entries.extend([("r", "Refresh results", "scan again"), ("b", "Back", "return to dashboard")])
        choice = _select_menu("Sections", entries, args=args, state=state, prompt="Open section")
        if choice in {"b", "back", ""}:
            return
        if _handle_global_refresh(choice, args, state, None):
            continue
        if choice.isdigit() and 1 <= int(choice) <= len(keys):
            key = keys[int(choice) - 1]
            _show_result_section(args, state, key)
            continue
        print("Unknown section.")


def _module_first_setup_menu(args: argparse.Namespace, state: _MenuState, module: DashboardModule, resolve_uuid: Callable) -> None:
    presets = list_module_presets(module.key)
    while True:
        entries = [
            ("r", "Use profile recommendation", "load profile and apply the suggested preset"),
            *[
                (str(index), preset.title, f"risk {preset.risk_level}")
                for index, preset in enumerate(presets, 1)
            ],
            ("s", "Skip for now", "keep current settings"),
        ]
        choice = _select_menu(
            f"{module.title} Setup",
            entries,
            args=args,
            state=state,
            prompt="Choose settings",
            note="Choose settings before using this module. You can change them later from Recommended, Active, Advanced, or Custom presets.",
        )
        if choice in {"s", "skip", "b", "back", ""}:
            state.status_message = f"{module.title} setup skipped."
            return
        if choice in {"r", "recommended"}:
            profile = _load_profile_for_recommendations(args, state, resolve_uuid)
            if profile is None:
                _pause("Profile data is unavailable. Press Enter...")
                return
            recommendation = recommend_module_preset(profile, args, module.key)
            _apply_selected_module_preset(args, state, recommendation.preset)
            state.status_message = f"Applied {recommendation.preset.title} preset for {module.title}."
            return
        selected = _preset_from_choice(choice, presets)
        if selected is None:
            print("Unknown preset.")
            continue
        _apply_selected_module_preset(args, state, selected)
        state.status_message = f"Applied {selected.title} preset for {module.title}."
        return


def _module_results_menu(args: argparse.Namespace, state: _MenuState, module: DashboardModule, resolve_uuid: Callable) -> None:
    keys = list(module.result_sections)
    while True:
        data = state.latest
        if data is None:
            if not _ensure_required(args):
                _pause()
                return
            if not _refresh_module_results(args, state, module, resolve_uuid=resolve_uuid, announce=False):
                _pause()
                return
            data = state.latest
            if data is None:
                return
        note = "\n".join(module_summary_lines(data, module, last_refresh=state.last_refresh))
        entries = [
            (str(index), f"{SECTION_LABELS.get(key, _section_name(key))} {_badge(str(_module_section_count(data, module, key)))}", _section_hint(key))
            for index, key in enumerate(keys, 1)
        ]
        entries.extend([
            ("d", "Row details", "inspect one candidate"),
            ("r", "Refresh this module", "scan only this module"),
            ("b", "Back", f"return to {module.title}"),
        ])
        choice = _select_menu(f"{module.title} Results", entries, args=args, state=state, prompt="Open section", note=note)
        if choice in {"b", "back", ""}:
            return
        if choice in {"r", "refresh"}:
            _refresh_module_results(args, state, module, resolve_uuid=getattr(state, "resolve_uuid", None), announce=False)
            continue
        if choice in {"s", "summary"}:
            _show_module_summary(args, state, module)
            continue
        if choice in {"d", "details"}:
            _module_detail_menu(args, state, module)
            continue
        if choice.isdigit() and 1 <= int(choice) <= len(keys):
            key = keys[int(choice) - 1]
            if key == "summary":
                _show_module_summary(args, state, module)
                continue
            _show_result_section(args, state, key, module=module)
            continue
        print("Unknown section.")


def _show_module_summary(args: argparse.Namespace, state: _MenuState, module: DashboardModule) -> None:
    data = state.latest

    def draw_screen() -> None:
        _clear_screen()
        _draw_header(f"{module.title} / Results summary", args, state)
        if data is None:
            print("No results loaded. Refresh results first.")
            return
        for line in module_summary_lines(data, module, last_refresh=state.last_refresh):
            print(line)
        warnings = module_warnings(data, module)
        if warnings:
            print()
            print("Warnings")
            for warning in warnings[:8]:
                print(f"- {warning}")
        print()
        print(f"Filters: {_module_filter_summary(args, module)}")

    _pause_with_redraw(draw_screen)


def _module_detail_menu(args: argparse.Namespace, state: _MenuState, module: DashboardModule) -> None:
    while True:
        data = state.latest
        if data is None:
            print("No results loaded. Refresh results first.")
            return
        rows = module_candidate_rows(data, module)
        if not rows:
            def draw_screen() -> None:
                _clear_screen()
                _draw_header(f"{module.title} / Row details", args, state)
                print(empty_state_hint(module.key, module.sections[0]))

            _pause_with_redraw(draw_screen)
            return
        entries = [
            (str(index), _detail_entry_label(section, item), f"risk {normalize_risk(item)}")
            for index, (section, item) in enumerate(rows[:25], 1)
        ]
        choice = _select_menu(
            f"{module.title} Row details",
            [*entries, ("b", "Back", f"return to {module.title} results")],
            args=args,
            state=state,
            prompt="Open row",
            note=f"Filters: {_module_filter_summary(args, module)}",
        )
        if choice in {"b", "back", ""}:
            return
        if choice.isdigit() and 1 <= int(choice) <= len(entries):
            section, item = rows[int(choice) - 1]
            _show_row_detail(args, state, module, section, item)
            continue
        print("Unknown row.")


def _show_row_detail(args: argparse.Namespace, state: _MenuState, module: DashboardModule, section: str, item: object) -> None:
    def draw_screen() -> None:
        _clear_screen()
        _draw_header(f"{module.title} / Row detail", args, state)
        _draw_settings([("", key, value) for key, value in detail_lines(item, section)])

    _pause_with_redraw(draw_screen)


def _detail_entry_label(section: str, item: object) -> str:
    if section == "craft":
        return getattr(getattr(item, "recipe", None), "name", "Craft")
    if section == "bazaar-compression":
        return str(getattr(item, "name", "Conversion"))
    if section == "ah-underpriced":
        return str(getattr(item, "item", "AH item"))
    if section == "talisman":
        return str(getattr(getattr(item, "entry", None), "display_name", "Accessory"))
    return str(getattr(item, "product_id", "Product"))


def _module_section_count(data, module: DashboardModule, key: str) -> int | str:
    if key == "warnings":
        return len(module_warnings(data, module))
    if key == "rejected":
        return len(module_rejections(data, module))
    return _section_count(data, key)


def _module_filter_summary(args: argparse.Namespace, module: DashboardModule) -> str:
    if module.key == "bazaar":
        return (
            f"rows {args.spread_limit or args.limit_per_section}, "
            f"min volume {args.min_spread_volume_week:g}, "
            f"speed {'conservative' if args.conservative_speed else 'standard'}"
        )
    if module.key == "craft":
        return f"min profit {_coins(args.min_profit)}, margin {args.min_profit_percent:g}%, rows {args.limit_per_section}"
    if module.key == "accessories":
        _ensure_talisman_attrs(args)
        return f"view {args.accessory_view}, max price {_optional_coins(args.max_accessory_price)}, rows {args.max_accessory_recommendations}"
    if module.key == "compression":
        return f"mode {args.conversion_mode}, min profit {_coins(args.min_profit)}, rows {args.limit_per_section}"
    return f"min profit {_coins(args.min_profit)}, max sell {args.max_median_sell_time_hours:g}h, rows {args.limit_per_section}"


def _module_settings_view(
    args: argparse.Namespace,
    state: _MenuState,
    module: DashboardModule,
    *,
    title: str,
    rows: list[tuple[str, str, str]],
) -> None:
    def draw_screen() -> None:
        _clear_screen()
        _draw_header(f"{module.title} / {title}", args, state)
        _draw_settings(rows)

    _pause_with_redraw(draw_screen)


def _module_recommended_settings_menu(
    args: argparse.Namespace,
    state: _MenuState,
    module: DashboardModule,
    resolve_uuid: Callable,
) -> None:
    profile = _load_profile_for_recommendations(args, state, resolve_uuid)
    if profile is None:
        _pause("Profile data is unavailable. Press Enter...")
        return
    recommendation = recommend_module_preset(profile, args, module.key)
    presets = list_module_presets(module.key)
    note = _recommendation_note(recommendation.preset, recommendation.reasons)
    while True:
        entries = [
            ("a", f"Apply recommended: {recommendation.preset.title}", f"risk {recommendation.preset.risk_level}"),
            *[
                (str(index), preset.title, f"risk {preset.risk_level}")
                for index, preset in enumerate(presets, 1)
            ],
            ("b", "Back", f"return to {module.title}"),
        ]
        choice = _select_menu(
            f"{module.title} Recommended settings",
            entries,
            args=args,
            state=state,
            prompt="Preset to apply",
            note=note,
        )
        if choice in {"b", "back", ""}:
            return
        selected = recommendation.preset if choice == "a" else _preset_from_choice(choice, presets)
        if selected is None:
            print("Unknown preset.")
            continue
        _apply_selected_module_preset(args, state, selected)
        _pause(f"Applied {selected.title} preset. Press Enter...")
        return


def _load_profile_for_recommendations(
    args: argparse.Namespace,
    state: _MenuState,
    resolve_uuid: Callable,
) -> object | None:
    loaded = getattr(getattr(state, "latest", None), "profile", None)
    if loaded is not None:
        return loaded
    _apply_detected_defaults(args)
    try:
        if args.profile_file:
            player_uuid = resolve_uuid(_profile_http(args), args.player_name) if args.player_name else None
            profile = load_profile(args.profile_file, player_name=args.player_name, player_uuid=player_uuid)
        else:
            http = _profile_http(args)
            ensure_profile_configuration(http, force_setup=bool(getattr(args, "setup", False)))
            profile = load_api_profile(
                http,
                force_refresh=bool(getattr(args, "refresh_profile", False)),
                ttl_seconds=int(getattr(args, "profile_cache_ttl", 600) or 600),
            ).profile
    except Exception as exc:  # noqa: BLE001 - keep menu usable if profile loading fails
        state.status_message = f"Profile recommendation failed: {exc}"
        print(f"Profile recommendation failed: {exc}")
        return None
    if args.budget is None:
        config = None if args.profile_file else load_user_config()
        args.budget = budget_from_profile(profile, config)
    if not args.player_name:
        args.player_name = profile.player_name
    return profile


def _recommendation_note(preset: ModulePreset, reasons: tuple[str, ...]) -> str:
    lines = [
        f"Recommended preset: {preset.title}",
        f"Risk level: {preset.risk_level}",
        "Why this recommendation?",
        *[f"- {reason}" for reason in reasons],
        "",
        "Settings changed:",
        *[f"- {_setting_label(field)}: {_format_setting_value(value)}" for field, value in preset.settings_patch.items()],
    ]
    return "\n".join(lines)


def _preset_from_choice(choice: str, presets: tuple[ModulePreset, ...]) -> ModulePreset | None:
    if not choice.isdigit():
        return None
    index = int(choice)
    if 1 <= index <= len(presets):
        return presets[index - 1]
    return None


def _apply_selected_module_preset(args: argparse.Namespace, state: _MenuState, preset: ModulePreset) -> None:
    apply_module_preset(args, preset)
    state.module_presets[preset.module_key] = preset.title
    state.status_message = f"Applied {preset.title} preset for {_module_title(preset.module_key)}."


def _module_title(module_key: str) -> str:
    for module in DASHBOARD_MODULES:
        if module.key == module_key:
            return module.title
    return module_key


def _setting_label(field: str) -> str:
    labels = {
        "spread_limit": "Rows shown",
        "min_spread_profit_per_unit": "Min spread profit",
        "min_spread_volume_week": "Min weekly volume",
        "max_spread_depth_ratio": "Max depth ratio",
        "max_capital_percent_per_flip": "Capital per flip",
        "max_estimated_bottleneck_minutes": "Max bottleneck",
        "min_speed_confidence": "Speed confidence",
        "conservative_speed": "Conservative speed",
        "min_profit": "Min profit",
        "min_profit_percent": "Min margin",
        "min_sales_per_day": "Min sales per day",
        "max_median_sell_time_hours": "Max sell time",
        "use_buy_order_cost": "Use instant buy cost",
        "accessory_view": "View",
        "accessory_sort": "Sort",
        "max_accessory_price": "Max accessory price",
        "max_accessory_recommendations": "Rows shown",
        "include_locked_accessories": "Include locked",
        "include_uncertain_accessories": "Include uncertain",
        "include_manual_unlocks": "Include manual unlocks",
        "include_ah_accessories": "Include AH items",
        "include_craftable_accessories": "Include craftable items",
        "only_craftable": "Only craftable",
        "only_ah": "Only AH",
        "show_locked": "Show locked",
        "conversion_mode": "Mode",
        "limit_per_section": "Rows shown",
    }
    return labels.get(field, field.replace("_", " ").title())


def _format_setting_value(value: object) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if value is None:
        return "none"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _recommended_module_settings(args: argparse.Namespace, module: DashboardModule) -> list[tuple[str, str, str]]:
    if module.key == "bazaar":
        return [
            ("1", "Budget", _coins(args.budget)),
            ("2", "Rows shown", str(args.spread_limit or args.limit_per_section)),
            ("3", "Capital per flip", f"{args.max_capital_percent_per_flip:g}%"),
            ("4", "Speed strictness", "conservative" if getattr(args, "conservative_speed", True) else "standard"),
        ]
    if module.key == "craft":
        return [
            ("1", "Min profit", _coins(args.min_profit)),
            ("2", "Min margin", f"{args.min_profit_percent:g}%"),
            ("3", "Max craft cost", _optional_coins(args.max_craft_cost)),
            ("4", "Ingredient pricing", "instant buy" if args.use_buy_order_cost else "sell-order side"),
        ]
    if module.key == "accessories":
        _ensure_talisman_attrs(args)
        return [
            ("1", "Rows shown", str(args.max_accessory_recommendations)),
            ("2", "Max price", _optional_coins(args.max_accessory_price)),
            ("3", "Craftable items", "shown" if args.include_craftable_accessories else "hidden"),
            ("4", "AH items", "shown" if args.include_ah_accessories else "hidden"),
        ]
    if module.key == "compression":
        return [
            ("1", "Mode", args.conversion_mode),
            ("2", "Rows shown", str(args.limit_per_section)),
            ("3", "Capital per flip", f"{args.max_capital_percent_per_flip:g}%"),
            ("4", "Min profit", _coins(args.min_profit)),
        ]
    return [
        ("1", "Rows shown", str(args.limit_per_section)),
        ("2", "Min profit", _coins(args.min_profit)),
        ("3", "Max sell time", f"{args.max_median_sell_time_hours:g}h"),
        ("4", "Manual checks", "required"),
    ]


def _active_module_settings(args: argparse.Namespace, state: _MenuState, module: DashboardModule) -> list[tuple[str, str, str]]:
    rows = _recommended_module_settings(args, module)
    scanned = ", ".join(module.sections)
    preset = state.module_presets.get(module.key, "not applied")
    return [("p", "Applied preset", preset), *rows, ("s", "Mapped sections", scanned)]


def _module_advanced_settings_menu(
    args: argparse.Namespace,
    state: _MenuState,
    module: DashboardModule,
    resolve_uuid: Callable,
) -> None:
    if module.key == "bazaar":
        _bazaar_spread_settings_menu(args, state, resolve_uuid)
        return
    if module.key == "craft":
        _craft_flips_settings_menu(args, state, resolve_uuid)
        return
    if module.key == "accessories":
        _talisman_settings_menu(args, state, resolve_uuid)
        return
    if module.key == "compression":
        _compression_settings_menu(args, state, resolve_uuid)
        return
    _ah_bin_settings_menu(args, state, resolve_uuid)


def _module_custom_presets_menu(
    args: argparse.Namespace,
    state: _MenuState,
    module: DashboardModule,
    resolve_uuid: Callable,
) -> None:
    while True:
        presets = list_module_settings_presets(module.key)
        choice = _select_menu(
            f"{module.title} custom presets",
            _refreshable_entries([
                ("s", "Save current preset", "create or overwrite a module preset"),
                ("l", "Load preset", "apply a saved module preset"),
                ("d", "Delete preset", "remove a saved module preset"),
                ("b", "Back", f"return to {module.title}"),
            ]),
            args=args,
            state=state,
            prompt="Choose preset action",
            note=_module_presets_note(presets),
        )
        if _handle_global_refresh(choice, args, state, resolve_uuid):
            continue
        if choice in {"b", "back", ""}:
            return
        if choice in {"s", "save"}:
            _save_module_settings_preset_menu(args, state, module)
            continue
        if choice in {"l", "load"}:
            _load_module_settings_preset_menu(args, state, module, resolve_uuid)
            continue
        if choice in {"d", "delete"}:
            _delete_module_settings_preset_menu(args, state, module, resolve_uuid)
            continue
        print("Unknown preset action.")


def _save_module_settings_preset_menu(args: argparse.Namespace, state: _MenuState, module: DashboardModule) -> None:
    _clear_screen()
    _draw_header(f"{module.title} / Save custom preset", args, state)
    name = input("Preset name: ").strip()
    if not name:
        _pause("No name entered. Press Enter...")
        return
    try:
        save_module_settings_preset(args, module.key, name)
    except ValueError as exc:
        _pause(f"{exc}. Press Enter...")
        return
    clean_name = " ".join(name.strip().split())
    state.module_presets[module.key] = f"Custom: {clean_name}"
    state.status_message = f"Saved {clean_name} preset for {module.title}."
    _pause(f"Saved {clean_name} preset. Press Enter...")


def _load_module_settings_preset_menu(
    args: argparse.Namespace,
    state: _MenuState,
    module: DashboardModule,
    resolve_uuid: Callable,
) -> None:
    name = _choose_module_settings_preset("Load custom preset", args, state, module, resolve_uuid)
    if not name:
        return
    if load_module_settings_preset(args, module.key, name):
        state.module_presets[module.key] = f"Custom: {name}"
        state.status_message = f"Loaded {name} preset for {module.title}."
        _pause(f"Loaded {name} preset. Press Enter...")
    else:
        _pause("Preset not found. Press Enter...")


def _delete_module_settings_preset_menu(
    args: argparse.Namespace,
    state: _MenuState,
    module: DashboardModule,
    resolve_uuid: Callable,
) -> None:
    name = _choose_module_settings_preset("Delete custom preset", args, state, module, resolve_uuid)
    if not name:
        return
    if delete_module_settings_preset(module.key, name):
        if state.module_presets.get(module.key) == f"Custom: {name}":
            state.module_presets.pop(module.key, None)
        state.status_message = f"Deleted {name} preset for {module.title}."
        _pause(f"Deleted {name} preset. Press Enter...")
    else:
        _pause("Preset not found. Press Enter...")


def _choose_module_settings_preset(
    title: str,
    args: argparse.Namespace,
    state: _MenuState,
    module: DashboardModule,
    resolve_uuid: Callable,
) -> str | None:
    presets = list_module_settings_presets(module.key)
    if not presets:
        _clear_screen()
        _draw_header(f"{module.title} / {title}", args, state)
        _pause("No saved module presets. Press Enter...")
        return None
    names = sorted(presets)
    entries = [(str(index), name, _module_settings_summary(module.key, presets[name])) for index, name in enumerate(names, 1)]
    choice = _select_menu(
        f"{module.title} / {title}",
        _refreshable_entries([*entries, ("b", "Back", "return")]),
        args=args,
        state=state,
        prompt="Choose preset",
    )
    if _handle_global_refresh(choice, args, state, resolve_uuid):
        return None
    if choice in {"b", "back", ""}:
        return None
    if choice.isdigit() and 1 <= int(choice) <= len(names):
        return names[int(choice) - 1]
    return None


def _module_presets_note(presets: dict[str, dict]) -> str:
    if not presets:
        return "No saved custom presets for this module yet."
    names = ", ".join(sorted(presets)[:6])
    if len(presets) > 6:
        names += f", +{len(presets) - 6} more"
    return f"Saved presets: {names}"


def _module_settings_summary(module_key: str, settings: dict) -> str:
    if module_key == "bazaar":
        rows = settings.get("spread_limit") or settings.get("limit_per_section", "?")
        capital = settings.get("max_capital_percent_per_flip", "?")
        speed = "conservative" if settings.get("conservative_speed", True) else "standard"
        return f"{rows} rows / {capital}% capital / {speed}"
    if module_key == "craft":
        return f"min {_coins(settings.get('min_profit'))} / {settings.get('min_profit_percent', '?')}%"
    if module_key == "accessories":
        return f"{settings.get('accessory_view', '?')} / {settings.get('max_accessory_recommendations', '?')} rows"
    if module_key == "compression":
        return f"{settings.get('conversion_mode', '?')} / min {_coins(settings.get('min_profit'))}"
    return f"{settings.get('limit_per_section', '?')} rows / min {_coins(settings.get('min_profit'))}"


def _compression_settings_menu(args: argparse.Namespace, state: _MenuState, resolve_uuid: Callable) -> None:
    while True:
        choice = _select_menu(
            "Bazaar Compression settings",
            _refreshable_entries([
                ("1", f"Mode  {args.conversion_mode}", "conservative or realistic"),
                ("2", f"Rows shown  {args.limit_per_section}", "result count"),
                ("3", f"Min profit  {_value(args.min_profit, coins=True)}", "minimum expected profit"),
                ("4", f"Min profit percent  {args.min_profit_percent:g}%", "minimum margin"),
                ("5", f"Max capital per flip  {args.max_capital_percent_per_flip:g}%", "budget cap per candidate"),
                ("6", f"Conversions file  {_short_path(args.bazaar_conversions_file)}", "conversion data file"),
                ("b", "Back", "return to module"),
            ]),
            args=args,
            state=state,
            prompt="Setting to edit",
        )
        if _handle_global_refresh(choice, args, state, resolve_uuid):
            continue
        if choice in {"b", "back", ""}:
            return
        if choice == "1":
            args.conversion_mode = "conservative" if args.conversion_mode == "realistic" else "realistic"
        elif choice == "2":
            args.limit_per_section = _ask_int("Rows shown", args.limit_per_section)
        elif choice == "3":
            args.min_profit = _ask_float("Min profit", args.min_profit)
        elif choice == "4":
            args.min_profit_percent = _ask_float("Min profit percent", args.min_profit_percent)
        elif choice == "5":
            args.max_capital_percent_per_flip = _ask_float("Max capital percent per flip", args.max_capital_percent_per_flip)
        elif choice == "6":
            value = input(f"Conversions file [{args.bazaar_conversions_file}]: ").strip()
            if value:
                args.bazaar_conversions_file = value


def _ah_bin_settings_menu(args: argparse.Namespace, state: _MenuState, resolve_uuid: Callable) -> None:
    while True:
        choice = _select_menu(
            "AH BIN Finder settings",
            _refreshable_entries([
                ("1", f"Rows shown  {args.limit_per_section}", "result count"),
                ("2", f"Min profit  {_value(args.min_profit, coins=True)}", "minimum expected profit"),
                ("3", f"Min profit percent  {args.min_profit_percent:g}%", "minimum margin"),
                ("4", f"Max median sell time hours  {args.max_median_sell_time_hours:g}", "sell-time ceiling"),
                ("5", f"Watchlist file  {_short_path(args.ah_watchlist_file)}", "AH watchlist data file"),
                ("b", "Back", "return to module"),
            ]),
            args=args,
            state=state,
            prompt="Setting to edit",
        )
        if _handle_global_refresh(choice, args, state, resolve_uuid):
            continue
        if choice in {"b", "back", ""}:
            return
        if choice == "1":
            args.limit_per_section = _ask_int("Rows shown", args.limit_per_section)
        elif choice == "2":
            args.min_profit = _ask_float("Min profit", args.min_profit)
        elif choice == "3":
            args.min_profit_percent = _ask_float("Min profit percent", args.min_profit_percent)
        elif choice == "4":
            args.max_median_sell_time_hours = _ask_float("Max median sell time hours", args.max_median_sell_time_hours)
        elif choice == "5":
            value = input(f"Watchlist file [{args.ah_watchlist_file}]: ").strip()
            if value:
                args.ah_watchlist_file = value


def _ensure_module_sections(args: argparse.Namespace, module: DashboardModule) -> None:
    selected = set(_parse_sections(args.sections))
    selected.update(module.sections)
    args.sections = ",".join(section for section in DEFAULT_SECTIONS if section in selected)


def _settings_menu(args: argparse.Namespace, state: _MenuState, resolve_uuid: Callable) -> None:
    while True:
        choice = _select_menu(
            "Settings",
            _refreshable_entries([
                ("1", f"Rows shown  {args.limit_per_section}", "default result count"),
                ("2", f"Show rejected  {'yes' if args.show_rejected else 'no'}", "show filtered candidates"),
                ("3", "Settings profiles", "save, load, delete global settings"),
                ("4", "Advanced global settings", "cache, refresh, scan compatibility"),
                ("b", "Back", "return to dashboard"),
            ]),
            args=args,
            state=state,
            prompt="Setting to edit",
            note="Module thresholds are edited inside each module.",
        )
        if _handle_global_refresh(choice, args, state, resolve_uuid):
            continue
        if choice in {"b", "back", ""}:
            return
        if choice == "1":
            args.limit_per_section = _ask_int("Rows shown", args.limit_per_section)
        elif choice == "2":
            args.show_rejected = not args.show_rejected
        elif choice == "3":
            _settings_profiles_menu(args, state, resolve_uuid)
        elif choice == "4":
            _advanced_global_settings_menu(args, state, resolve_uuid)
        else:
            print("Unknown setting.")


def _advanced_global_settings_menu(args: argparse.Namespace, state: _MenuState, resolve_uuid: Callable) -> None:
    while True:
        choice = _select_menu(
            "Advanced global settings",
            _refreshable_entries([
                ("1", f"Cache TTL  {args.cache_ttl}s", "API cache lifetime"),
                ("2", f"Refresh interval  {args.refresh_interval or 'manual'}", "automatic refresh interval"),
                ("3", f"Scanned sections  {_section_summary(args.sections)}", "compatibility scan selection"),
                ("4", f"Allow restricted profile  {'yes' if args.allow_restricted_profile else 'no'}", "include restricted profiles"),
                ("5", "Reset Hypixel profile configuration", "remove saved profile and API key"),
                ("b", "Back", "return to settings"),
            ]),
            args=args,
            state=state,
            prompt="Setting to edit",
        )
        if _handle_global_refresh(choice, args, state, resolve_uuid):
            continue
        if choice in {"b", "back", ""}:
            return
        if choice == "1":
            args.cache_ttl = _ask_int("Cache TTL seconds", args.cache_ttl)
        elif choice == "2":
            value = _ask_int("Refresh interval seconds", int(args.refresh_interval or 300))
            args.refresh_interval = None if value <= 0 else value
        elif choice == "3":
            _scan_sections_menu(args, state, resolve_uuid)
        elif choice == "4":
            args.allow_restricted_profile = not args.allow_restricted_profile
        elif choice == "5":
            if reset_profile_configuration_with_confirmation():
                args.profile_file = None
                args.player_name = None
                args.budget = None
        else:
            print("Unknown setting.")


def _craft_flips_settings_menu(args: argparse.Namespace, state: _MenuState, resolve_uuid: Callable) -> None:
    while True:
        choice = _select_menu(
            "Craft flips settings",
            _refreshable_entries([
                ("1", f"Max craft cost  {_optional_coins(args.max_craft_cost)}", "reject crafts above this cost"),
                ("2", f"Use instant buy cost  {'yes' if args.use_buy_order_cost else 'no'}", "price ingredients from the instant-buy side"),
                ("3", f"Min profit  {_value(args.min_profit, coins=True)}", "minimum expected profit"),
                ("4", f"Min profit percent  {args.min_profit_percent:g}%", "minimum margin"),
                ("5", f"Min sales per day  {args.min_sales_per_day:g}", "market speed floor"),
                ("6", f"Max median sell time hours  {args.max_median_sell_time_hours:g}", "sell-time ceiling"),
                ("7", f"Max capital per flip  {args.max_capital_percent_per_flip:g}%", "budget cap per candidate"),
                ("8", f"Recipes file  {_short_path(args.recipes_file)}", "craft recipe data file"),
                ("b", "Back", "return to settings"),
            ]),
            args=args,
            state=state,
            prompt="Setting to edit",
        )
        if _handle_global_refresh(choice, args, state, resolve_uuid):
            continue
        if choice in {"b", "back", ""}:
            return
        if choice == "1":
            args.max_craft_cost = _ask_optional_float("Max craft cost", args.max_craft_cost)
        elif choice == "2":
            args.use_buy_order_cost = not args.use_buy_order_cost
        elif choice == "3":
            args.min_profit = _ask_float("Min profit", args.min_profit)
        elif choice == "4":
            args.min_profit_percent = _ask_float("Min profit percent", args.min_profit_percent)
        elif choice == "5":
            args.min_sales_per_day = _ask_float("Min sales per day", args.min_sales_per_day)
        elif choice == "6":
            args.max_median_sell_time_hours = _ask_float("Max median sell time hours", args.max_median_sell_time_hours)
        elif choice == "7":
            args.max_capital_percent_per_flip = _ask_float("Max capital percent per flip", args.max_capital_percent_per_flip)
        elif choice == "8":
            value = input(f"Recipes file [{args.recipes_file}]: ").strip()
            if value:
                args.recipes_file = value


def _bazaar_spread_settings_menu(args: argparse.Namespace, state: _MenuState, resolve_uuid: Callable) -> None:
    while True:
        choice = _select_menu(
            "Bazaar spread settings",
            _refreshable_entries([
                ("1", f"Spread limit  {args.spread_limit or args.limit_per_section}", "rows shown for spread flips"),
                ("2", f"Min spread profit/unit  {_value(args.min_spread_profit_per_unit, coins=True)}", "per-unit spread floor"),
                ("3", f"Min spread weekly volume  {_value(args.min_spread_volume_week, coins=True)}", "movement floor"),
                ("4", f"Max spread depth ratio  {args.max_spread_depth_ratio:g}", "wall tolerance"),
                ("5", f"Max bottleneck minutes  {args.max_estimated_bottleneck_minutes:g}", "speed ceiling"),
                ("6", f"Min speed confidence  {args.min_speed_confidence:g}", "speed confidence floor"),
                ("7", f"Conservative speed  {'yes' if args.conservative_speed else 'no'}", "speed model strictness"),
                ("8", f"Max capital per flip  {args.max_capital_percent_per_flip:g}%", "budget cap per candidate"),
                ("b", "Back", "return to settings"),
            ]),
            args=args,
            state=state,
            prompt="Setting to edit",
        )
        if _handle_global_refresh(choice, args, state, resolve_uuid):
            continue
        if choice in {"b", "back", ""}:
            return
        if choice == "1":
            args.spread_limit = _ask_int("Spread limit", args.spread_limit or args.limit_per_section)
        elif choice == "2":
            args.min_spread_profit_per_unit = _ask_float("Min spread profit per unit", args.min_spread_profit_per_unit)
        elif choice == "3":
            args.min_spread_volume_week = _ask_float("Min spread weekly volume", args.min_spread_volume_week)
        elif choice == "4":
            args.max_spread_depth_ratio = _ask_float("Max spread depth ratio", args.max_spread_depth_ratio)
        elif choice == "5":
            args.max_estimated_bottleneck_minutes = _ask_float("Max bottleneck minutes", args.max_estimated_bottleneck_minutes)
        elif choice == "6":
            args.min_speed_confidence = _ask_float("Min speed confidence", args.min_speed_confidence)
        elif choice == "7":
            args.conservative_speed = not args.conservative_speed
        elif choice == "8":
            args.max_capital_percent_per_flip = _ask_float("Max capital percent per flip", args.max_capital_percent_per_flip)


def _talisman_settings_menu(args: argparse.Namespace, state: _MenuState, resolve_uuid: Callable) -> None:
    _ensure_talisman_attrs(args)
    while True:
        choice = _select_menu(
            "Accessories Helper settings",
            _refreshable_entries([
                ("1", f"Max accessory price  {_optional_coins(args.max_accessory_price)}", "hide pricier recommendations"),
                ("2", f"Max rows  {args.max_accessory_recommendations}", "rows shown in Recommended"),
                ("3", f"Max AH checks  {args.max_accessory_ah_checks}", "bounded SkyCofl price checks per refresh"),
                ("4", f"Include locked  {'yes' if args.include_locked_accessories else 'no'}", "show locked/unknown rows"),
                ("5", f"Include uncertain  {'yes' if args.include_uncertain_accessories else 'no'}", "show entries marked uncertain"),
                ("6", f"Include manual unlocks  {'yes' if args.include_manual_unlocks else 'no'}", "quest/race/soulbound suggestions"),
                ("7", f"Include AH items  {'yes' if args.include_ah_accessories else 'no'}", "fetch and show AH availability"),
                ("8", f"Include craftable items  {'yes' if args.include_craftable_accessories else 'no'}", "show craftable suggestions"),
                ("9", f"Sort key  {args.accessory_sort}", "score, rarity, price, craft-cost, coin-per-mp, name"),
                ("10", f"View  {args.accessory_view}", "recommended, craftable, buy-ah, upgrades, locked, owned-covered"),
                ("11", f"Rarity filter  {args.accessory_rarity or 'all'}", "comma-separated rarities"),
                ("12", f"Search  {args.accessory_search or 'none'}", "filter by accessory name"),
                ("13", "Refresh AH prices", "refresh results now"),
                ("14", f"Accessories file  {_short_path(args.accessories_file)}", "local JSON database"),
                ("b", "Back", "return to settings"),
            ]),
            args=args,
            state=state,
            prompt="Setting to edit",
        )
        if _handle_global_refresh(choice, args, state, resolve_uuid):
            continue
        if choice in {"b", "back", ""}:
            return
        if choice == "1":
            args.max_accessory_price = _ask_optional_float("Max accessory price", args.max_accessory_price)
        elif choice == "2":
            args.max_accessory_recommendations = _ask_int("Max rows", args.max_accessory_recommendations)
        elif choice == "3":
            args.max_accessory_ah_checks = _ask_int("Max AH checks", args.max_accessory_ah_checks)
        elif choice == "4":
            args.include_locked_accessories = not args.include_locked_accessories
            args.show_locked = args.include_locked_accessories
        elif choice == "5":
            args.include_uncertain_accessories = not args.include_uncertain_accessories
        elif choice == "6":
            args.include_manual_unlocks = not args.include_manual_unlocks
        elif choice == "7":
            args.include_ah_accessories = not args.include_ah_accessories
        elif choice == "8":
            args.include_craftable_accessories = not args.include_craftable_accessories
        elif choice == "9":
            value = input(f"Sort key [{args.accessory_sort}]: ").strip()
            if value:
                args.accessory_sort = value
        elif choice == "10":
            value = input(f"View [{args.accessory_view}]: ").strip()
            if value:
                args.accessory_view = value
        elif choice == "11":
            args.accessory_rarity = input(f"Rarity filter [{args.accessory_rarity or 'all'}]: ").strip()
        elif choice == "12":
            args.accessory_search = input(f"Search [{args.accessory_search or 'none'}] (empty clears): ").strip() or None
        elif choice == "13":
            _handle_global_refresh("r", args, state, resolve_uuid)
        elif choice == "14":
            value = input(f"Accessories file [{args.accessories_file}]: ").strip()
            if value:
                args.accessories_file = value


def _settings_profiles_menu(args: argparse.Namespace, state: _MenuState, resolve_uuid: Callable) -> None:
    while True:
        profiles = list_settings_profiles()
        entries = _refreshable_entries([
            ("s", "Save current settings", "create or overwrite a named profile"),
            ("l", "Load profile", "apply a saved settings profile"),
            ("d", "Delete profile", "remove a saved settings profile"),
            ("b", "Back", "return to settings"),
        ])
        note = _settings_profiles_note(profiles)
        choice = _select_menu(
            "Settings profiles",
            entries,
            args=args,
            state=state,
            prompt="Choose profile action",
            note=note,
        )
        if _handle_global_refresh(choice, args, state, resolve_uuid):
            continue
        if choice in {"b", "back", ""}:
            return
        if choice in {"s", "save"}:
            _save_settings_profile_menu(args)
        elif choice in {"l", "load"}:
            _load_settings_profile_menu(args, state, resolve_uuid)
        elif choice in {"d", "delete"}:
            _delete_settings_profile_menu(args, state, resolve_uuid)


def _save_settings_profile_menu(args: argparse.Namespace) -> None:
    _clear_screen()
    _draw_simple_header("Save settings profile")
    name = input("Profile name: ").strip()
    if not name:
        _pause("No name entered. Press Enter...")
        return
    try:
        save_settings_profile(args, name)
    except ValueError as exc:
        _pause(f"{exc}. Press Enter...")
        return
    setattr(args, "active_settings_profile", " ".join(name.strip().split()))
    _pause(f"Saved settings profile '{name}'. Press Enter...")


def _load_settings_profile_menu(args: argparse.Namespace, state: _MenuState, resolve_uuid: Callable) -> None:
    name = _choose_settings_profile("Load settings profile", args, state, resolve_uuid)
    if not name:
        return
    if load_settings_profile(args, name):
        setattr(args, "active_settings_profile", name)
        _pause(f"Loaded settings profile '{name}'. Press Enter...")
    else:
        _pause("Profile not found. Press Enter...")


def _delete_settings_profile_menu(args: argparse.Namespace, state: _MenuState, resolve_uuid: Callable) -> None:
    name = _choose_settings_profile("Delete settings profile", args, state, resolve_uuid)
    if not name:
        return
    if delete_settings_profile(name):
        if getattr(args, "active_settings_profile", None) == name:
            setattr(args, "active_settings_profile", None)
        _pause(f"Deleted settings profile '{name}'. Press Enter...")
    else:
        _pause("Profile not found. Press Enter...")


def _choose_settings_profile(title: str, args: argparse.Namespace, state: _MenuState, resolve_uuid: Callable) -> str | None:
    profiles = list_settings_profiles()
    if not profiles:
        _clear_screen()
        _draw_simple_header(title)
        _pause("No saved settings profiles. Press Enter...")
        return None
    names = sorted(profiles)
    entries = [(str(index), name, _profile_settings_summary(profiles[name])) for index, name in enumerate(names, 1)]
    entries = _refreshable_entries([*entries, ("b", "Back", "return")])
    choice = _select_menu(title, entries, args=args, state=state, prompt="Choose profile")
    if _handle_global_refresh(choice, args, state, resolve_uuid):
        return None
    if choice in {"b", "back", ""}:
        return None
    if choice.isdigit() and 1 <= int(choice) <= len(names):
        return names[int(choice) - 1]
    return None


def _settings_profiles_note(profiles: dict) -> str:
    if not profiles:
        return "No saved settings profiles yet."
    active = get_active_settings_profile()
    names = ", ".join((f"*{name}" if name == active else name) for name in sorted(profiles)[:6])
    if len(profiles) > 6:
        names += f", +{len(profiles) - 6} more"
    return f"Saved profiles: {names}   (* active)"


def _profile_settings_summary(settings: dict) -> str:
    min_profit = settings.get("min_profit", "?")
    min_percent = settings.get("min_profit_percent", "?")
    sections = settings.get("sections", "")
    section_count = len(_parse_sections(sections)) if isinstance(sections, str) else "?"
    return f"min {_coins(min_profit)} / {min_percent}% / {section_count} sections"


def _scan_sections_menu(args: argparse.Namespace, state: _MenuState, resolve_uuid: Callable) -> None:
    selected = set(_parse_sections(args.sections))
    keys = list(DEFAULT_SECTIONS)
    while True:
        entries = []
        for index, key in enumerate(keys, 1):
            marker = "[x]" if key in selected else "[ ]"
            entries.append((str(index), f"{marker} {SECTION_LABELS.get(key, key)}", "toggle scan"))
        entries = _refreshable_entries([*entries, ("a", "Enable all", "scan every section"), ("b", "Back", "save and return")])
        choice = _select_menu(
            "Scanned sections",
            entries,
            args=args,
            state=state,
            prompt="Toggle scanned section",
            note="These control what the next refresh scans. The Sections page controls what you view.",
        )
        if _handle_global_refresh(choice, args, state, resolve_uuid):
            continue
        if choice in {"b", "back", ""}:
            args.sections = ",".join(key for key in keys if key in selected)
            return
        if choice in {"a", "all"}:
            selected = set(keys)
            continue
        if choice.isdigit() and 1 <= int(choice) <= len(keys):
            key = keys[int(choice) - 1]
            if key in selected:
                selected.remove(key)
            else:
                selected.add(key)
            continue
        print("Unknown section choice.")


def _profile_menu(args: argparse.Namespace, state: _MenuState, resolve_uuid: Callable) -> None:
    while True:
        profile = _profile_from_state(state) or _load_local_profile_for_menu(args)
        config = load_user_config()
        choice = _select_menu(
            "Profile / budget",
            _refreshable_entries([
                ("1", f"Profile source  {_profile_source_label(args)}", "API by default; local file only when set"),
                ("2", f"Player name  {args.player_name or 'not set'}", "Minecraft name"),
                ("3", f"Budget  {_profile_budget_label(args, config, profile)}", "purse, purse + bank, or custom"),
                ("4", "Change Hypixel profile", "choose username/profile through Hypixel API"),
                ("5", "Refresh profile now", "fetch fresh profile data through Hypixel API"),
                ("6", "Advanced: local profile file", "developer/debug fallback"),
                ("7", "Reset Hypixel profile configuration", "remove saved profile and API key"),
                ("8", f"Data freshness  {_profile_freshness_label(args, state)}", "fresh, cached, stale, unavailable"),
                ("b", "Back", "return to dashboard"),
            ]),
            args=args,
            state=state,
            prompt="Field to edit",
            note=_profile_menu_note(args, state, profile, config),
        )
        if _handle_global_refresh(choice, args, state, resolve_uuid):
            continue
        if choice in {"b", "back", ""}:
            return
        if choice == "1":
            value = input("Local profile JSON path (empty clears local fallback): ").strip()
            args.profile_file = value or None
            if value and not args.player_name:
                args.player_name = _infer_player_name(Path(value))
            _apply_detected_defaults(args)
        elif choice == "2":
            value = input("Player name: ").strip()
            if value:
                args.player_name = value
        elif choice == "3":
            _budget_source_menu(args, state)
        elif choice == "4":
            change_profile(_profile_http(args))
            args.profile_file = None
            args.budget = None
        elif choice == "5":
            refresh_profile_now(_profile_http(args))
            args.profile_file = None
            args.budget = None
        elif choice == "6":
            value = input("Profile JSON path: ").strip()
            if value:
                args.profile_file = value
                if not args.player_name:
                    args.player_name = _infer_player_name(Path(value))
                _apply_detected_defaults(args)
        elif choice == "7":
            if reset_profile_configuration_with_confirmation():
                args.profile_file = None
                args.player_name = None
                args.budget = None
        elif choice == "8":
            print(_profile_menu_note(args, state, profile, config))
            _pause()
        else:
            print("Unknown field.")


def _ensure_required(args: argparse.Namespace) -> bool:
    _apply_detected_defaults(args)
    if not args.profile_file:
        try:
            ensure_profile_configuration(_profile_http(args), force_setup=bool(getattr(args, "setup", False)))
        except Exception as exc:  # noqa: BLE001 - keep interactive flow usable
            print(f"Profile setup failed: {exc}")
            return False
    return True


def _refresh_results(args: argparse.Namespace, state: _MenuState, *, resolve_uuid: Callable | None, announce: bool) -> bool:
    if resolve_uuid is None:
        resolve_uuid = getattr(state, "resolve_uuid", None)
    if resolve_uuid is None:
        print("Refresh callback is unavailable.")
        return False
    setattr(state, "resolve_uuid", resolve_uuid)
    if announce:
        print("Refreshing results...")
    try:
        data = collect_dashboard_data(args, resolve_uuid=resolve_uuid)
    except Exception as exc:  # noqa: BLE001 - interactive menu should survive failed refreshes
        print(f"Refresh failed: {exc}")
        state.status_message = f"Refresh failed: {exc}"
        return False
    with state.lock:
        state.latest = data
        state.last_refresh = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        state.status_message = "Results refreshed."
    if announce:
        _clear_screen()
        print_dashboard_status(data, last_refresh=state.last_refresh, auto_refresh=state.auto_refresh)
        _pause("Press Enter to return to the menu...")
    return True


def _refresh_module_results(
    args: argparse.Namespace,
    state: _MenuState,
    module: DashboardModule,
    *,
    resolve_uuid: Callable | None,
    announce: bool,
) -> bool:
    if resolve_uuid is None:
        resolve_uuid = getattr(state, "resolve_uuid", None)
    if resolve_uuid is None:
        print("Refresh callback is unavailable.")
        return False
    setattr(state, "resolve_uuid", resolve_uuid)
    if announce:
        print(f"Refreshing {module.title}...")
    original_sections = args.sections
    args.sections = ",".join(module.sections)
    try:
        data = collect_dashboard_data(args, resolve_uuid=resolve_uuid)
    except Exception as exc:  # noqa: BLE001 - interactive menu should survive failed refreshes
        print(f"Refresh failed: {exc}")
        state.status_message = f"Refresh failed: {exc}"
        return False
    finally:
        args.sections = original_sections
    with state.lock:
        state.latest = merge_module_data(state.latest, data, module)
        state.last_refresh = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        state.status_message = f"{module.title} refreshed."
    if announce:
        _clear_screen()
        _show_module_summary(args, state, module)
    return True


def _show_result_section(args: argparse.Namespace, state: _MenuState, key: str, module: DashboardModule | None = None) -> None:
    while True:
        data = state.latest
        if data is None:
            print("No results loaded. Refresh results first.")
            return
        sort_key = _current_talisman_sort(state) if key == "talisman" else _section_sort_key(state, key)
        display_data = _module_scoped_data(data, module, key) if module is not None else data
        sorted_data = _sorted_talisman_data(display_data, sort_key) if key == "talisman" else _sorted_section_data(display_data, key, sort_key)
        def draw_screen() -> None:
            _clear_screen()
            _draw_header(SECTION_LABELS.get(key, _section_name(key)), args, state)
            if key == "talisman":
                _draw_talisman_sort_hint(sort_key)
            else:
                _draw_sort_hint(key, sort_key)
            if module is not None:
                print(f"Filters: {_module_filter_summary(args, module)}")
            print()
            print_dashboard_section(sorted_data, key, show_rejected=args.show_rejected)
            if module is not None and _module_section_count(display_data, module, key) == 0 and key not in {"warnings", "rejected"}:
                print(empty_state_hint(module.key, key))

        if not _interactive_menu_enabled():
            draw_screen()
            choice = input("Press R to refresh, D for details, left/right to change sort, or Enter to go back: ").strip().lower()
            if choice == "left":
                _cycle_section_sort(state, key, -1)
                continue
            if choice == "right":
                _cycle_section_sort(state, key, 1)
                continue
            if choice in {"d", "details"} and module is not None:
                _module_detail_menu(args, state, module)
                continue
            if choice in {"r", "refresh"}:
                if module is not None:
                    _refresh_module_results(args, state, module, resolve_uuid=getattr(state, "resolve_uuid", None), announce=False)
                else:
                    _handle_global_refresh(choice, args, state, getattr(state, "resolve_uuid", None))
                continue
            return

        def draw_interactive_screen() -> None:
            draw_screen()
            if key == "talisman":
                print(_muted("Up/Down scroll   Left/Right change sort   D details   R refresh   Enter/Esc back"))
            else:
                print(_muted("Left/Right change sort   D details   R refresh   Enter/Esc back"))

        choice = _read_result_section_key(
            draw_screen,
            redraw_screen=draw_interactive_screen,
            static_render=key == "talisman",
            footer="Up/Down scroll   Left/Right change sort   D details   R refresh   Enter/Esc back",
        )
        if choice == "left":
            if key == "talisman":
                _cycle_talisman_sort(state, -1)
            else:
                _cycle_section_sort(state, key, -1)
            continue
        if choice == "right":
            if key == "talisman":
                _cycle_talisman_sort(state, 1)
            else:
                _cycle_section_sort(state, key, 1)
            continue
        if choice == "d" and module is not None:
            _module_detail_menu(args, state, module)
            continue
        if choice == "r":
            if module is not None:
                _refresh_module_results(args, state, module, resolve_uuid=getattr(state, "resolve_uuid", None), announce=False)
            else:
                _handle_global_refresh(choice, args, state, getattr(state, "resolve_uuid", None))
            continue
        if choice in {"enter", "escape", "q", "b"}:
            return


def _read_result_section_key(
    draw_screen: Callable[[], None],
    *,
    static_render: bool = False,
    footer: str = "",
    redraw_screen: Callable[[], None] | None = None,
) -> str:
    lines = _capture_redraw_frame(draw_screen).splitlines()
    if not static_render and not _frame_needs_scroll(lines, footer):
        return _read_key_with_redraw(redraw_screen or draw_screen)
    offset = 0
    while True:
        offset = _draw_static_scroll_frame(lines, offset, footer=footer)
        key = _read_key(timeout=None)
        if key == "up":
            offset = max(0, offset - 1)
            continue
        if key == "down":
            offset = min(_max_scroll_offset(lines, footer), offset + 1)
            continue
        if key:
            return key


def _frame_needs_scroll(lines: list[str], footer: str = "") -> bool:
    footer_lines = 1 if footer else 0
    return len(lines) + footer_lines > max(1, get_terminal_size().height)


def _draw_static_scroll_frame(lines: list[str], offset: int, *, footer: str = "") -> int:
    height = max(1, get_terminal_size().height)
    footer_lines = 1 if footer else 0
    content_height = max(1, height - footer_lines)
    max_offset = max(0, len(lines) - content_height)
    offset = max(0, min(offset, max_offset))
    visible = lines[offset: offset + content_height]
    frame = "\n".join(visible)
    if footer:
        position = f"{offset + 1}-{min(len(lines), offset + content_height)}/{len(lines)}" if lines else "0/0"
        frame = f"{frame}\n{_muted(f'{footer}   [{position}]')}" if frame else _muted(f"{footer}   [{position}]")
    _write_redraw_frame(frame)
    return offset


def _max_scroll_offset(lines: list[str], footer: str = "") -> int:
    footer_lines = 1 if footer else 0
    content_height = max(1, max(1, get_terminal_size().height) - footer_lines)
    return max(0, len(lines) - content_height)


def _current_talisman_sort(state: _MenuState) -> str:
    value = state.section_sorts.get("talisman", TALISMAN_SORTS[0])
    return value if value in TALISMAN_SORTS else TALISMAN_SORTS[0]


def _cycle_talisman_sort(state: _MenuState, direction: int) -> None:
    current = _current_talisman_sort(state)
    index = TALISMAN_SORTS.index(current)
    state.section_sorts["talisman"] = TALISMAN_SORTS[(index + direction) % len(TALISMAN_SORTS)]
    if state.persist_sort_preferences:
        save_sort_preferences(state.section_sorts)


def _draw_talisman_sort_hint(sort_key: str) -> None:
    options = " / ".join(
        f"[{TALISMAN_SORT_LABELS[value]}]" if value == sort_key else TALISMAN_SORT_LABELS[value]
        for value in TALISMAN_SORTS
    )
    print(_muted(f"Sort: {options}"))


def _sorted_talisman_data(data, sort_key: str):
    analysis = getattr(data, "talisman_helper", None)
    if analysis is None:
        return data
    sorted_analysis = replace(
        analysis,
        recommendations=_sort_talisman_rows(analysis.recommendations, sort_key),
        craftable=_sort_talisman_rows(analysis.craftable, sort_key),
        ah_available=_sort_talisman_rows(analysis.ah_available, sort_key),
        upgrades=_sort_talisman_rows(analysis.upgrades, sort_key),
        locked=_sort_talisman_rows(analysis.locked, sort_key),
        all_missing=_sort_talisman_rows(analysis.all_missing, sort_key),
        owned=_sort_talisman_rows(analysis.owned, sort_key),
        rows=_sort_talisman_rows(analysis.rows, sort_key),
    )
    values = dict(vars(data)) if hasattr(data, "__dict__") else {}
    values["talisman_helper"] = sorted_analysis
    return SimpleNamespace(**values)


def _sort_talisman_rows(rows: list[object], sort_key: str) -> list[object]:
    reverse = sort_key in {"score", "rarity"}
    return sorted(rows, key=lambda row: _talisman_sort_value(row, sort_key), reverse=reverse)


def _talisman_sort_value(row: object, sort_key: str):
    entry = getattr(row, "entry", None)
    if sort_key == "cost":
        value = getattr(row, "estimated_cost", None)
        return (value is None, value or 0, str(getattr(entry, "display_name", "")))
    if sort_key == "coin-per-mp":
        value = getattr(row, "coin_per_mp", None)
        return (value is None, value or 0, str(getattr(entry, "display_name", "")))
    if sort_key == "rarity":
        return (RARITY_SORT.get(str(getattr(entry, "rarity", "")).lower(), 0), float(getattr(row, "score", 0) or 0))
    if sort_key == "name":
        return str(getattr(entry, "display_name", "")).lower()
    if sort_key == "status":
        return (str(getattr(row, "status", "")).lower(), str(getattr(entry, "display_name", "")).lower())
    return (float(getattr(row, "score", 0) or 0), str(getattr(entry, "display_name", "")).lower())


def _module_scoped_data(data, module: DashboardModule | None, key: str):
    if module is None:
        return data
    values = dict(vars(data)) if hasattr(data, "__dict__") else {}
    if key == "warnings":
        values["warnings"] = module_warnings(data, module)
    elif key == "rejected":
        values["rejected"] = module_rejections(data, module)
    return SimpleNamespace(**values)




def _handle_global_refresh(choice: str, args: argparse.Namespace, state: _MenuState, resolve_uuid: Callable | None) -> bool:
    if choice not in {"r", "refresh"}:
        return False
    if not _ensure_required(args):
        state.status_message = "Refresh skipped; profile setup is incomplete."
        return True
    ok = _refresh_results(args, state, resolve_uuid=resolve_uuid, announce=False)
    if not ok and not state.status_message:
        state.status_message = "Refresh failed."
    return True


def _refreshable_entries(entries: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    if any(key.lower() == "r" for key, _, _ in entries):
        return entries
    return [("r", "Refresh results", "refresh and stay on this menu"), *entries]


def _toggle_auto_refresh(args: argparse.Namespace, state: _MenuState, *, resolve_uuid: Callable) -> None:
    if state.auto_refresh:
        _stop_auto_refresh(state)
        print("Automatic refresh is now OFF.")
        return
    interval = max(60, int(args.refresh_interval or 300))
    state.stop_event = threading.Event()
    state.auto_refresh = True
    setattr(state, "resolve_uuid", resolve_uuid)
    state.thread = threading.Thread(
        target=_auto_refresh_loop,
        args=(args, state, resolve_uuid, interval),
        daemon=True,
        name="skyflip-auto-refresh",
    )
    state.thread.start()
    state.status_message = f"Automatic refresh ON. Updating every {interval // 60:g} min."


def _auto_refresh_loop(args: argparse.Namespace, state: _MenuState, resolve_uuid: Callable, interval: int) -> None:
    while state.stop_event is not None and not state.stop_event.is_set():
        ok = _refresh_results(args, state, resolve_uuid=resolve_uuid, announce=False)
        if ok:
            state.status_message = f"Auto-refreshed at {state.last_refresh}."
        if state.stop_event.wait(interval):
            break


def _stop_auto_refresh(state: _MenuState) -> None:
    if state.stop_event is not None:
        state.stop_event.set()
    state.auto_refresh = False
    state.thread = None
    state.stop_event = None
    state.status_message = "Automatic refresh OFF."


def _detect_profile_file() -> Path | None:
    candidates = sorted(
        [*Path.cwd().glob("*_selected_profile.json"), *Path.cwd().glob("*selected*profile*.json")],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _profile_http(args: argparse.Namespace) -> HttpClient:
    ttl = int(getattr(args, "profile_cache_ttl", 600) or 600)
    return HttpClient(FileCache(ttl_seconds=ttl))


def _profile_source_label(args: argparse.Namespace) -> str:
    if args.profile_file:
        return f"local file ({_short_path(args.profile_file)})"
    return "Hypixel API"


def _profile_budget_label(args: argparse.Namespace, config: HypixelUserConfig | None, profile: object | None) -> str:
    if args.budget is not None:
        return f"{_coins(args.budget)} active"
    if args.profile_file:
        return budget_source_label(None, profile)
    if config is None:
        return "not configured"
    return budget_source_label(config, profile)


def _budget_source_menu(args: argparse.Namespace, state: _MenuState) -> None:
    profile = _profile_from_state(state) or _load_local_profile_for_menu(args)
    config = load_user_config()
    while True:
        choice = _select_menu(
            "Budget source",
            [
                ("1", "Purse only", _profile_budget_preview(profile, BUDGET_SOURCE_PURSE, config)),
                ("2", "Purse + bank", _profile_budget_preview(profile, BUDGET_SOURCE_PURSE_BANK, config)),
                ("3", "Custom amount", _profile_budget_preview(profile, BUDGET_SOURCE_CUSTOM, config)),
                ("b", "Back", "return to profile"),
            ],
            args=args,
            state=state,
            prompt="Budget source",
            note="The selected source is used when no explicit --budget value is provided.",
        )
        if choice in {"b", "back", ""}:
            return
        if choice == "1":
            _save_budget_source(args, profile, BUDGET_SOURCE_PURSE, None)
            state.status_message = "Budget source set to purse only."
            return
        if choice == "2":
            _save_budget_source(args, profile, BUDGET_SOURCE_PURSE_BANK, None)
            state.status_message = "Budget source set to purse + bank."
            return
        if choice == "3":
            default = args.budget or (config.custom_budget if config else None) or getattr(profile, "available_coins", 0.0)
            amount = _ask_float("Custom budget", default)
            _save_budget_source(args, profile, BUDGET_SOURCE_CUSTOM, amount)
            state.status_message = "Budget source set to custom amount."
            return
        print("Unknown budget source.")


def _save_budget_source(args: argparse.Namespace, profile: object | None, source: str, custom_budget: float | None) -> None:
    config = load_user_config()
    if config is not None and not args.profile_file:
        save_user_config(
            HypixelUserConfig(
                config.minecraft_username,
                config.uuid,
                config.selected_profile_name,
                config.last_profile_id,
                source,
                custom_budget,
            )
        )
        args.budget = None
        return
    preview_config = HypixelUserConfig("", "", "", None, source, custom_budget)
    if profile is not None:
        args.budget = budget_from_profile(profile, preview_config)
    elif source == BUDGET_SOURCE_CUSTOM:
        args.budget = max(0.0, float(custom_budget or 0.0))
    else:
        args.budget = None


def _profile_budget_preview(profile: object | None, source: str, config: HypixelUserConfig | None) -> str:
    preview = HypixelUserConfig("", "", "", None, source, config.custom_budget if config else None)
    if source == BUDGET_SOURCE_CUSTOM and config and config.custom_budget is not None:
        return _coins(config.custom_budget)
    if source == BUDGET_SOURCE_CUSTOM:
        return "choose manually"
    if profile is None:
        return "applies on next profile load"
    return _coins(budget_from_profile(profile, preview))


def _profile_freshness_label(args: argparse.Namespace, state: _MenuState) -> str:
    if args.profile_file:
        return "local fallback"
    profile = _profile_from_state(state)
    ttl = int(getattr(args, "profile_cache_ttl", 600) or 600)
    age = _profile_cache_age(profile)
    if age is None:
        age = cache_age_seconds()
    if profile is not None:
        source = str(getattr(profile, "profile_source", "") or "").lower()
        warnings = "\n".join(getattr(profile, "warnings", []) or []).lower()
        if "stale" in source or "stale cached profile" in warnings or (age is not None and age > ttl):
            return "stale"
        if "cache" in source:
            return "cached"
        return "fresh"
    if age is None:
        return "unavailable"
    return "cached" if age <= ttl else "stale"


def _profile_menu_note(
    args: argparse.Namespace,
    state: _MenuState,
    profile: object | None,
    config: HypixelUserConfig | None,
) -> str:
    lines = [
        f"Data freshness: {_profile_freshness_label(args, state)}",
        f"Budget source: {_profile_budget_label(args, config, profile)}",
    ]
    restricted = _restricted_profile_note(profile, None)
    if restricted:
        lines.extend(["", restricted])
    if args.profile_file:
        lines.append("")
        lines.append("Local profile JSON is an advanced/developer fallback; Hypixel API remains the default profile source.")
    return "\n".join(lines)


def _restricted_profile_note(profile: object | None, module: DashboardModule | None) -> str | None:
    if profile is None or not getattr(profile, "is_restricted_mode", False):
        return None
    allowed = "Accessories Helper remains available."
    unavailable = "Bazaar Flip, AH Craft Flips, Bazaar Compression, and AH BIN modules are unavailable unless restricted profiles are explicitly allowed."
    if module is not None:
        if module.key == "accessories":
            return f"Restricted profile mode {getattr(profile, 'profile_mode', None)!r}. {allowed}"
        return f"Restricted profile mode {getattr(profile, 'profile_mode', None)!r}. This market module is unavailable unless restricted profiles are explicitly allowed. {allowed}"
    return f"Restricted profile mode {getattr(profile, 'profile_mode', None)!r}. {unavailable} {allowed}"


def _profile_from_state(state: _MenuState) -> object | None:
    return getattr(getattr(state, "latest", None), "profile", None)


def _load_local_profile_for_menu(args: argparse.Namespace) -> object | None:
    if not args.profile_file:
        return None
    try:
        return load_profile(args.profile_file, player_name=args.player_name)
    except Exception:
        return None


def _profile_cache_age(profile: object | None) -> float | None:
    fetched_at = getattr(profile, "profile_fetched_at", None) if profile is not None else None
    try:
        return max(0.0, time.time() - float(fetched_at)) if fetched_at else None
    except (TypeError, ValueError):
        return None


def _infer_player_name(path: Path) -> str | None:
    name = path.name
    if "_" in name:
        first = name.split("_", 1)[0].strip()
        if first:
            return first
    return None


