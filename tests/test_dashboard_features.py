import json
import time

from skyflip.ah_underpriced import WatchItem, evaluate_watch_item
from skyflip.bazaar import BazaarPrice, BazaarProduct
from skyflip.bazaar_compression import BazaarConversion, evaluate_conversion
from skyflip.bazaar_order import evaluate_bazaar_order_product
from skyflip.cache import FileCache
from skyflip.cofl import ActiveAuctions, MarketAnalysis, SoldSummary
from skyflip.market_speed import normalize_ah_speed, normalize_bazaar_speed
from skyflip.models import RejectedItem
from skyflip.profile_parser import PlayerProfile
from skyflip.scoring import AnalyzerConfig, score_generic_opportunity
from skyflip.terminal import print_dashboard
from skyflip.dashboard import analyze_craft_section, collect_dashboard_data
from skyflip.user_config import BUDGET_SOURCE_CUSTOM, HypixelUserConfig, save_user_config


def config(**overrides):
    values = {
        "budget": 10_000_000,
        "min_profit": 1_000,
        "min_profit_percent": 1,
        "min_sales_per_day": 2,
        "max_median_sell_time_hours": 12,
        "max_capital_percent_per_flip": 10,
        "limit": 10,
    }
    values.update(overrides)
    return AnalyzerConfig(**values)


def bazaar_product(tag="TEST", buy=100, sell=120, moving=1_000_000, depth=1_000):
    return BazaarProduct(
        tag=tag,
        buy_price=buy,
        sell_price=sell,
        buy_volume=100_000,
        sell_volume=100_000,
        buy_moving_week=moving,
        sell_moving_week=moving,
        best_buy_order=buy,
        best_sell_offer=sell,
        top_buy_order_depth=depth,
        top_sell_offer_depth=depth,
    )


class FakeBazaar:
    def __init__(self, products):
        self.products_by_tag = products

    def product_metrics(self, tag):
        return self.products_by_tag.get(tag)


class FakeCofl:
    def active_bins(self, tag):
        return ActiveAuctions([70_000, 98_000, 100_000], 8, 70_000, 98_000, 100_000)

    def analysis(self, tag, days):
        return MarketAnalysis(total_sales=80, sales_per_day=12, median_price=100_000, average_price=102_000, median_sell_time_hours=2, coeff_variation=0.1)

    def sold_summary(self, tag):
        return SoldSummary()


def test_bazaar_order_flip_math_and_budget_cap():
    result = evaluate_bazaar_order_product(bazaar_product(), config())

    assert not isinstance(result, RejectedItem)
    assert result.net_profit_per_unit > 17
    assert result.buy_order_price * result.suggested_order_size <= 1_000_000
    assert "Suggested manual action" in result.manual_action


def test_bazaar_order_normalizes_hypixel_summary_orientation():
    product = bazaar_product(buy=120, sell=100)
    product = BazaarProduct(
        tag=product.tag,
        buy_price=product.buy_price,
        sell_price=product.sell_price,
        buy_volume=product.buy_volume,
        sell_volume=product.sell_volume,
        buy_moving_week=product.buy_moving_week,
        sell_moving_week=product.sell_moving_week,
        best_buy_order=120,
        best_sell_offer=100,
        top_buy_order_depth=product.top_buy_order_depth,
        top_sell_offer_depth=product.top_sell_offer_depth,
        buy_summary=({"pricePerUnit": 120, "amount": 1_000}, {"pricePerUnit": 121, "amount": 1_000}),
        sell_summary=({"pricePerUnit": 100, "amount": 1_000}, {"pricePerUnit": 99, "amount": 1_000}),
    )

    result = evaluate_bazaar_order_product(product, config(min_profit=100))

    assert not isinstance(result, RejectedItem)
    assert result.buy_order_price < result.sell_order_price


