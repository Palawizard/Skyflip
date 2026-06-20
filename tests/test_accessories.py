import json
from dataclasses import replace
from pathlib import Path

from skyflip.accessories import (
    AccessoryDatabase,
    AccessoryFilters,
    analyze_accessories,
    augment_with_hypixel_accessories,
    detect_owned_accessories,
    get_best_owned_tier_by_family,
    is_downgrade_covered,
    load_accessory_database,
    missing_requirements,
)
from skyflip.bazaar import BazaarPrice
from skyflip.cofl import ActiveAuctions, SoldSummary
from skyflip.profile_parser import PlayerProfile, _looks_like_skyblock_item_id, parse_profile
from skyflip.terminal import print_dashboard_section


class FakeBazaar:
    warnings = []

    def __init__(self, prices=None):
        self.prices = prices or {}

    def price_for(self, tag, *, use_buy_order_cost=False):
        value = self.prices.get(tag)
        if value is None:
            return None
        return BazaarPrice(tag, value, "fake")


class FakeCofl:
    warnings = []

    def __init__(self, bins=None, sold=None, fail=False):
        self.bins = bins or {}
        self.sold = sold or {}
        self.fail = fail

    def active_bins(self, tag):
        if self.fail:
            return ActiveAuctions()
        prices = sorted(self.bins.get(tag, []))
        return ActiveAuctions(
            prices=prices,
            active_count=len(prices),
            lowest_bin=prices[0] if len(prices) > 0 else None,
            second_lowest_bin=prices[1] if len(prices) > 1 else None,
            third_lowest_bin=prices[2] if len(prices) > 2 else None,
        )

    def sold_summary(self, tag):
        return self.sold.get(tag, SoldSummary())


class FakeHttp:
    def __init__(self, items):
        self.items = items

    def get_json(self, url):
        return type("Result", (), {"payload": {"items": self.items}})()


def verified_db(*item_ids):
    db = load_accessory_database("data/accessories.json")
    wanted = set(item_ids)
    return type(db)([
        replace(item, recipe_verified=True)
        if item.item_id in wanted
        else item
        for item in db.accessories
    ])


def test_accessory_database_loading_starter_set():
    db = load_accessory_database("data/accessories.json")

    assert "VACCINE_TALISMAN" in db.by_id
    assert db.by_id["VACCINE_RING"].upgrade_from == "VACCINE_TALISMAN"
    assert db.by_family["zombie"][-1].item_id == "ZOMBIE_ARTIFACT"


def test_profile_parser_extracts_accessory_item_ids_from_plain_profile():
    profile = parse_profile({"members": {"id": {"inventory": {"items": [{"id": "zombie_artifact"}]}}}})

    assert "ZOMBIE_ARTIFACT" in profile.item_ids
    assert profile.inventory_api_enabled is True


def test_family_deduplication_higher_tier_covers_lower():
    db = load_accessory_database("data/accessories.json")
    profile = PlayerProfile("PalaMC", "id", 0, 0, item_ids=["ZOMBIE_ARTIFACT"], inventory_api_enabled=True)
    ownership = detect_owned_accessories(profile, db)

    assert "ZOMBIE_ARTIFACT" in ownership.owned_exact
    assert ownership.owned_family_best["zombie"] == "ZOMBIE_ARTIFACT"
    assert "ZOMBIE_TALISMAN" in ownership.covered_by_higher_tier
    assert "ZOMBIE_RING" in ownership.covered_by_higher_tier


def test_family_helper_returns_best_owned_tier_and_covered_lowers():
    db = load_accessory_database("data/accessories.json")
    best = get_best_owned_tier_by_family({"ZOMBIE_RING", "VACCINE_RING"}, db)

    assert best["zombie"].family_id == "zombie"
    assert best["zombie"].item_id == "ZOMBIE_RING"
    assert best["zombie"].tier_index == 1
    assert best["zombie"].covered_lower_tiers == {"ZOMBIE_TALISMAN"}
    assert is_downgrade_covered(db.by_id["VACCINE_TALISMAN"], best)


