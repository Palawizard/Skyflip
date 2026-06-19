from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


APP_NAME = "SkyFlip"
KEYRING_SERVICE = "skyflip"
KEYRING_USERNAME = "hypixel_api_key"
PROFILE_CACHE_TTL_SECONDS = 600
BUDGET_SOURCE_PURSE = "purse"
BUDGET_SOURCE_PURSE_BANK = "purse_bank"
BUDGET_SOURCE_CUSTOM = "custom"
BUDGET_SOURCES = {BUDGET_SOURCE_PURSE, BUDGET_SOURCE_PURSE_BANK, BUDGET_SOURCE_CUSTOM}


@dataclass(frozen=True)
class HypixelUserConfig:
    minecraft_username: str
    uuid: str
    selected_profile_name: str
    last_profile_id: str | None = None
    budget_source: str = BUDGET_SOURCE_PURSE_BANK
    custom_budget: float | None = None


def normalize_budget_source(value: str | None) -> str:
    source = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "bank": BUDGET_SOURCE_PURSE_BANK,
        "total": BUDGET_SOURCE_PURSE_BANK,
        "available": BUDGET_SOURCE_PURSE_BANK,
        "purse+bank": BUDGET_SOURCE_PURSE_BANK,
        "purse_only": BUDGET_SOURCE_PURSE,
    }
    source = aliases.get(source, source)
    return source if source in BUDGET_SOURCES else BUDGET_SOURCE_PURSE_BANK


def budget_from_profile(profile: object, config: HypixelUserConfig | None = None) -> float:
    source = normalize_budget_source(config.budget_source if config else None)
    if source == BUDGET_SOURCE_CUSTOM:
        return max(0.0, float(config.custom_budget or 0.0)) if config else 0.0
    purse = float(getattr(profile, "purse", 0.0) or 0.0)
    if source == BUDGET_SOURCE_PURSE:
        return max(0.0, purse)
    bank = float(getattr(profile, "bank", 0.0) or 0.0)
    return max(0.0, purse + bank)


def budget_source_label(config: HypixelUserConfig | None, profile: object | None = None) -> str:
    source = normalize_budget_source(config.budget_source if config else None)
    if source == BUDGET_SOURCE_PURSE:
        return "purse only"
    if source == BUDGET_SOURCE_CUSTOM:
        amount = config.custom_budget if config else None
        return f"custom ({amount:,.0f} coins)" if amount is not None else "custom"
    if profile is not None:
        purse = float(getattr(profile, "purse", 0.0) or 0.0)
        bank = float(getattr(profile, "bank", 0.0) or 0.0)
        return f"purse + bank ({purse + bank:,.0f} coins)"
    return "purse + bank"


def user_config_dir() -> Path:
    override = os.environ.get("SKYFLIP_CONFIG_DIR")
    if override:
        return Path(override)
    try:
        from platformdirs import user_config_dir as platform_user_config_dir

        return Path(platform_user_config_dir(APP_NAME, appauthor=False))
    except Exception:
        if os.name == "nt" and os.environ.get("APPDATA"):
            return Path(os.environ["APPDATA"]) / APP_NAME
        if sys_platform() == "darwin":
            return Path.home() / "Library" / "Application Support" / APP_NAME
        return Path.home() / ".config" / "skyflip"


def config_path() -> Path:
    override = os.environ.get("SKYFLIP_CONFIG_FILE")
    return Path(override) if override else user_config_dir() / "config.json"


def profile_cache_path() -> Path:
    override = os.environ.get("SKYFLIP_PROFILE_CACHE_FILE")
    return Path(override) if override else user_config_dir() / "profile_cache.json"


def load_user_config() -> HypixelUserConfig | None:
    path = config_path()
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    username = str(raw.get("minecraft_username") or "").strip()
    uuid = str(raw.get("uuid") or "").strip()
    profile_name = str(raw.get("selected_profile_name") or "").strip()
    if not username or not uuid or not profile_name:
        return None
    profile_id = raw.get("last_profile_id")
    budget_source = normalize_budget_source(raw.get("budget_source"))
    custom_budget = _float_or_none(raw.get("custom_budget"))
    return HypixelUserConfig(username, uuid, profile_name, str(profile_id) if profile_id else None, budget_source, custom_budget)


def save_user_config(config: HypixelUserConfig) -> None:
    raw = _read_config_raw()
    raw.update(
        {
            "minecraft_username": config.minecraft_username,
            "uuid": config.uuid,
            "selected_profile_name": config.selected_profile_name,
            "last_profile_id": config.last_profile_id,
            "budget_source": normalize_budget_source(config.budget_source),
            "custom_budget": config.custom_budget,
        }
    )
    _write_config_raw(raw)


def get_api_key() -> str | None:
    if value := os.environ.get("HYPIXEL_API_KEY"):
        return value
    if value := _keyring_get():
        return value
    raw = _read_config_raw()
    value = raw.get("hypixel_api_key") if isinstance(raw, dict) else None
    return str(value) if value else None


def save_api_key(api_key: str) -> bool:
    if _keyring_set(api_key):
        raw = _read_config_raw()
        if "hypixel_api_key" in raw:
            raw.pop("hypixel_api_key", None)
            _write_config_raw(raw)
        return True
    raw = _read_config_raw()
    raw["hypixel_api_key"] = api_key
    _write_config_raw(raw)
    return False


def delete_api_key() -> None:
    _keyring_delete()
    raw = _read_config_raw()
    if "hypixel_api_key" in raw:
        raw.pop("hypixel_api_key", None)
        _write_config_raw(raw)


def reset_user_config() -> None:
    delete_api_key()
    for path in (config_path(), profile_cache_path()):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def cache_profile_payload(payload: dict[str, Any], *, source: str, profile_name: str, uuid: str) -> None:
    path = profile_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "created_at": time.time(),
                "source": source,
                "profile_name": profile_name,
                "uuid": uuid,
                "payload": payload,
            },
            separators=(",", ":"),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def load_cached_profile_payload(*, allow_stale: bool = False, ttl_seconds: int = PROFILE_CACHE_TTL_SECONDS) -> dict[str, Any] | None:
    path = profile_cache_path()
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    created_at = float(raw.get("created_at") or 0)
    if not allow_stale and ttl_seconds > 0 and time.time() - created_at > ttl_seconds:
        return None
    payload = raw.get("payload")
    if not isinstance(payload, dict):
        return None
    result = dict(payload)
    result.setdefault("_skyflip_profile_source", raw.get("source", "api-cache"))
    result.setdefault("_skyflip_profile_fetched_at", created_at)
    return result


def cache_age_seconds() -> float | None:
    path = profile_cache_path()
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    created_at = float(raw.get("created_at") or 0)
    return max(0.0, time.time() - created_at) if created_at else None


def _read_config_raw() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_config_raw(raw: dict[str, Any]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw, indent=2), encoding="utf-8")


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _keyring_get() -> str | None:
    try:
        import keyring

        value = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
        return str(value) if value else None
    except Exception:
        return None


def _keyring_set(api_key: str) -> bool:
    try:
        import keyring

        keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, api_key)
        return True
    except Exception:
        return False


def _keyring_delete() -> None:
    try:
        import keyring

        keyring.delete_password(KEYRING_SERVICE, KEYRING_USERNAME)
    except Exception:
        pass


def sys_platform() -> str:
    import sys

    return sys.platform
