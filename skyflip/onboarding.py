from __future__ import annotations

import getpass

from .http import HttpClient
from .profile_fetcher import (
    ApiUnavailableError,
    InvalidApiKeyError,
    ProfileFetchError,
    ProfileNotFoundError,
    fetch_selected_profile_payload,
    load_api_profile,
    resolve_minecraft_uuid,
)
from .profile_parser import parse_profile
from .user_config import (
    BUDGET_SOURCE_CUSTOM,
    BUDGET_SOURCE_PURSE,
    BUDGET_SOURCE_PURSE_BANK,
    HypixelUserConfig,
    budget_source_label,
    delete_api_key,
    get_api_key,
    load_user_config,
    reset_user_config,
    save_api_key,
    save_user_config,
)


def run_onboarding(http: HttpClient, *, existing: HypixelUserConfig | None = None) -> HypixelUserConfig:
    print("SkyFlip Hypixel profile setup")
    _print_step(1, "Minecraft account")
    username = (existing.minecraft_username if existing else "") or _ask_nonempty("Minecraft username")
    while True:
        try:
            uuid = existing.uuid if existing and existing.minecraft_username.lower() == username.lower() else resolve_minecraft_uuid(http, username)
            break
        except ProfileFetchError as exc:
            print(_profile_error_message(exc, username=username))
            username = _ask_nonempty("Minecraft username")

    _print_step(2, "SkyBlock profile")
    profile_name = (existing.selected_profile_name if existing else "") or _ask_nonempty("SkyBlock profile name")
    _print_step(3, "Hypixel API key")
    while True:
        api_key = get_api_key() or _ask_api_key()
        try:
            config, payload = fetch_selected_profile_payload(
                http,
                username=username,
                profile_name=profile_name,
                api_key=api_key,
                uuid=uuid,
            )
        except InvalidApiKeyError as exc:
            print(f"{_profile_error_message(exc)} Please enter the key again.")
            delete_api_key()
            profile_name = profile_name or _ask_nonempty("SkyBlock profile name")
            continue
        except ProfileNotFoundError as exc:
            print(_profile_error_message(exc))
            profile_name = _ask_nonempty("SkyBlock profile name")
            continue
        except ApiUnavailableError as exc:
            print(_profile_error_message(exc))
            profile_name = profile_name or _ask_nonempty("SkyBlock profile name")
            continue
        _save_api_key_with_warning(api_key)
        profile = parse_profile(payload, player_name=config.minecraft_username, player_uuid=config.uuid)
        _print_step(4, "Budget source")
        config = _choose_budget_source(config, profile)
        save_user_config(config)
        _print_step(5, "Confirmation")
        _confirm_profile_configuration(config, profile)
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
                print(f"{_profile_error_message(exc)} Please enter the key again.")
                delete_api_key()
                continue
            except ProfileFetchError as exc:
                print(_profile_error_message(exc))
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
    _print_step(1, "Minecraft account")
    username = _ask_nonempty("Minecraft username", default=current.minecraft_username if current else None)
    uuid = resolve_minecraft_uuid(http, username)
    _print_step(2, "SkyBlock profile")
    profile_name = _ask_nonempty("SkyBlock profile name", default=current.selected_profile_name if current else None)
    _print_step(3, "Hypixel API key")
    api_key = get_api_key() or _ask_api_key()
    config, payload = fetch_selected_profile_payload(http, username=username, profile_name=profile_name, api_key=api_key, uuid=uuid)
    _print_step(4, "Budget source")
    config = _choose_budget_source(config, parse_profile(payload, player_name=config.minecraft_username, player_uuid=config.uuid))
    save_user_config(config)
    _save_api_key_with_warning(api_key)
    _print_step(5, "Confirmation")
    _confirm_profile_configuration(config, parse_profile(payload, player_name=config.minecraft_username, player_uuid=config.uuid), changed=True)
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


def _print_step(number: int, label: str) -> None:
    print(f"\nStep {number}/5 - {label}")


def _choose_budget_source(config: HypixelUserConfig, profile: object) -> HypixelUserConfig:
    default = config.budget_source
    print("Choose the coin source SkyFlip should use for recommendations.")
    print(f"1. Purse only ({getattr(profile, 'purse', 0.0):,.0f} coins)")
    print(f"2. Purse + bank ({getattr(profile, 'available_coins', 0.0):,.0f} coins)")
    print("3. Custom amount")
    while True:
        answer = input(f"Budget source [1/2/3, default { _budget_choice_for_source(default) }]: ").strip()
        choice = answer or _budget_choice_for_source(default)
        if choice == "1":
            return HypixelUserConfig(
                config.minecraft_username,
                config.uuid,
                config.selected_profile_name,
                config.last_profile_id,
                BUDGET_SOURCE_PURSE,
                None,
            )
        if choice == "2":
            return HypixelUserConfig(
                config.minecraft_username,
                config.uuid,
                config.selected_profile_name,
                config.last_profile_id,
                BUDGET_SOURCE_PURSE_BANK,
                None,
            )
        if choice == "3":
            amount = _ask_float("Custom budget", config.custom_budget or getattr(profile, "available_coins", 0.0))
            return HypixelUserConfig(
                config.minecraft_username,
                config.uuid,
                config.selected_profile_name,
                config.last_profile_id,
                BUDGET_SOURCE_CUSTOM,
                amount,
            )
        print("Choose 1, 2, or 3.")


def _budget_choice_for_source(source: str) -> str:
    if source == BUDGET_SOURCE_PURSE:
        return "1"
    if source == BUDGET_SOURCE_CUSTOM:
        return "3"
    return "2"


def _ask_float(label: str, default: float | None = None) -> float:
    suffix = f" [{default:,.0f}]" if default is not None else ""
    while True:
        value = input(f"{label}{suffix}: ").strip().replace(",", "")
        if not value and default is not None:
            return float(default)
        try:
            result = float(value)
        except ValueError:
            print(f"{label} must be a number.")
            continue
        if result >= 0:
            return result
        print(f"{label} cannot be negative.")


def _confirm_profile_configuration(config: HypixelUserConfig, profile: object, *, changed: bool = False) -> None:
    verb = "Changed" if changed else "Saved"
    print(
        f"{verb} Hypixel profile configuration for "
        f"{config.minecraft_username} / {config.selected_profile_name}."
    )
    print(f"Budget source: {budget_source_label(config, profile)}.")


def _profile_error_message(exc: ProfileFetchError, *, username: str | None = None) -> str:
    if isinstance(exc, InvalidApiKeyError):
        return "Hypixel API key was rejected or is missing."
    if isinstance(exc, ProfileNotFoundError):
        available = ", ".join(exc.available) if exc.available else "none returned by Hypixel"
        return f"SkyBlock profile {exc.requested!r} was not found. Available profiles: {available}."
    if isinstance(exc, ApiUnavailableError):
        target = f" for {username!r}" if username else ""
        return f"Profile lookup{target} is unavailable right now: {exc}"
    return str(exc)
