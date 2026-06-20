import json
from pathlib import Path

from skyflip.accessory_analysis import analyze_accessories
from skyflip.accessory_filters import filters_from_args
from skyflip.accessory_models import AccessoryFilters
from skyflip.accessory_database import load_accessory_database
from skyflip.ah_underpriced import WatchItem, evaluate_watch_item, load_watchlist
from skyflip.bazaar import BazaarPrice
from skyflip.bazaar_compression import load_conversions
from skyflip.cofl import ActiveAuctions, SoldSummary
from skyflip.dataset_repair import WikiConfirmation, audit_datasets, repair_datasets
from skyflip.datasets import check_usage_command, generate_obvious_compressions, runtime_dataset_warning, summary_command
from skyflip.dataset_validation import validate_all_datasets, validate_bazaar_conversions
from skyflip.profile_parser import PlayerProfile
from skyflip.scoring import AnalyzerConfig

class FakeBazaar:
    warnings = []

    def price_for(self, tag, *, use_buy_order_cost=False):
        return BazaarPrice(tag, 1, "fake")


class FakeCofl:
    warnings = []
    calls = []

    def active_bins(self, tag):
        self.calls.append(("active_bins", tag))
        return ActiveAuctions()

    def sold_summary(self, tag):
        self.calls.append(("sold_summary", tag))
        return SoldSummary()

    def analysis(self, tag, days):
        self.calls.append(("analysis", tag))
        return None


class FakeWiki:
    def confirm_item(self, name, tag=None):
        if tag in {"WOOD_TALISMAN", "TEST_RING"}:
            return WikiConfirmation(name, f"https://wiki.hypixel.net/{name.replace(' ', '_')}")
        return None


def test_local_datasets_validate_without_errors_offline():
    result = validate_all_datasets(bazaar_product_ids=None)

    assert not result.errors
    assert result.valid_entries > 0
    assert result.uncertain_entries == 0


def test_runtime_dataset_warning_ignores_uncertain_and_disabled_entries():
    assert runtime_dataset_warning() is None


def test_runtime_dataset_warning_reports_real_errors(tmp_path):
    path = tmp_path / "missing.json"

    warning = runtime_dataset_warning(paths={"craft_recipes": path})

    assert warning == "1 dataset error; run `python -m skyflip datasets validate` for details."