def test_missing_accessory_detection_does_not_recommend_covered_lower_tier():
    db = load_accessory_database("data/accessories.json")
    profile = PlayerProfile("PalaMC", "id", 0, 0, item_ids=["ZOMBIE_ARTIFACT"], inventory_api_enabled=True)
    analysis = analyze_accessories(profile, FakeBazaar(), FakeCofl(), database=db, filters=AccessoryFilters(include_ah=False))

    names = {row.entry.item_id for row in analysis.all_missing}
    assert "ZOMBIE_TALISMAN" not in names
    assert "ZOMBIE_RING" not in names


def test_owning_lower_tier_recommends_next_buyable_upgrade():
    db = load_accessory_database("data/accessories.json")
    profile = PlayerProfile("PalaMC", "id", 0, 0, item_ids=["ZOMBIE_RING"], inventory_api_enabled=True)
    analysis = analyze_accessories(
        profile,
        FakeBazaar(),
        FakeCofl({"ZOMBIE_ARTIFACT": [2_000_000, 2_100_000]}),
        database=db,
        filters=AccessoryFilters(),
    )

    recommended = {row.entry.item_id: row for row in analysis.recommendations}
    assert "ZOMBIE_TALISMAN" not in recommended
    assert "ZOMBIE_ARTIFACT" in recommended
    assert recommended["ZOMBIE_ARTIFACT"].reasons[0] == "upgrade owned tier"


def test_exact_owned_accessory_not_recommended():
    db = load_accessory_database("data/accessories.json")
    profile = PlayerProfile("PalaMC", "id", 0, 0, item_ids=["VACCINE_TALISMAN"], inventory_api_enabled=True)
    analysis = analyze_accessories(profile, FakeBazaar(), FakeCofl(), database=db, filters=AccessoryFilters(include_ah=False))

    assert "VACCINE_TALISMAN" not in {row.entry.item_id for row in analysis.recommendations}
    assert "VACCINE_TALISMAN" not in {row.entry.item_id for row in analysis.all_missing}


def test_named_upgrade_families_hide_downgrades():
    db = load_accessory_database("data/accessories.json")
    cases = [
        ("ZOMBIE_ARTIFACT", {"ZOMBIE_TALISMAN", "ZOMBIE_RING"}),
        ("VACCINE_RING", {"VACCINE_TALISMAN"}),
        ("POTION_AFFINITY_ARTIFACT", {"POTION_AFFINITY_TALISMAN", "POTION_AFFINITY_RING"}),
        ("PERSONAL_COMPACTOR_6000", {"PERSONAL_COMPACTOR_4000", "PERSONAL_COMPACTOR_5000"}),
        ("GANACHE_CHOCOLATE_SLAB", {"NIBBLE_CHOCOLATE_STICK", "SMOOTH_CHOCOLATE_BAR", "RICH_CHOCOLATE_CHUNK"}),
    ]
    for owned_item, hidden in cases:
        profile = PlayerProfile("PalaMC", "id", 0, 0, item_ids=[owned_item], inventory_api_enabled=True)
        analysis = analyze_accessories(profile, FakeBazaar(), FakeCofl(), database=db, filters=AccessoryFilters(include_ah=False))
        recommended = {row.entry.item_id for row in analysis.recommendations}
        missing = {row.entry.item_id for row in analysis.all_missing}
        covered = {row.entry.item_id for row in analysis.owned if row.covered_by_higher_tier}

        assert not (hidden & recommended)
        assert not (hidden & missing)
        assert hidden <= covered


