from __future__ import annotations

import json
import os
from argparse import Namespace
from pathlib import Path
from typing import Any


STORE_SCHEMA_VERSION = 1
STORE_SOURCE = "skyflip"

SETTINGS_PROFILE_FIELDS = [
    "days",
    "min_profit",
    "min_profit_percent",
    "min_sales_per_day",
    "max_median_sell_time_hours",
    "cache_ttl",
    "sections",
    "limit_per_section",
    "spread_limit",
    "min_spread_profit_per_unit",
    "min_spread_volume_week",
    "max_spread_depth_ratio",
    "max_estimated_buy_minutes",
    "max_estimated_sell_minutes",
    "max_estimated_bottleneck_minutes",
    "min_speed_confidence",
    "conservative_speed",
    "max_craft_cost",
    "max_capital_percent_per_flip",
    "use_buy_order_cost",
    "recipes_file",
    "bazaar_conversions_file",
    "ah_watchlist_file",
    "conversion_mode",
    "show_rejected",
    "allow_restricted_profile",
    "refresh_interval",
    "accessories_file",
    "max_accessory_price",
    "max_accessory_recommendations",
    "max_accessory_ah_checks",
    "accessory_sort",
    "accessory_rarity",
    "accessory_view",
    "accessory_search",
    "accessory_ascending",
    "show_owned",
    "show_locked",
    "only_craftable",
    "only_ah",
    "include_locked_accessories",
    "include_uncertain_accessories",
    "include_manual_unlocks",
    "include_ah_accessories",
    "include_craftable_accessories",
]

MODULE_SETTINGS_FIELDS = {
    "bazaar": [
        "limit_per_section",
        "spread_limit",
        "min_spread_profit_per_unit",
        "min_spread_volume_week",
        "max_spread_depth_ratio",
        "max_estimated_buy_minutes",
        "max_estimated_sell_minutes",
        "max_estimated_bottleneck_minutes",
        "min_speed_confidence",
        "conservative_speed",
        "max_capital_percent_per_flip",
    ],
    "craft": [
        "min_profit",
        "min_profit_percent",
        "min_sales_per_day",
        "max_median_sell_time_hours",
        "max_craft_cost",
        "max_capital_percent_per_flip",
        "use_buy_order_cost",
        "recipes_file",
    ],
    "accessories": [
        "accessories_file",
        "max_accessory_price",
        "max_accessory_recommendations",
        "max_accessory_ah_checks",
        "accessory_sort",
        "accessory_rarity",
        "accessory_view",
        "accessory_search",
        "accessory_ascending",
        "show_owned",
        "show_locked",
        "only_craftable",
        "only_ah",
        "include_locked_accessories",
        "include_uncertain_accessories",
        "include_manual_unlocks",
        "include_ah_accessories",
        "include_craftable_accessories",
    ],
    "compression": [
        "conversion_mode",
        "limit_per_section",
        "min_profit",
        "min_profit_percent",
        "max_capital_percent_per_flip",
        "bazaar_conversions_file",
    ],
    "ah-bin": [
        "limit_per_section",
        "min_profit",
        "min_profit_percent",
        "max_median_sell_time_hours",
        "ah_watchlist_file",
    ],
}


def settings_profiles_path() -> Path:
    override = os.environ.get("SKYFLIP_SETTINGS_PROFILES_FILE")
    if override:
        return Path(override)
    return Path.cwd() / ".skyflip" / "settings_profiles.json"


def list_settings_profiles() -> dict[str, dict[str, Any]]:
    return _read_store().get("profiles", {})


def list_module_settings_presets(module_key: str) -> dict[str, dict[str, Any]]:
    module_presets = _read_store().get("module_presets", {})
    presets = module_presets.get(module_key) if isinstance(module_presets, dict) else None
    return presets if isinstance(presets, dict) else {}


def get_active_settings_profile() -> str | None:
    active = _read_store().get("active_profile")
    return str(active) if active else None


def set_active_settings_profile(name: str | None) -> None:
    store = _read_store()
    store["active_profile"] = _clean_name(name or "") or None
    _write_store(store)


def load_active_settings_profile(args: Namespace) -> str | None:
    active = get_active_settings_profile()
    if not active:
        return None
    return active if load_settings_profile(args, active) else None


def _read_store() -> dict[str, Any]:
    path = settings_profiles_path()
    if not path.exists():
        return _empty_store()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_store()
    if not isinstance(data, dict):
        return _empty_store()
    profiles = data.get("profiles") if isinstance(data, dict) else None
    module_presets = data.get("module_presets") if isinstance(data, dict) else None
    active = data.get("active_profile")
    return {
        "version": int(data.get("version") or STORE_SCHEMA_VERSION),
        "source": str(data.get("source") or STORE_SOURCE),
        "profiles": profiles if isinstance(profiles, dict) else {},
        "module_presets": _clean_module_presets(module_presets),
        "active_profile": str(active) if active else None,
    }


