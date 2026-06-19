from __future__ import annotations

from skyflip.onboarding import run_onboarding
from skyflip.profile_fetcher import InvalidApiKeyError, ProfileNotFoundError
from skyflip.user_config import BUDGET_SOURCE_CUSTOM, BUDGET_SOURCE_PURSE, HypixelUserConfig, load_user_config


UUID = "abc123abc123abc123abc123abc123ab"


def selected_payload(purse: float = 100.0, bank: float = 900.0) -> dict:
    return {
        "profile": {
            "profile_id": "one",
            "cute_name": "Apple",
            "banking": {"balance": bank},
            "members": {
                UUID: {
                    "player_name": "PalaMC",
                    "coin_purse": purse,
                }
            },
        },
        "_skyflip_profile_source": "api",
        "_skyflip_profile_fetched_at": 123.0,
    }


def test_onboarding_handles_key_and_profile_errors(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SKYFLIP_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr("skyflip.onboarding.resolve_minecraft_uuid", lambda http, username: UUID)
    monkeypatch.setattr("skyflip.onboarding.get_api_key", lambda: None)
    monkeypatch.setattr("skyflip.onboarding.delete_api_key", lambda: None)
    monkeypatch.setattr("skyflip.onboarding.save_api_key", lambda api_key: True)
    inputs = iter(["PalaMC", "Wrong", "Apple", "1"])
    keys = iter(["SECRET_BAD", "SECRET_GOOD", "SECRET_GOOD"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(keys))
    calls = []

    def fake_fetch(http, *, username, profile_name, api_key, uuid=None):
        calls.append((profile_name, api_key))
        if len(calls) == 1:
            raise InvalidApiKeyError("SECRET_BAD should not be printed")
        if len(calls) == 2:
            raise ProfileNotFoundError(profile_name, ["Apple"])
        config = HypixelUserConfig(username, uuid or UUID, profile_name, "one")
        return config, selected_payload()

    monkeypatch.setattr("skyflip.onboarding.fetch_selected_profile_payload", fake_fetch)

    config = run_onboarding(object())
    output = capsys.readouterr().out

    assert config.selected_profile_name == "Apple"
    assert config.budget_source == BUDGET_SOURCE_PURSE
    assert load_user_config().budget_source == BUDGET_SOURCE_PURSE
    assert "Step 1/5 - Minecraft account" in output
    assert "Step 4/5 - Budget source" in output
    assert "Step 5/5 - Confirmation" in output
    assert "Available profiles: Apple" in output
    assert "SECRET_BAD" not in output
    assert "SECRET_GOOD" not in output


def test_onboarding_accepts_custom_budget(monkeypatch, tmp_path):
    monkeypatch.setenv("SKYFLIP_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr("skyflip.onboarding.resolve_minecraft_uuid", lambda http, username: UUID)
    monkeypatch.setattr("skyflip.onboarding.get_api_key", lambda: None)
    monkeypatch.setattr("skyflip.onboarding.save_api_key", lambda api_key: True)
    inputs = iter(["PalaMC", "Apple", "3", "456"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "SECRET")

    def fake_fetch(http, *, username, profile_name, api_key, uuid=None):
        return HypixelUserConfig(username, uuid or UUID, profile_name, "one"), selected_payload()

    monkeypatch.setattr("skyflip.onboarding.fetch_selected_profile_payload", fake_fetch)

    config = run_onboarding(object())

    assert config.budget_source == BUDGET_SOURCE_CUSTOM
    assert config.custom_budget == 456.0
    assert load_user_config().custom_budget == 456.0