def test_ganache_chocolate_slab_covers_smooth_chocolate_bar():
    db = load_accessory_database("data/accessories.json")
    profile = PlayerProfile("PalaMC", "id", 0, 0, accessory_bag_item_ids=["GANACHE_CHOCOLATE_SLAB"], inventory_api_enabled=True)
    analysis = analyze_accessories(
        profile,
        FakeBazaar(),
        FakeCofl({"SMOOTH_CHOCOLATE_BAR": [50_000], "PRESTIGE_CHOCOLATE_REALM": [1_000_000]}),
        database=db,
        filters=AccessoryFilters(max_recommendations=50, include_uncertain=True),
    )

    recommended = {row.entry.item_id for row in analysis.recommendations}
    covered = {row.entry.item_id for row in analysis.owned if row.covered_by_higher_tier}
    assert "GANACHE_CHOCOLATE_SLAB" in analysis.ownership.owned_exact
    assert "SMOOTH_CHOCOLATE_BAR" not in recommended
    assert "RICH_CHOCOLATE_CHUNK" not in recommended
    assert "NIBBLE_CHOCOLATE_STICK" not in recommended
    assert {"NIBBLE_CHOCOLATE_STICK", "SMOOTH_CHOCOLATE_BAR", "RICH_CHOCOLATE_CHUNK"} <= covered
    assert "PRESTIGE_CHOCOLATE_REALM" in recommended


def test_auto_imported_non_suffix_chain_gets_inferred_family():
    db = augment_with_hypixel_accessories(
        AccessoryDatabase([]),
        FakeHttp([
            {"id": "NIBBLE_CHOCOLATE_STICK", "name": "Nibble Chocolate Stick", "category": "ACCESSORY", "tier": "COMMON"},
            {"id": "SMOOTH_CHOCOLATE_BAR", "name": "Smooth Chocolate Bar", "category": "ACCESSORY", "tier": "UNCOMMON"},
            {"id": "RICH_CHOCOLATE_CHUNK", "name": "Rich Chocolate Chunk", "category": "ACCESSORY", "tier": "RARE"},
            {"id": "GANACHE_CHOCOLATE_SLAB", "name": "Ganache Chocolate Slab", "category": "ACCESSORY", "tier": "EPIC"},
            {"id": "PRESTIGE_CHOCOLATE_REALM", "name": "Prestige Chocolate Realm", "category": "ACCESSORY", "tier": "LEGENDARY"},
        ]),
    )
    profile = PlayerProfile("PalaMC", "id", 0, 0, accessory_bag_item_ids=["GANACHE_CHOCOLATE_SLAB"], inventory_api_enabled=True)
    analysis = analyze_accessories(
        profile,
        FakeBazaar(),
        FakeCofl({"SMOOTH_CHOCOLATE_BAR": [50_000], "PRESTIGE_CHOCOLATE_REALM": [1_000_000]}),
        database=db,
        filters=AccessoryFilters(max_recommendations=50, include_uncertain=True),
    )

    assert db.by_id["SMOOTH_CHOCOLATE_BAR"].family_id == "chocolate"
    assert db.by_id["GANACHE_CHOCOLATE_SLAB"].tier_index > db.by_id["SMOOTH_CHOCOLATE_BAR"].tier_index
    assert "SMOOTH_CHOCOLATE_BAR" not in {row.entry.item_id for row in analysis.recommendations}
    assert "PRESTIGE_CHOCOLATE_REALM" in {row.entry.item_id for row in analysis.recommendations}


def test_auto_imported_numeric_chain_gets_inferred_family():
    db = augment_with_hypixel_accessories(
        AccessoryDatabase([]),
        FakeHttp([
            {"id": "WIDGET_COMPRESSOR_1000", "name": "Widget Compressor 1000", "category": "ACCESSORY", "tier": "COMMON"},
            {"id": "WIDGET_COMPRESSOR_2000", "name": "Widget Compressor 2000", "category": "ACCESSORY", "tier": "UNCOMMON"},
        ]),
    )
    profile = PlayerProfile("PalaMC", "id", 0, 0, item_ids=["WIDGET_COMPRESSOR_2000"], inventory_api_enabled=True)
    analysis = analyze_accessories(profile, FakeBazaar(), FakeCofl(), database=db, filters=AccessoryFilters(include_ah=False))

    assert db.by_id["WIDGET_COMPRESSOR_1000"].family_id == "widget_compressor"
    assert "WIDGET_COMPRESSOR_1000" in analysis.ownership.covered_by_higher_tier