def test_bazaar_order_rejects_slow_huge_depth():
    result = evaluate_bazaar_order_product(bazaar_product(moving=1_000, depth=20_000), config())

    assert isinstance(result, RejectedItem)
    assert "moving week volume is too low" in result.reason or "estimated fill time too long" in result.reason


def test_conversion_rejects_low_output_volume():
    conversion = BazaarConversion("A to B", "A", 160, "B", 1, "compression", True)
    bazaar = FakeBazaar({"A": bazaar_product("A", moving=1_000_000), "B": bazaar_product("B", buy=20_000, sell=30_000, moving=100)})

    result = evaluate_conversion(conversion, bazaar, config())

    assert isinstance(result, RejectedItem)
    assert "volume too low" in result.reason or "output sale speed is bad" in result.reason


def test_ah_underpriced_detection_requires_speed_and_confidence():
    profile = PlayerProfile("PalaMC", "id", 1_000_000, 9_000_000)
    item = WatchItem("TEST", "Test Item", "weapon", max_budget_percent=10)

    result = evaluate_watch_item(item, FakeCofl(), profile, config(), days=7)

    assert not isinstance(result, RejectedItem)
    assert result.expected_profit > 0
    assert result.underpriced_percent >= 30
    assert "Check AH manually" in result.manual_action


def test_market_speed_labels_fast_and_slow_markets():
    fast = normalize_ah_speed(sales_per_day=40, median_sell_time_hours=1, sold_sample_count=100, active_bin_count=20)
    slow = normalize_bazaar_speed(buy_moving_week=100, sell_moving_week=100, buy_volume=10, sell_volume=10, top_order_depth=10_000)

    assert fast.speed_score > slow.speed_score
    assert slow.risk_label == "Too slow"


def test_generic_scoring_prioritizes_speed():
    fast = score_generic_opportunity(profit=10_000, profit_percent=5, speed_score=95, confidence_score=90, budget_fit_score=90, competition_score=90)
    slow = score_generic_opportunity(profit=50_000, profit_percent=10, speed_score=5, confidence_score=30, budget_fit_score=90, competition_score=90)

    assert fast > slow


def test_terminal_dashboard_smoke(capsys):
    profile = PlayerProfile("PalaMC", "id", 1_000_000, 9_000_000)

    print_dashboard(
        profile=profile,
        budget=10_000_000,
        craft=[],
        bazaar_spreads=[],
        bazaar_orders=[],
        conversions=[],
        ah_underpriced=[],
        rejected=[RejectedItem("craft", "Thing", "reason")],
        warnings=["warn"],
        show_rejected=True,
        cache_ttl=300,
    )

    output = capsys.readouterr().out
    assert "A. Player summary" in output
    assert "Manual-only tool" in output
    assert "Thing" in output


def test_terminal_bazaar_order_shows_size_next_to_product(capsys):
    profile = PlayerProfile("PalaMC", "id", 1_000_000, 9_000_000)
    order = evaluate_bazaar_order_product(bazaar_product(), config())
    assert not isinstance(order, RejectedItem)

    print_dashboard(
        profile=profile,
        budget=10_000_000,
        craft=[],
        bazaar_spreads=[],
        bazaar_orders=[order],
        conversions=[],
        ah_underpriced=[],
        rejected=[],
        warnings=[],
        show_rejected=False,
        cache_ttl=300,
    )

    output = capsys.readouterr().out
    assert f"TEST x{order.suggested_order_size}" in output


def test_file_cache_ttl_expires(tmp_path):
    cache = FileCache(tmp_path, ttl_seconds=1)
    cache.set("key", {"value": 1})
    assert cache.get("key") is not None
    path = next(tmp_path.glob("*.json"))
    old = time.time() - 10
    path.write_text(path.read_text().replace(str(cache.get("key").created_at), str(old)), encoding="utf-8")

    assert cache.get("key") is None


