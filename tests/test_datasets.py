import json
from pathlib import Path

from skyflip.accessory_analysis import analyze_accessories
from skyflip.accessory_filters import filters_from_args
from skyflip.accessory_models import AccessoryFilters
from skyflip.accessory_database import load_accessory_database
from skyflip.ah_underpriced import load_watchlist
from skyflip.bazaar import BazaarPrice
from skyflip.bazaar_compression import load_conversions
from skyflip.cofl import ActiveAuctions, SoldSummary
from skyflip.datasets import check_usage_command, generate_obvious_compressions, summary_command
from skyflip.dataset_validation import validate_all_datasets, validate_bazaar_conversions
from skyflip.profile_parser import PlayerProfile

class FakeBazaar:
    warnings = []

    def price_for(self, tag, *, use_buy_order_cost=False):
        return BazaarPrice(tag, 1, "fake")


class FakeCofl:
    warnings = []

    def active_bins(self, tag):
        return ActiveAuctions()

    def sold_summary(self, tag):
        return SoldSummary()


def test_local_datasets_validate_without_errors_offline():
    result = validate_all_datasets(bazaar_product_ids=None)

    assert not result.errors
    assert result.valid_entries > 0
    assert result.uncertain_entries > 0


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
