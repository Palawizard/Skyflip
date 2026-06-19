import json
import time

import pytest

from skyflip.http import ApiError, ApiResult
from skyflip.onboarding import reset_profile_configuration_with_confirmation
from skyflip.profile_parser import PlayerProfile
from skyflip.profile_fetcher import (
    ApiUnavailableError,
    InvalidApiKeyError,
    ProfileNotFoundError,
    fetch_hypixel_profiles,
    fetch_selected_profile_payload,
    load_api_profile,
    resolve_minecraft_uuid,
    select_profile_payload,
)
from skyflip.user_config import (
    BUDGET_SOURCE_CUSTOM,
    BUDGET_SOURCE_PURSE,
    BUDGET_SOURCE_PURSE_BANK,
    HypixelUserConfig,
    budget_from_profile,
    budget_source_label,
    config_path,
    get_api_key,
    load_user_config,
    profile_cache_path,
    reset_user_config,
    save_api_key,
    save_user_config,
)


class FakeHttp:
    def __init__(self, responses=None, error=None):
        self.responses = responses or {}
        self.error = error
        self.calls = []

    def get_json(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        payload = self.responses.get(url)
        if payload is None:
            payload = next(iter(self.responses.values()))
        return ApiResult(payload=payload, source="live", url=url)


def profile_payload():
    return {
        "success": True,
        "profiles": [
            {
                "profile_id": "one",
                "cute_name": "Apple",
                "members": {
                    "abc123abc123abc123abc123abc123ab": {
                        "player_name": "PalaMC",
                        "coin_purse": 123,
                    }
                },
            },
            {"profile_id": "two", "cute_name": "Banana", "members": {}},
        ],
    }


def test_resolve_username_to_uuid():
    http = FakeHttp({"x": {"id": "abc123abc123abc123abc123abc123ab"}})

    assert resolve_minecraft_uuid(http, "PalaMC") == "abc123abc123abc123abc123abc123ab"
    assert "api.mojang.com" in http.calls[0][0]


def test_profile_cute_name_selection_success_and_failure():
    profile_id, selected = select_profile_payload(profile_payload(), "apple", "abc123abc123abc123abc123abc123ab")

    assert profile_id == "one"
    assert selected["profile"]["cute_name"] == "Apple"
    with pytest.raises(ProfileNotFoundError) as exc:
        select_profile_payload(profile_payload(), "Zucchini", "abc123abc123abc123abc123abc123ab")
    assert "Apple" in str(exc.value)
    assert "Banana" in str(exc.value)


def test_invalid_hypixel_api_key_is_not_saved(monkeypatch, tmp_path):
    monkeypatch.setenv("SKYFLIP_CONFIG_DIR", str(tmp_path))
    http = FakeHttp({"x": {"success": False, "cause": "Invalid API key"}})

    with pytest.raises(InvalidApiKeyError):
        fetch_hypixel_profiles(http, "abc123abc123abc123abc123abc123ab", "SECRET")

    assert get_api_key() is None


def test_config_save_key_fallback_and_reset(monkeypatch, tmp_path):
    monkeypatch.setenv("SKYFLIP_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr("skyflip.user_config._keyring_set", lambda api_key: False)
    monkeypatch.setattr("skyflip.user_config._keyring_get", lambda: None)
    save_user_config(HypixelUserConfig("PalaMC", "abc123abc123abc123abc123abc123ab", "Apple", "one"))
    save_api_key("SECRET")

    assert load_user_config().selected_profile_name == "Apple"
    assert get_api_key() == "SECRET"
    assert "SECRET" in config_path().read_text(encoding="utf-8")

    reset_user_config()

    assert load_user_config() is None
    assert get_api_key() is None


def test_save_user_config_preserves_plaintext_fallback_key(monkeypatch, tmp_path):
    monkeypatch.setenv("SKYFLIP_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr("skyflip.user_config._keyring_set", lambda api_key: False)
    monkeypatch.setattr("skyflip.user_config._keyring_get", lambda: None)
    save_api_key("SECRET")

    save_user_config(HypixelUserConfig("PalaMC", "abc123abc123abc123abc123abc123ab", "Apple", "one"))
    save_user_config(HypixelUserConfig("PalaMC", "abc123abc123abc123abc123abc123ab", "Banana", "two"))

    assert get_api_key() == "SECRET"
    assert load_user_config().selected_profile_name == "Banana"


def test_budget_source_persists_and_calculates(monkeypatch, tmp_path):
    monkeypatch.setenv("SKYFLIP_CONFIG_DIR", str(tmp_path))
    profile = PlayerProfile("PalaMC", "uuid", purse=125.0, bank=875.0)

    save_user_config(HypixelUserConfig("PalaMC", "abc123abc123abc123abc123abc123ab", "Apple", "one", BUDGET_SOURCE_PURSE))
    config = load_user_config()
    assert config is not None
    assert config.budget_source == BUDGET_SOURCE_PURSE
    assert budget_from_profile(profile, config) == 125.0

    save_user_config(HypixelUserConfig("PalaMC", "abc123abc123abc123abc123abc123ab", "Apple", "one", BUDGET_SOURCE_PURSE_BANK))
    assert budget_from_profile(profile, load_user_config()) == 1000.0

    save_user_config(
        HypixelUserConfig(
            "PalaMC",
            "abc123abc123abc123abc123abc123ab",
            "Apple",
            "one",
            BUDGET_SOURCE_CUSTOM,
            250.0,
        )
    )
    config = load_user_config()
    assert budget_from_profile(profile, config) == 250.0
    assert budget_source_label(config) == "custom (250 coins)"


def test_profile_fetch_preserves_budget_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("SKYFLIP_CONFIG_DIR", str(tmp_path))
    save_user_config(
        HypixelUserConfig(
            "PalaMC",
            "abc123abc123abc123abc123abc123ab",
            "Apple",
            "one",
            BUDGET_SOURCE_CUSTOM,
            777.0,
        )
    )
    http = FakeHttp({"x": profile_payload()})

    config, _ = fetch_selected_profile_payload(
        http,
        username="PalaMC",
        profile_name="Apple",
        api_key="SECRET",
        uuid="abc123abc123abc123abc123abc123ab",
    )

    assert config.budget_source == BUDGET_SOURCE_CUSTOM
    assert config.custom_budget == 777.0
    assert load_user_config().budget_source == BUDGET_SOURCE_CUSTOM


def test_old_config_defaults_to_purse_plus_bank(monkeypatch, tmp_path):
    monkeypatch.setenv("SKYFLIP_CONFIG_DIR", str(tmp_path))
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text(
        json.dumps(
            {
                "minecraft_username": "PalaMC",
                "uuid": "abc123abc123abc123abc123abc123ab",
                "selected_profile_name": "Apple",
            }
        ),
        encoding="utf-8",
    )

    config = load_user_config()

    assert config is not None
    assert config.budget_source == BUDGET_SOURCE_PURSE_BANK
    assert config.custom_budget is None


def test_api_failure_uses_stale_profile_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("SKYFLIP_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr("skyflip.user_config._keyring_set", lambda api_key: False)
    monkeypatch.setattr("skyflip.user_config._keyring_get", lambda: None)
    save_user_config(HypixelUserConfig("PalaMC", "abc123abc123abc123abc123abc123ab", "Apple", "one"))
    save_api_key("SECRET")
    profile_cache_path().write_text(
        json.dumps(
            {
                "created_at": time.time() - 10_000,
                "source": "api",
                "payload": {"profile": profile_payload()["profiles"][0]},
            }
        ),
        encoding="utf-8",
    )
    http = FakeHttp(error=ApiError("503 without SECRET"))

    loaded = load_api_profile(http, force_refresh=True, ttl_seconds=1)

    assert loaded.profile.player_name == "PalaMC"
    assert loaded.source == "api-cache-stale"
    assert "SECRET" not in "\n".join(loaded.warnings)


def test_reset_confirmation_flow(monkeypatch, tmp_path):
    monkeypatch.setenv("SKYFLIP_CONFIG_DIR", str(tmp_path))
    save_user_config(HypixelUserConfig("PalaMC", "abc123abc123abc123abc123abc123ab", "Apple", "one"))
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    assert reset_profile_configuration_with_confirmation()
    assert load_user_config() is None