def test_dashboard_uses_api_profile_by_default(monkeypatch):
    profile = PlayerProfile("PalaMC", "id", 1_000_000, 9_000_000)
    calls = {"ensure": 0, "load": 0}

    def fake_ensure(http, force_setup=False):
        calls["ensure"] += 1

    def fake_load(http, force_refresh=False, ttl_seconds=600):
        calls["load"] += 1
        return type("Loaded", (), {"profile": profile})()

    monkeypatch.setattr("skyflip.dashboard.ensure_profile_configuration", fake_ensure)
    monkeypatch.setattr("skyflip.dashboard.load_api_profile", fake_load)
    args = type(
        "Args",
        (),
        {
            "profile_file": None,
            "player_name": None,
            "budget": None,
            "cache_ttl": 300,
            "min_profit": 1_000,
            "min_profit_percent": 1,
            "min_sales_per_day": 2,
            "max_median_sell_time_hours": 12,
            "max_craft_cost": None,
            "max_capital_percent_per_flip": 10,
            "limit_per_section": 0,
            "min_spread_profit_per_unit": 0,
            "min_spread_volume_week": 1_000,
            "max_spread_depth_ratio": 1.25,
            "spread_limit": 0,
            "sections": "none",
            "allow_restricted_profile": False,
            "show_rejected": False,
            "profile_cache_ttl": 600,
        },
    )()

    data = collect_dashboard_data(args, resolve_uuid=lambda http, name: None)

    assert data.profile is profile
    assert args.budget == 10_000_000
    assert calls == {"ensure": 1, "load": 1}


def test_dashboard_uses_configured_budget_source(monkeypatch, tmp_path):
    monkeypatch.setenv("SKYFLIP_CONFIG_DIR", str(tmp_path))
    save_user_config(HypixelUserConfig("PalaMC", "id", "Apple", "one", BUDGET_SOURCE_CUSTOM, 2_500_000))
    profile = PlayerProfile("PalaMC", "id", 1_000_000, 9_000_000)

    monkeypatch.setattr("skyflip.dashboard.ensure_profile_configuration", lambda http, force_setup=False: None)
    monkeypatch.setattr("skyflip.dashboard.load_api_profile", lambda http, force_refresh=False, ttl_seconds=600: type("Loaded", (), {"profile": profile})())
    args = type(
        "Args",
        (),
        {
            "profile_file": None,
            "player_name": None,
            "budget": None,
            "cache_ttl": 300,
            "min_profit": 1_000,
            "min_profit_percent": 1,
            "min_sales_per_day": 2,
            "max_median_sell_time_hours": 12,
            "max_craft_cost": None,
            "max_capital_percent_per_flip": 10,
            "limit_per_section": 0,
            "min_spread_profit_per_unit": 0,
            "min_spread_volume_week": 1_000,
            "max_spread_depth_ratio": 1.25,
            "spread_limit": 0,
            "sections": "none",
            "allow_restricted_profile": False,
            "show_rejected": False,
            "profile_cache_ttl": 600,
        },
    )()

    data = collect_dashboard_data(args, resolve_uuid=lambda http, name: None)

    assert data.budget == 2_500_000
    assert args.budget == 2_500_000


def test_craft_section_keeps_wand_and_excludes_event_recipe():
    class FakeBazaar:
        warnings = []

        def price_for(self, tag, *, use_buy_order_cost=False):
            return BazaarPrice(tag, 100.0, "fake")

    class FakeCofl:
        warnings = []

        def analysis(self, tag, days):
            price = 250_000 if tag == "WAND_OF_MENDING" else 200_000
            return MarketAnalysis(total_sales=120, sales_per_day=20, median_sell_time_hours=2, median_price=price)

        def active_bins(self, tag):
            price = 250_000 if tag == "WAND_OF_MENDING" else 200_000
            return ActiveAuctions([price, price * 1.01, price * 1.02], 3, price, price * 1.01, None)

        def sold_summary(self, tag):
            return SoldSummary()

        def bazaar_snapshot_price(self, tag):
            return 100.0

    args = type(
        "Args",
        (),
        {
            "recipes_file": "data/craft_recipes.json",
            "use_buy_order_cost": False,
            "days": 7,
        },
    )()
    profile = PlayerProfile(
        "PalaMC",
        "id",
        1_000_000,
        9_000_000,
        slayer_levels={"zombie": 3},
        collection_tiers={"BONE": 9},
    )

    recommended, rejected = analyze_craft_section(
        args,
        FakeBazaar(),
        FakeCofl(),
        profile,
        config(limit=50, min_profit=1_000, min_profit_percent=1, min_sales_per_day=1),
    )

    assert "WAND_OF_MENDING" in {item.recipe.tag for item in recommended}
    assert "INTIMIDATION_RING" not in {item.recipe.tag for item in recommended}
    ring_rejection = next(item for item in rejected if item.item == "Intimidation Ring")
    assert "event-limited craft" in ring_rejection.reason


