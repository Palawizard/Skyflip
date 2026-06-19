from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from .http import ApiError, HttpClient
from .profile_parser import PlayerProfile, parse_profile
from .user_config import (
    BUDGET_SOURCE_PURSE_BANK,
    PROFILE_CACHE_TTL_SECONDS,
    HypixelUserConfig,
    cache_profile_payload,
    get_api_key,
    load_cached_profile_payload,
    load_user_config,
    save_user_config,
)


MOJANG_PROFILE_URL = "https://api.mojang.com/users/profiles/minecraft/{username}"
HYPIXEL_PROFILES_URL = "https://api.hypixel.net/v2/skyblock/profiles?uuid={uuid}"


class ProfileFetchError(RuntimeError):
    pass


class InvalidApiKeyError(ProfileFetchError):
    pass


class ProfileNotFoundError(ProfileFetchError):
    def __init__(self, requested: str, available: list[str]) -> None:
        self.requested = requested
        self.available = available
        choices = ", ".join(available) if available else "none"
        super().__init__(f"SkyBlock profile {requested!r} was not found. Available profiles: {choices}")


class ApiUnavailableError(ProfileFetchError):
    pass


@dataclass(frozen=True)
class LoadedProfile:
    profile: PlayerProfile
    source: str
    fetched_at: float | None
    warnings: list[str]


def resolve_minecraft_uuid(http: HttpClient, username: str) -> str:
    try:
        result = http.get_json(MOJANG_PROFILE_URL.format(username=quote(username)))
    except ApiError as exc:
        raise ApiUnavailableError(f"Mojang username lookup failed for {username!r}: {exc}") from exc
    payload = result.payload if isinstance(result.payload, dict) else {}
    uuid = payload.get("id")
    if not uuid:
        raise ProfileFetchError(f"Minecraft username {username!r} was not found")
    return str(uuid).replace("-", "").lower()


def fetch_hypixel_profiles(http: HttpClient, uuid: str, api_key: str) -> dict[str, Any]:
    url = HYPIXEL_PROFILES_URL.format(uuid=quote(uuid))
    try:
        result = http.get_json(
            url,
            headers={"API-Key": api_key},
            cache_key=f"hypixel-profiles:{uuid}",
            use_cache=False,
        )
    except ApiError as exc:
        message = _scrub_key(str(exc), api_key)
        if "401" in message or "403" in message or "invalid" in message.lower():
            raise InvalidApiKeyError("Hypixel API key was rejected") from exc
        raise ApiUnavailableError(f"Hypixel profile API failed: {message}") from exc
    payload = result.payload if isinstance(result.payload, dict) else {}
    if payload.get("success") is False:
        cause = str(payload.get("cause") or "Hypixel API rejected the request")
        if "key" in cause.lower() or "invalid" in cause.lower():
            raise InvalidApiKeyError("Hypixel API key was rejected")
        raise ApiUnavailableError(_scrub_key(cause, api_key))
    return payload


def select_profile_payload(profiles_payload: dict[str, Any], profile_name: str, uuid: str) -> tuple[str, dict[str, Any]]:
    profiles = profiles_payload.get("profiles") or []
    if not isinstance(profiles, list):
        profiles = []
    wanted = profile_name.strip().lower()
    available: list[str] = []
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        cute_name = str(profile.get("cute_name") or "")
        if cute_name:
            available.append(cute_name)
        if cute_name.lower() == wanted:
            members = profile.get("members") if isinstance(profile.get("members"), dict) else {}
            compact_uuid = uuid.replace("-", "").lower()
            if compact_uuid not in {str(key).replace("-", "").lower() for key in members}:
                raise ProfileFetchError(f"Profile {cute_name!r} does not contain the selected Minecraft UUID")
            return str(profile.get("profile_id") or ""), {"profile": profile}
    raise ProfileNotFoundError(profile_name, available)


def fetch_selected_profile_payload(
    http: HttpClient,
    *,
    username: str,
    profile_name: str,
    api_key: str,
    uuid: str | None = None,
) -> tuple[HypixelUserConfig, dict[str, Any]]:
    resolved_uuid = uuid or resolve_minecraft_uuid(http, username)
    profiles_payload = fetch_hypixel_profiles(http, resolved_uuid, api_key)
    profile_id, selected_payload = select_profile_payload(profiles_payload, profile_name, resolved_uuid)
    selected_payload["_skyflip_profile_source"] = "api"
    selected_payload["_skyflip_profile_fetched_at"] = time.time()
    current = load_user_config()
    config = HypixelUserConfig(
        username,
        resolved_uuid,
        profile_name,
        profile_id or None,
        current.budget_source if current else BUDGET_SOURCE_PURSE_BANK,
        current.custom_budget if current else None,
    )
    save_user_config(config)
    cache_profile_payload(selected_payload, source="api", profile_name=profile_name, uuid=resolved_uuid)
    return config, selected_payload


def load_api_profile(
    http: HttpClient,
    *,
    force_refresh: bool = False,
    ttl_seconds: int = PROFILE_CACHE_TTL_SECONDS,
) -> LoadedProfile:
    config = load_user_config()
    if config is None:
        raise ProfileFetchError("Hypixel profile configuration is missing")
    api_key = get_api_key()
    if not api_key:
        raise InvalidApiKeyError("Hypixel API key is missing")

    cached = None if force_refresh else load_cached_profile_payload(ttl_seconds=ttl_seconds)
    if cached is not None:
        profile = parse_profile(cached, player_name=config.minecraft_username, player_uuid=config.uuid)
        return LoadedProfile(profile, "api-cache", cached.get("_skyflip_profile_fetched_at"), list(profile.warnings))

    try:
        updated_config, payload = fetch_selected_profile_payload(
            http,
            username=config.minecraft_username,
            profile_name=config.selected_profile_name,
            api_key=api_key,
            uuid=config.uuid,
        )
    except ApiUnavailableError as exc:
        stale = load_cached_profile_payload(allow_stale=True, ttl_seconds=ttl_seconds)
        if stale is None:
            raise
        profile = parse_profile(stale, player_name=config.minecraft_username, player_uuid=config.uuid)
        warnings = [*profile.warnings, f"Using stale cached profile data because the API is unavailable: {exc}"]
        return LoadedProfile(profile, "api-cache-stale", stale.get("_skyflip_profile_fetched_at"), warnings)

    profile = parse_profile(payload, player_name=updated_config.minecraft_username, player_uuid=updated_config.uuid)
    return LoadedProfile(profile, "api", payload.get("_skyflip_profile_fetched_at"), list(profile.warnings))


def _scrub_key(message: str, api_key: str) -> str:
    return message.replace(api_key, "[redacted]") if api_key else message