def test_accessory_bag_id_heuristic_recognizes_chocolate_accessories():
    assert _looks_like_skyblock_item_id("GANACHE_CHOCOLATE_SLAB")


def test_personal_compactor_6000_can_recommend_7000_upgrade():
    db = load_accessory_database("data/accessories.json")
    profile = PlayerProfile("PalaMC", "id", 0, 0, item_ids=["PERSONAL_COMPACTOR_6000"], inventory_api_enabled=True)
    analysis = analyze_accessories(
        profile,
        FakeBazaar(),
        FakeCofl({"PERSONAL_COMPACTOR_7000": [15_000_000, 15_500_000]}),
        database=db,
        filters=AccessoryFilters(max_recommendations=50),
    )

    recommended = {row.entry.item_id for row in analysis.recommendations}
    assert "PERSONAL_COMPACTOR_4000" not in recommended
    assert "PERSONAL_COMPACTOR_7000" in recommended


def test_normalized_display_names_with_reforge_are_detected_as_owned():
    db = load_accessory_database("data/accessories.json")
    profile = PlayerProfile("PalaMC", "id", 0, 0, item_ids=["§aShaded Zombie Ring ✪"], inventory_api_enabled=True)
    ownership = detect_owned_accessories(profile, db)

    assert "ZOMBIE_RING" in ownership.owned_exact
    assert "ZOMBIE_TALISMAN" in ownership.covered_by_higher_tier


def test_craftability_from_collections_and_craft_cost():
    db = verified_db("VACCINE_TALISMAN")
    profile = PlayerProfile("PalaMC", "id", 0, 0, collection_tiers={"POTATO_ITEM": 3}, inventory_api_enabled=True)
    analysis = analyze_accessories(
        profile,
        FakeBazaar({"POISONOUS_POTATO": 12}),
        FakeCofl(),
        database=db,
        filters=AccessoryFilters(include_ah=False, only_craftable=True),
    )

    vaccine = next(row for row in analysis.craftable if row.entry.item_id == "VACCINE_TALISMAN")
    assert vaccine.status == "Craftable now"
    assert vaccine.craft_cost == 108
    assert "Poisonous Potato" in vaccine.shopping_list[0]


def test_craftability_from_skill_and_slayer_locks():
    db = load_accessory_database("data/accessories.json")
    profile = PlayerProfile("PalaMC", "id", 0, 0, skills={"combat": 5}, slayer_levels={"zombie": 1}, inventory_api_enabled=True)

    wolf_paw = db.by_id["WOLF_PAW"]
    zombie_ring = db.by_id["ZOMBIE_RING"]

    assert "combat 5 < 12" in missing_requirements(wolf_paw, profile)
    assert "zombie slayer 1 < 2" in missing_requirements(zombie_ring, profile)


def test_locked_reason_generation():
    db = load_accessory_database("data/accessories.json")
    profile = PlayerProfile("PalaMC", "id", 0, 0, collection_tiers={"COBBLESTONE": 1}, inventory_api_enabled=True)
    analysis = analyze_accessories(profile, FakeBazaar(), FakeCofl(), database=db, filters=AccessoryFilters(show_locked=True, hide_locked=False, include_ah=False))

    haste = next(row for row in analysis.locked if row.entry.item_id == "HASTE_RING")
    assert "COBBLESTONE collection tier 1 < 8" in haste.missing_requirements