def test_craft_section_can_use_auctioned_previous_wand_tier():
    class FakeBazaar:
        warnings = []

        def price_for(self, tag, *, use_buy_order_cost=False):
            prices = {
                "REVENANT_FLESH": 4_000,
                "REVENANT_VISCERA": 8_000,
                "ENCHANTED_DARK_OAK_LOG": 12_000,
            }
            return BazaarPrice(tag, prices.get(tag, 100), "fake")

    class FakeCofl:
        warnings = []

        def analysis(self, tag, days):
            price = 389_999 if tag == "WAND_OF_MENDING" else 40_000
            return MarketAnalysis(total_sales=80, sales_per_day=15, median_sell_time_hours=2, median_price=price)

        def active_bins(self, tag):
            price = 389_999 if tag == "WAND_OF_MENDING" else 40_000
            return ActiveAuctions([price, price * 1.01, price * 1.02], 3, price, price * 1.01, None)

        def sold_summary(self, tag):
            return SoldSummary()

        def bazaar_snapshot_price(self, tag):
            return None

    args = type("Args", (), {"recipes_file": "data/craft_recipes.json", "use_buy_order_cost": False, "days": 7})()
    profile = PlayerProfile("PalaMC", "id", 1_000_000, 9_000_000, slayer_levels={"zombie": 3})

    recommended, _rejected = analyze_craft_section(
        args,
        FakeBazaar(),
        FakeCofl(),
        profile,
        config(limit=50, min_profit=5_000, min_profit_percent=2, min_sales_per_day=1),
    )

    mending = next(item for item in recommended if item.recipe.tag == "WAND_OF_MENDING")
    previous = next(ingredient for ingredient in mending.craft_cost.ingredients if ingredient.tag == "WAND_OF_HEALING")
    assert previous.source == "ah-buy-subitem"


def test_craft_section_skips_non_auctionable_recipe_market_calls(tmp_path):
    recipe_file = tmp_path / "recipes.json"
    recipe_file.write_text(
        json.dumps(
            {
                "recipes": [
                    {
                        "output": {
                            "tag": "NO_MARKET",
                            "display_name": "No Market Item",
                            "quantity": 1,
                            "auctionable": False,
                        },
                        "ingredients": [{"item_tag": "A", "display_name": "A", "amount": 1, "source": "bazaar"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    class FakeBazaar:
        warnings = []

        def price_for(self, tag, *, use_buy_order_cost=False):
            raise AssertionError("non-auctionable recipes should not be priced")

    class FakeCofl:
        warnings = []

        def analysis(self, tag, days):
            raise AssertionError("non-auctionable recipes should not query market data")

    args = type("Args", (), {"recipes_file": str(recipe_file), "use_buy_order_cost": False, "days": 7})()

    recommended, rejected = analyze_craft_section(
        args,
        FakeBazaar(),
        FakeCofl(),
        PlayerProfile("PalaMC", "id", 1, 2),
        config(),
    )

    assert recommended == []
    assert rejected[0].item == "No Market Item"
    assert "not auctionable" in rejected[0].reason
