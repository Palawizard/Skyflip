from __future__ import annotations

import json
import os
from argparse import Namespace
from pathlib import Path
from typing import Any


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


def settings_profiles_path() -> Path:
    override = os.environ.get("SKYFLIP_SETTINGS_PROFILES_FILE")
    if override:
        return Path(override)
    return Path.cwd() / ".skyflip" / "settings_profiles.json"


def list_settings_profiles() -> dict[str, dict[str, Any]]:
    return _read_store().get("profiles", {})


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
        return {"profiles": {}, "active_profile": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"profiles": {}, "active_profile": None}
    if not isinstance(data, dict):
        return {"profiles": {}, "active_profile": None}
    profiles = data.get("profiles") if isinstance(data, dict) else None
    active = data.get("active_profile")
    return {
        "profiles": profiles if isinstance(profiles, dict) else {},
        "active_profile": str(active) if active else None,
    }


def save_settings_profile(args: Namespace, name: str) -> None:
    clean_name = _clean_name(name)
    if not clean_name:
        raise ValueError("settings profile name cannot be empty")
    profiles = list_settings_profiles()
    profiles[clean_name] = capture_settings(args)
    _write_profiles(profiles, active_profile=clean_name)


def load_settings_profile(args: Namespace, name: str) -> bool:
    profile = list_settings_profiles().get(name)
    if not isinstance(profile, dict):
        return False
    apply_settings(args, profile)
    set_active_settings_profile(name)
    return True


def delete_settings_profile(name: str) -> bool:
    profiles = list_settings_profiles()
    if name not in profiles:
        return False
    del profiles[name]
    active = get_active_settings_profile()
    _write_profiles(profiles, active_profile=None if active == name else active)
    return True


def capture_settings(args: Namespace) -> dict[str, Any]:
    return {
        field: getattr(args, field)
        for field in SETTINGS_PROFILE_FIELDS
        if hasattr(args, field)
    }


def apply_settings(args: Namespace, settings: dict[str, Any]) -> None:
    for field in SETTINGS_PROFILE_FIELDS:
        if field in settings:
            setattr(args, field, settings[field])


def _write_profiles(profiles: dict[str, dict[str, Any]], *, active_profile: str | None = None) -> None:
    _write_store({"profiles": profiles, "active_profile": active_profile})


def _write_store(store: dict[str, Any]) -> None:
    path = settings_profiles_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    profiles = store.get("profiles")
    active = store.get("active_profile")
    path.write_text(
        json.dumps(
            {
                "active_profile": active if active in (profiles or {}) else None,
                "profiles": profiles if isinstance(profiles, dict) else {},
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _clean_name(name: str) -> str:
    return " ".join(name.strip().split())
