from skyflip.bazaar import BazaarPrice
from skyflip.cofl import ActiveAuctions, MarketAnalysis, SoldSummary
from skyflip.pricing import PricingEngine
from skyflip.recipes import Ingredient, Recipe, Requirements
from skyflip.scoring import AnalyzerConfig, evaluate_opportunity


class FakeBazaar:
    warnings = []
    last_source = "fake"

    def __init__(self, prices):
        self.prices = prices
        self.calls = []

    def price_for(self, tag, *, use_buy_order_cost=False):
        self.calls.append((tag, use_buy_order_cost))
        value = self.prices.get(tag)
        if value is None:
            return None
        return BazaarPrice(tag=tag, unit_price=value, source_field="fake")


class FakeCofl:
    warnings = []

    def __init__(self, markets=None):
        self.markets = markets or {}

    def bazaar_snapshot_price(self, tag):
        return None

    def analysis(self, tag, days):
        return self.markets.get(tag, {}).get("analysis", MarketAnalysis(total_sales=50, sales_per_day=10, median_price=100_000))

    def active_bins(self, tag):
        return self.markets.get(tag, {}).get(
            "active",
            ActiveAuctions(prices=[100_000, 105_000, 110_000], active_count=3, lowest_bin=100_000, second_lowest_bin=105_000, third_lowest_bin=110_000),
        )

    def sold_summary(self, tag):
        return SoldSummary()

    def failure_status(self, tag):
        return self.markets.get(tag, {}).get("failure_status")


def test_craft_cost_calculates_bazaar_ingredients():
    recipe = Recipe(
        "OUT",
        "Output",
        1,
        None,
        True,
        [Ingredient("A", "A", 3, "bazaar"), Ingredient("B", "B", 2, "fixed_cost", 5)],
        Requirements(),
        [],
    )
    engine = PricingEngine({"OUT": recipe}, FakeBazaar({"A": 10}), FakeCofl())

    cost = engine.craft_cost(recipe)

    assert cost.total_cost == 40
    assert not cost.unavailable
    assert engine.bazaar.calls == [("A", True)]


def test_previous_recipe_uses_cheaper_auction_subitem():
    base = Recipe("BASE", "Base", 1, None, True, [Ingredient("A", "A", 10, "bazaar")], Requirements(), [])
    top = Recipe("TOP", "Top", 1, None, True, [Ingredient("BASE", "Base", 1, "previous_recipe")], Requirements(), [])
    cofl = FakeCofl({"BASE": {"active": ActiveAuctions([50, 55], 2, 50, 55, None), "analysis": MarketAnalysis(total_sales=50, sales_per_day=10, median_price=50)}})
    engine = PricingEngine({"BASE": base, "TOP": top}, FakeBazaar({"A": 10}), cofl)

    cost = engine.craft_cost(top)

    assert cost.total_cost == 50
    assert cost.ingredients[0].source == "ah-buy-subitem"


def test_previous_recipe_crafts_nested_subitem_when_auction_is_expensive():
    base = Recipe("BASE", "Base", 1, None, True, [Ingredient("A", "A", 10, "bazaar")], Requirements(), [])
    top = Recipe("TOP", "Top", 1, None, True, [Ingredient("BASE", "Base", 1, "previous_recipe")], Requirements(), [])
    cofl = FakeCofl({"BASE": {"active": ActiveAuctions([150, 155], 2, 150, 155, None), "analysis": MarketAnalysis(total_sales=50, sales_per_day=10, median_price=150)}})
    engine = PricingEngine({"BASE": base, "TOP": top}, FakeBazaar({"A": 10}), cofl)

    cost = engine.craft_cost(top)

    assert cost.total_cost == 100
    assert cost.ingredients[0].source == "nested-craft"


def test_explicit_craft_source_can_use_cheaper_auction_subitem():
    base = Recipe("BASE", "Base", 1, None, True, [Ingredient("A", "A", 10, "bazaar")], Requirements(), [])
    top = Recipe("TOP", "Top", 1, None, True, [Ingredient("BASE", "Base", 1, "craft")], Requirements(), [])
    cofl = FakeCofl({"BASE": {"active": ActiveAuctions([50, 55], 2, 50, 55, None), "analysis": MarketAnalysis(total_sales=50, sales_per_day=10, median_price=50)}})
    engine = PricingEngine({"BASE": base, "TOP": top}, FakeBazaar({"A": 10}), cofl)

    cost = engine.craft_cost(top)

    assert cost.total_cost == 50
    assert cost.ingredients[0].source == "ah-buy-subitem"


def test_market_metrics_apply_safe_price_and_volatility_penalty():
    cofl = FakeCofl(
        {
            "OUT": {
                "analysis": MarketAnalysis(total_sales=50, sales_per_day=10, median_price=100_000, coeff_variation=1.2),
                "active": ActiveAuctions([90_000, 95_000, 96_000], 3, 90_000, 95_000, 96_000),
            }
        }
    )
    engine = PricingEngine({}, FakeBazaar({}), cofl)

    market = engine.market_metrics("OUT")

    assert market.safe_sell_price == 90_000 * 0.99 * 0.75
    assert "large volatility penalty applied" in market.notes


def test_rate_limited_market_has_explicit_rejection_reason():
    recipe = Recipe("OUT", "Output", 1, None, True, [Ingredient("A", "A", 1, "bazaar")], Requirements(), [])
    cofl = FakeCofl({"OUT": {"analysis": None, "active": ActiveAuctions(source="rate_limited"), "failure_status": "rate_limited"}})
    engine = PricingEngine({"OUT": recipe}, FakeBazaar({"A": 10}), cofl)

    craft = engine.craft_cost(recipe)
    market = engine.market_metrics("OUT")
    opportunity = evaluate_opportunity(recipe, type("Eligibility", (), {"eligible": True, "missing": [], "reasons": [], "confidence": 1.0})(), craft, market, AnalyzerConfig(budget=1_000_000))

    assert market.status == "rate_limited"
    assert "market check skipped due to SkyCofl rate limit" in opportunity.rejection_reasons
