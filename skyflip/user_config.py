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


@dataclass(frozen=True)
class HypixelUserConfig:
    minecraft_username: str
    uuid: str
    selected_profile_name: str
    last_profile_id: str | None = None


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
    return HypixelUserConfig(username, uuid, profile_name, str(profile_id) if profile_id else None)


def save_user_config(config: HypixelUserConfig) -> None:
    raw = _read_config_raw()
    raw.update(
        {
            "minecraft_username": config.minecraft_username,
            "uuid": config.uuid,
            "selected_profile_name": config.selected_profile_name,
            "last_profile_id": config.last_profile_id,
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