def save_settings_profile(args: Namespace, name: str) -> None:
    clean_name = _clean_name(name)
    if not clean_name:
        raise ValueError("settings profile name cannot be empty")
    profiles = list_settings_profiles()
    profiles[clean_name] = capture_settings(args)
    _write_profiles(profiles, active_profile=clean_name)


def save_module_settings_preset(args: Namespace, module_key: str, name: str) -> None:
    clean_name = _clean_name(name)
    if not clean_name:
        raise ValueError("module preset name cannot be empty")
    if module_key not in MODULE_SETTINGS_FIELDS:
        raise ValueError(f"unknown module: {module_key}")
    store = _read_store()
    module_presets = store.get("module_presets")
    if not isinstance(module_presets, dict):
        module_presets = {}
    presets = module_presets.get(module_key)
    if not isinstance(presets, dict):
        presets = {}
    presets[clean_name] = capture_module_settings(args, module_key)
    module_presets[module_key] = presets
    store["module_presets"] = module_presets
    _write_store(store)


def load_settings_profile(args: Namespace, name: str) -> bool:
    profile = list_settings_profiles().get(name)
    if not isinstance(profile, dict):
        return False
    apply_settings(args, profile)
    set_active_settings_profile(name)
    return True


def load_module_settings_preset(args: Namespace, module_key: str, name: str) -> bool:
    preset = list_module_settings_presets(module_key).get(name)
    if not isinstance(preset, dict):
        return False
    apply_settings(args, preset, fields=MODULE_SETTINGS_FIELDS.get(module_key, []))
    return True


def delete_settings_profile(name: str) -> bool:
    profiles = list_settings_profiles()
    if name not in profiles:
        return False
    del profiles[name]
    active = get_active_settings_profile()
    _write_profiles(profiles, active_profile=None if active == name else active)
    return True


def delete_module_settings_preset(module_key: str, name: str) -> bool:
    store = _read_store()
    module_presets = store.get("module_presets")
    if not isinstance(module_presets, dict):
        return False
    presets = module_presets.get(module_key)
    if not isinstance(presets, dict) or name not in presets:
        return False
    del presets[name]
    if presets:
        module_presets[module_key] = presets
    else:
        module_presets.pop(module_key, None)
    store["module_presets"] = module_presets
    _write_store(store)
    return True


def capture_settings(args: Namespace) -> dict[str, Any]:
    return {
        field: getattr(args, field)
        for field in SETTINGS_PROFILE_FIELDS
        if hasattr(args, field)
    }


def capture_module_settings(args: Namespace, module_key: str) -> dict[str, Any]:
    return {
        field: getattr(args, field)
        for field in MODULE_SETTINGS_FIELDS.get(module_key, [])
        if hasattr(args, field)
    }


def apply_settings(args: Namespace, settings: dict[str, Any], *, fields: list[str] | None = None) -> None:
    for field in fields or SETTINGS_PROFILE_FIELDS:
        if field in settings:
            setattr(args, field, settings[field])


def _write_profiles(profiles: dict[str, dict[str, Any]], *, active_profile: str | None = None) -> None:
    store = _read_store()
    store["profiles"] = profiles
    store["active_profile"] = active_profile
    _write_store(store)


def _write_store(store: dict[str, Any]) -> None:
    path = settings_profiles_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    profiles = store.get("profiles")
    module_presets = store.get("module_presets")
    active = store.get("active_profile")
    path.write_text(
        json.dumps(
            {
                "version": STORE_SCHEMA_VERSION,
                "source": STORE_SOURCE,
                "active_profile": active if active in (profiles or {}) else None,
                "profiles": profiles if isinstance(profiles, dict) else {},
                "module_presets": _clean_module_presets(module_presets),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _clean_name(name: str) -> str:
    return " ".join(name.strip().split())


def _empty_store() -> dict[str, Any]:
    return {
        "version": STORE_SCHEMA_VERSION,
        "source": STORE_SOURCE,
        "profiles": {},
        "module_presets": {},
        "active_profile": None,
    }


def _clean_module_presets(value: object) -> dict[str, dict[str, dict[str, Any]]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for module_key, presets in value.items():
        if module_key not in MODULE_SETTINGS_FIELDS or not isinstance(presets, dict):
            continue
        cleaned_presets: dict[str, dict[str, Any]] = {}
        for name, settings in presets.items():
            clean_name = _clean_name(str(name))
            if clean_name and isinstance(settings, dict):
                cleaned_presets[clean_name] = {
                    field: settings[field]
                    for field in MODULE_SETTINGS_FIELDS[module_key]
                    if field in settings
                }
        if cleaned_presets:
            result[module_key] = cleaned_presets
    return result