def test_disabled_entries_are_skipped_by_loaders(tmp_path):
    conversions = tmp_path / "bazaar_conversions.json"
    conversions.write_text(
        json.dumps(
            {
                "conversions": [
                    {
                        "name": "Disabled",
                        "input_product_id": "A",
                        "input_amount": 160,
                        "output_product_id": "B",
                        "output_amount": 1,
                        "conversion_type": "compression",
                        "craftable_manually": True,
                        "disabled": True,
                    },
                    {
                        "name": "Enabled",
                        "input_product_id": "C",
                        "input_amount": 160,
                        "output_product_id": "D",
                        "output_amount": 1,
                        "conversion_type": "compression",
                        "craftable_manually": True,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    watchlist = tmp_path / "ah_watchlist.json"
    watchlist.write_text(
        json.dumps(
            {
                "items": [
                    {"tag": "A", "name": "A", "category": "test", "enabled": False},
                    {"tag": "B", "name": "B", "category": "test"},
                ]
            }
        ),
        encoding="utf-8",
    )

    assert [item.name for item in load_conversions(conversions)] == ["Enabled"]
    assert [item.tag for item in load_watchlist(watchlist)] == ["B"]


def test_watchlist_market_capability_skips_unsupported_cofl_tag():
    cofl = FakeCofl()
    item = WatchItem("ELEPHANT;0", "Elephant", "pet", market_source="pet_ah", cofl_auction_supported=False)
    profile = PlayerProfile("PalaMC", "id", 0, 0, inventory_api_enabled=True)

    result = evaluate_watch_item(item, cofl, profile, AnalyzerConfig(budget=1_000_000))

    assert result.reason == "market data unsupported for pet_ah"
    assert cofl.calls == []


def test_bazaar_conversion_validation_uses_mocked_product_ids(tmp_path):
    path = tmp_path / "bazaar_conversions.json"
    path.write_text(
        json.dumps(
            {
                "conversions": [
                    {
                        "name": "Bad",
                        "input_product_id": "A",
                        "input_amount": 160,
                        "output_product_id": "MISSING",
                        "output_amount": 1,
                        "conversion_type": "compression",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = validate_bazaar_conversions(path, bazaar_product_ids={"A"})

    assert result.errors
    assert "not a live Bazaar product" in result.errors[0].message


def test_manual_verification_metadata_does_not_make_entry_uncertain(tmp_path):
    path = tmp_path / "bazaar_conversions.json"
    path.write_text(
        json.dumps(
            {
                "conversions": [
                    {
                        "name": "Manual check",
                        "input_product_id": "A",
                        "input_amount": 160,
                        "output_product_id": "B",
                        "output_amount": 1,
                        "conversion_type": "compression",
                        "verified": True,
                        "confidence": "medium",
                        "requires_manual_verification": True,
                        "source_notes": "Known conversion pattern; verify the final in-game step manually.",
                        "last_verified": "2026-06-20",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = validate_bazaar_conversions(path, bazaar_product_ids={"A", "B"})

    assert result.valid_entries == 1
    assert result.uncertain_entries == 0


def test_ownership_detection_only_metadata_is_valid_not_uncertain(tmp_path):
    root = tmp_path
    data = root / "data"
    data.mkdir()
    (data / "accessories.json").write_text(
        json.dumps(
            {
                "accessories": [
                    {
                        "item_id": "TEST_CHARM",
                        "display_name": "Test Charm",
                        "rarity": "common",
                        "family_id": "test_charm",
                        "tier_index": 0,
                        "is_accessory": True,
                        "auctionable": True,
                        "soulbound": False,
                        "source_types": ["ah"],
                        "requirements": {},
                        "recipe": None,
                        "verified": True,
                        "confidence": "medium",
                        "ownership_detection_only": True,
                        "source_notes": "metadata only",
                        "last_verified": "2026-06-20",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (data / "ah_watchlist.json").write_text(json.dumps({"items": []}), encoding="utf-8")
    (data / "bazaar_conversions.json").write_text(json.dumps({"conversions": []}), encoding="utf-8")
    (data / "craft_recipes.json").write_text(json.dumps({"recipes": []}), encoding="utf-8")

    result = validate_all_datasets(root=root)

    assert result.valid_entries == 1
    assert result.uncertain_entries == 0


def test_dataset_repair_classifies_and_confirms_with_mocked_wiki(tmp_path):
    root = tmp_path
    data = root / "data"
    data.mkdir()
    (data / "accessories.json").write_text(
        json.dumps(
            {
                "accessories": [
                    {
                        "item_id": "TEST_RING",
                        "display_name": "Test Ring",
                        "rarity": "common",
                        "family_id": "test_ring",
                        "tier_index": 0,
                        "is_accessory": True,
                        "auctionable": True,
                        "soulbound": False,
                        "source_types": ["ah"],
                        "requirements": {},
                        "recipe": None,
                        "auto_generated": True,
                        "verified": False,
                        "confidence": "low",
                        "uncertain_requirements": True,
                        "source_notes": "old",
                        "last_verified": "2026-06-20",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (data / "ah_watchlist.json").write_text(
        json.dumps({"items": [{"tag": "ELEPHANT;0", "name": "Elephant", "category": "pet", "max_budget_percent": 10}]}),
        encoding="utf-8",
    )
    (data / "bazaar_conversions.json").write_text(json.dumps({"conversions": []}), encoding="utf-8")
    (data / "craft_recipes.json").write_text(
        json.dumps(
            {
                "recipes": [
                    {
                        "output": {"tag": "WOOD_TALISMAN", "display_name": "Wood Talisman", "quantity": 1, "auctionable": False},
                        "ingredients": [{"item_tag": "LOG", "display_name": "Oak Log", "amount": 8, "source": "bazaar"}],
                        "verified": False,
                        "confidence": "low",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    audit = audit_datasets(root=root, wiki=FakeWiki())
    repair = repair_datasets(root=root, wiki=FakeWiki())
    repaired_accessory = json.loads((data / "accessories.json").read_text(encoding="utf-8"))["accessories"][0]
    repaired_watch = json.loads((data / "ah_watchlist.json").read_text(encoding="utf-8"))["items"][0]
    repaired_recipe = json.loads((data / "craft_recipes.json").read_text(encoding="utf-8"))["recipes"][0]

    assert audit.issue_counts["accessory_ownership_detection_only"] == 1
    assert repaired_accessory["ownership_detection_only"] is True
    assert repaired_accessory["recommendation_eligible"] is False
    assert repaired_accessory["verified"] is True
    assert repaired_watch["market_source"] == "pet_ah"
    assert repaired_watch["cofl_auction_supported"] is False
    assert repaired_recipe["disabled"] is True
    assert not repair.validation.errors


def test_obvious_bazaar_compression_generation_uses_only_existing_products():
    rows = generate_obvious_compressions({"COBBLESTONE", "ENCHANTED_COBBLESTONE", "ENCHANTED_COBBLESTONE_BLOCK"})
    pairs = {(row["input_product_id"], row["output_product_id"]) for row in rows}

    assert ("COBBLESTONE", "ENCHANTED_COBBLESTONE") in pairs
    assert ("ENCHANTED_COBBLESTONE", "ENCHANTED_COBBLESTONE_BLOCK") in pairs


def test_summary_and_check_usage_commands_print(capsys):
    assert summary_command() == 0
    summary_output = capsys.readouterr().out
    assert "Dataset summary" in summary_output
    assert "Accessories:" in summary_output

    assert check_usage_command() == 0
    usage_output = capsys.readouterr().out
    assert "Dataset usage" in usage_output
    assert "Dashboard calls load_recipes: ok" in usage_output


def test_cli_default_filters_hide_uncertain_accessories():
    args = type("Args", (), {})()

    filters = filters_from_args(args)

    assert filters.include_uncertain is False


def test_uncertain_accessory_filter_prevents_low_confidence_rows():
    db = load_accessory_database("data/accessories.json")
    profile = PlayerProfile("PalaMC", "id", 0, 0, inventory_api_enabled=True)
    analysis = analyze_accessories(
        profile,
        FakeBazaar(),
        FakeCofl(),
        database=db,
        filters=AccessoryFilters(include_uncertain=False, include_ah=False, show_locked=True, hide_locked=False),
    )

    assert analysis.rows
    assert all(row.entry.confidence != "low" for row in analysis.rows)
