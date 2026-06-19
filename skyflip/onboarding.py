from __future__ import annotations

import getpass

from .http import HttpClient
from .profile_fetcher import (
    InvalidApiKeyError,
    ProfileFetchError,
    ProfileNotFoundError,
    fetch_selected_profile_payload,
    load_api_profile,
    resolve_minecraft_uuid,
)
from .user_config import HypixelUserConfig, delete_api_key, get_api_key, load_user_config, reset_user_config, save_api_key, save_user_config


def run_onboarding(http: HttpClient, *, existing: HypixelUserConfig | None = None) -> HypixelUserConfig:
    print("SkyFlip Hypixel profile setup")
    username = (existing.minecraft_username if existing else "") or _ask_nonempty("Minecraft username")
    while True:
        try:
            uuid = existing.uuid if existing and existing.minecraft_username.lower() == username.lower() else resolve_minecraft_uuid(http, username)
            break
        except ProfileFetchError as exc:
            print(exc)
            username = _ask_nonempty("Minecraft username")

    profile_name = (existing.selected_profile_name if existing else "") or _ask_nonempty("SkyBlock profile name")
    while True:
        api_key = get_api_key() or _ask_api_key()
        try:
            config, _ = fetch_selected_profile_payload(
                http,
                username=username,
                profile_name=profile_name,
                api_key=api_key,
                uuid=uuid,
            )
        except InvalidApiKeyError as exc:
            print(f"{exc}. Please enter the key again.")
            delete_api_key()
            profile_name = profile_name or _ask_nonempty("SkyBlock profile name")
            continue
        except ProfileNotFoundError as exc:
            print(exc)
            profile_name = _ask_nonempty("SkyBlock profile name")
            continue
        _save_api_key_with_warning(api_key)
        print(f"Saved Hypixel profile configuration for {config.minecraft_username} / {config.selected_profile_name}.")
        return config


def ensure_profile_configuration(http: HttpClient, *, force_setup: bool = False) -> HypixelUserConfig:
    existing = load_user_config()
    if force_setup or existing is None:
        return run_onboarding(http, existing=existing)
    if not get_api_key():
        print("Saved profile found, but the Hypixel API key is missing.")
        while True:
            api_key = _ask_api_key()
            try:
                fetch_selected_profile_payload(
                    http,
                    username=existing.minecraft_username,
                    profile_name=existing.selected_profile_name,
                    api_key=api_key,
                    uuid=existing.uuid,
                )
            except InvalidApiKeyError as exc:
                print(f"{exc}. Please enter the key again.")
                delete_api_key()
                continue
            _save_api_key_with_warning(api_key)
            break
    return load_user_config() or existing


def reset_profile_configuration_with_confirmation() -> bool:
    answer = input(
        "This will remove saved Minecraft username, profile name, UUID, cached profile data, "
        "and saved Hypixel API key from this app. Continue? [y/N] "
    ).strip().lower()
    if answer not in {"y", "yes"}:
        print("Cancelled. Saved Hypixel profile configuration was not changed.")
        return False
    reset_user_config()
    print("Saved Hypixel profile configuration was reset. Onboarding will run on the next dashboard load.")
    return True


def refresh_profile_now(http: HttpClient) -> None:
    ensure_profile_configuration(http)
    load_api_profile(http, force_refresh=True)
    print("Profile data refreshed.")


def change_profile(http: HttpClient) -> HypixelUserConfig:
    current = load_user_config()
    username = _ask_nonempty("Minecraft username", default=current.minecraft_username if current else None)
    uuid = resolve_minecraft_uuid(http, username)
    profile_name = _ask_nonempty("SkyBlock profile name", default=current.selected_profile_name if current else None)
    api_key = get_api_key() or _ask_api_key()
    config, _ = fetch_selected_profile_payload(http, username=username, profile_name=profile_name, api_key=api_key, uuid=uuid)
    save_user_config(config)
    _save_api_key_with_warning(api_key)
    print(f"Changed Hypixel profile to {config.minecraft_username} / {config.selected_profile_name}.")
    return config


def _ask_nonempty(label: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{label}{suffix}: ").strip()
        if value:
            return value
        if default:
            return default
        print(f"{label} is required.")


def _ask_api_key() -> str:
    while True:
        value = getpass.getpass("Hypixel API key: ").strip()
        if value:
            return value
        print("Hypixel API key is required.")


def _save_api_key_with_warning(api_key: str) -> None:
    if not save_api_key(api_key):
        print("Warning: secure key storage is unavailable; the Hypixel API key was saved in the local user config file.")