def test_nested_upgrade_cost_uses_previous_tier_craft():
    db = verified_db("VACCINE_TALISMAN", "VACCINE_RING")
    profile = PlayerProfile("PalaMC", "id", 0, 0, collection_tiers={"POTATO_ITEM": 5}, inventory_api_enabled=True)
    analysis = analyze_accessories(
        profile,
        FakeBazaar({"POISONOUS_POTATO": 10, "ENCHANTED_POISONOUS_POTATO": 100}),
        FakeCofl(),
        database=db,
        filters=AccessoryFilters(include_ah=False),
    )

    ring = next(row for row in analysis.all_missing if row.entry.item_id == "VACCINE_RING")
    assert ring.status == "Craftable now"
    assert ring.craft_cost == 3_290


def test_ah_availability_normalization_and_safety():
    db = load_accessory_database("data/accessories.json")
    profile = PlayerProfile("PalaMC", "id", 0, 0, inventory_api_enabled=True)
    analysis = analyze_accessories(
        profile,
        FakeBazaar(),
        FakeCofl({"SPIDER_TALISMAN": [50_000, 52_000, 55_000]}, {"SPIDER_TALISMAN": SoldSummary(median_price=54_000, sale_count=10)}),
        database=db,
        filters=AccessoryFilters(only_ah=True, max_ah_checks=500),
    )

    spider = next(row for row in analysis.ah_available if row.entry.item_id == "SPIDER_TALISMAN")
    assert spider.available_on_ah is True
    assert spider.ah.active.lowest_bin == 50_000
    assert spider.ah.safe_price == 50_000


def test_recommendation_scoring_and_sorting_filtering():
    db = verified_db("FARMING_TALISMAN", "VACCINE_TALISMAN")
    profile = PlayerProfile("PalaMC", "id", 0, 0, collection_tiers={"WHEAT": 4, "POTATO_ITEM": 3}, inventory_api_enabled=True)
    analysis = analyze_accessories(
        profile,
        FakeBazaar({"HAY_BLOCK": 1, "POISONOUS_POTATO": 1000}),
        FakeCofl(),
        database=db,
        filters=AccessoryFilters(sort_key="craft-cost", descending=False, include_ah=False, only_craftable=True),
    )

    assert analysis.craftable[0].entry.item_id == "FARMING_TALISMAN"
    assert analysis.craftable[0].score > 0


def test_terminal_rendering_smoke(capsys):
    db = verified_db("FARMING_TALISMAN")
    profile = PlayerProfile("PalaMC", "id", 0, 0, collection_tiers={"WHEAT": 4}, magical_power=10, inventory_api_enabled=True)
    analysis = analyze_accessories(profile, FakeBazaar({"HAY_BLOCK": 1}), FakeCofl(), database=db, filters=AccessoryFilters(include_ah=False))
    data = type("Data", (), {"talisman_helper": analysis})()

    print_dashboard_section(data, "talisman")

    output = capsys.readouterr().out
    assert "Accessories Helper" in output
    assert "MP: 10" in output
    assert "Farming Talisman" in output
    assert "Accessory" in output
    assert "Rarity" in output
    assert "Action" in output
    assert "Cost" in output
    assert "Why" in output
    assert "Median sold" not in output
    assert "Shopping" not in output


def test_default_recommended_table_hides_locked_and_long_recipe_info(capsys):
    db = verified_db("FARMING_TALISMAN")
    profile = PlayerProfile("PalaMC", "id", 0, 0, collection_tiers={"WHEAT": 4}, magical_power=10, inventory_api_enabled=True)
    analysis = analyze_accessories(profile, FakeBazaar({"HAY_BLOCK": 1}), FakeCofl(), database=db, filters=AccessoryFilters(include_ah=False))
    data = type("Data", (), {"talisman_helper": analysis})()

    print_dashboard_section(data, "talisman")

    output = capsys.readouterr().out
    assert "Hay Bale from Bazaar" not in output
    assert "Locked" not in output


