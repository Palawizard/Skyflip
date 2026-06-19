from __future__ import annotations

import pytest

from skyflip.cli import _normalize_dashboard_args, build_parser, main


def test_dashboard_help_groups_discoverable():
    parser = build_parser()
    dashboard_parser = parser._subparsers._group_actions[0].choices["dashboard"]

    help_text = dashboard_parser.format_help()

    assert "Profile:" in help_text
    assert "Modules:" in help_text
    assert "Presets:" in help_text
    assert "Exports:" in help_text
    assert "Advanced settings:" in help_text
    assert "--module NAME" in help_text


def test_module_alias_maps_to_sections():
    parser = build_parser()
    args = parser.parse_args(["dashboard", "--module", "bazaar,accessories"])

    _normalize_dashboard_args(args, parser)

    assert args.sections == "bazaar-spread,bazaar-order,talisman"
    assert args.selected_modules == ("bazaar", "accessories")


def test_module_alias_keeps_raw_sections_compatible():
    parser = build_parser()
    args = parser.parse_args(["dashboard", "--sections", "craft", "--module", "compression"])

    _normalize_dashboard_args(args, parser)

    assert args.sections == "craft,bazaar-compression"


def test_module_alias_rejects_unknown_module():
    parser = build_parser()
    args = parser.parse_args(["dashboard", "--module", "unknown"])

    with pytest.raises(SystemExit):
        _normalize_dashboard_args(args, parser)


def test_dashboard_preset_flags_apply_existing_presets():
    parser = build_parser()
    args = parser.parse_args(["dashboard", "--bazaar-preset", "safe", "--craft-preset", "risky"])

    _normalize_dashboard_args(args, parser)

    assert args.spread_limit == 8
    assert args.min_speed_confidence == 60.0
    assert args.min_profit == 1_000.0
    assert args.use_buy_order_cost is True


def test_main_module_alias_reaches_dashboard(monkeypatch):
    captured = {}
    monkeypatch.setattr("skyflip.cli.ensure_profile_configuration", lambda http, force_setup=False: None)

    def fake_run_dashboard(args, *, resolve_uuid):
        captured["sections"] = args.sections
        return 0

    monkeypatch.setattr("skyflip.cli.run_dashboard", fake_run_dashboard)

    result = main(
        [
            "dashboard",
            "--profile-file",
            "profile.json",
            "--player-name",
            "PalaMC",
            "--budget",
            "1000",
            "--once",
            "--module",
            "ah-bin",
        ]
    )

    assert result == 0
    assert captured["sections"] == "ah-underpriced"