def test_details_view_contains_long_recipe_info_only_on_demand(capsys):
    db = verified_db("FARMING_TALISMAN")
    profile = PlayerProfile("PalaMC", "id", 0, 0, collection_tiers={"WHEAT": 4}, magical_power=10, inventory_api_enabled=True)
    analysis = analyze_accessories(profile, FakeBazaar({"HAY_BLOCK": 1}), FakeCofl(), database=db, filters=AccessoryFilters(view="details", include_ah=False))
    data = type("Data", (), {"talisman_helper": analysis})()

    print_dashboard_section(data, "talisman")

    output = capsys.readouterr().out
    assert "Details" in output
    assert "Hay Bale from Bazaar" in output


def test_owned_covered_view_shows_covered_downgrades(capsys):
    db = load_accessory_database("data/accessories.json")
    profile = PlayerProfile("PalaMC", "id", 0, 0, item_ids=["ZOMBIE_ARTIFACT"], inventory_api_enabled=True)
    analysis = analyze_accessories(profile, FakeBazaar(), FakeCofl(), database=db, filters=AccessoryFilters(view="owned-covered", include_ah=False))
    data = type("Data", (), {"talisman_helper": analysis})()

    print_dashboard_section(data, "talisman")

    output = capsys.readouterr().out
    assert "Owned / Covered" in output
    assert "Zombie Artifact" in output
    assert "Zombie Ring" in output
    assert "Zombie Talisman" in output


def test_incomplete_accessory_data_warning_appears_once(capsys):
    db = load_accessory_database("data/accessories.json")
    profile = PlayerProfile("PalaMC", "id", 0, 0)
    analysis = analyze_accessories(profile, FakeBazaar(), FakeCofl(), database=db, filters=AccessoryFilters(include_ah=False))
    data = type("Data", (), {"talisman_helper": analysis})()

    print_dashboard_section(data, "talisman")

    output = capsys.readouterr().out
    warning = "Accessory data may be incomplete because inventory/accessory API data is missing or disabled."
    assert output.count(warning) == 1


def test_unverified_recipe_is_not_marked_craftable():
    db = load_accessory_database("data/accessories.json")
    profile = PlayerProfile("PalaMC", "id", 0, 0, collection_tiers={"POTATO_ITEM": 5}, inventory_api_enabled=True)

    analysis = analyze_accessories(profile, FakeBazaar({"POISONOUS_POTATO": 10, "ENCHANTED_POISONOUS_POTATO": 100}), FakeCofl(), database=db, filters=AccessoryFilters(include_ah=False))

    ring = next(row for row in analysis.all_missing if row.entry.item_id == "VACCINE_RING")
    assert ring.status == "Unknown recipe"
    assert not ring.craftable_now


def test_missing_inventory_warning_and_lower_confidence():
    db = load_accessory_database("data/accessories.json")
    profile = PlayerProfile("PalaMC", "id", 0, 0)
    analysis = analyze_accessories(profile, FakeBazaar(), FakeCofl(), database=db, filters=AccessoryFilters(include_ah=False))

    assert analysis.ownership.confidence < 1
    assert any("Accessory data may be incomplete" in warning for warning in analysis.summary.warnings)


def test_no_crash_when_ah_fails_or_requirements_uncertain():
    db = load_accessory_database("data/accessories.json")
    profile = PlayerProfile("PalaMC", "id", 0, 0, inventory_api_enabled=True)
    analysis = analyze_accessories(profile, FakeBazaar(), FakeCofl(fail=True), database=db, filters=AccessoryFilters(show_locked=True, hide_locked=False, include_uncertain=True))

    assert analysis.rows
    assert any(row.entry.uncertain_requirements for row in analysis.rows)


def test_database_file_can_be_updated(tmp_path):
    source = json.loads(Path("data/accessories.json").read_text(encoding="utf-8"))
    source["accessories"] = source["accessories"][:1]
    target = tmp_path / "accessories.json"
    target.write_text(json.dumps(source), encoding="utf-8")

    db = load_accessory_database(target)

    assert len(db.accessories) == 1
