from skyflip.cofl import ActiveAuctions, MarketAnalysis, SoldSummary
from skyflip.pricing import CraftCost, MarketMetrics
from skyflip.report import format_plain_table, short_coins
from skyflip.recipes import Eligibility, Recipe, Requirements
from skyflip.scoring import AnalyzerConfig, evaluate_opportunity


def make_market(**overrides):
    analysis = overrides.pop("analysis", MarketAnalysis(total_sales=100, sales_per_day=20, median_sell_time_hours=2, median_price=200_000))
    active = overrides.pop("active", ActiveAuctions([200_000, 205_000], 2, 200_000, 205_000, None))
    return MarketMetrics(
        safe_sell_price=overrides.pop("safe_sell_price", 200_000),
        suggested_listing_price=199_999,
        analysis=analysis,
        active=active,
        sold=SoldSummary(),
        median_sold_price=200_000,
        volatility=0.1,
        price_wall_score=0.1,
        manipulation_risk_score=0.0,
        confidence_score=0.9,
    )


def test_profitable_fast_item_is_recommended():
    recipe = Recipe("OUT", "Output", 1, None, [], Requirements(), [])
    craft = CraftCost("OUT", "Output", 100_000, 100_000, [])
    eligibility = Eligibility(True, 1.0, ["combat ok"], [])

    result = evaluate_opportunity(recipe, eligibility, craft, make_market(), AnalyzerConfig(budget=1_000_000))

    assert not result.rejected
    assert result.estimated_profit == 96_000
    assert result.max_batch_size >= 1


def test_low_sales_item_is_rejected():
    recipe = Recipe("OUT", "Output", 1, None, [], Requirements(), [])
    craft = CraftCost("OUT", "Output", 100_000, 100_000, [])
    eligibility = Eligibility(True, 1.0, ["ok"], [])
    market = make_market(analysis=MarketAnalysis(total_sales=3, sales_per_day=0.5, median_sell_time_hours=1, median_price=200_000))

    result = evaluate_opportunity(recipe, eligibility, craft, market, AnalyzerConfig(budget=1_000_000))

    assert result.rejected
    assert "sales/day below 2" in result.rejection_reasons


def test_anomalous_volatile_profit_is_rejected():
    recipe = Recipe("OUT", "Output", 1, None, [], Requirements(), [])
    craft = CraftCost("OUT", "Output", 10_000, 10_000, [])
    eligibility = Eligibility(True, 1.0, ["ok"], [])
    market = make_market(
        safe_sell_price=1_000_000,
        analysis=MarketAnalysis(total_sales=50, sales_per_day=8, median_sell_time_hours=2, median_price=1_000_000),
    )
    market = MarketMetrics(
        safe_sell_price=market.safe_sell_price,
        suggested_listing_price=market.suggested_listing_price,
        analysis=market.analysis,
        active=ActiveAuctions([1_000_000] * 8, 8, 1_000_000, 1_000_000, 1_000_000),
        sold=market.sold,
        median_sold_price=market.median_sold_price,
        volatility=0.7,
        price_wall_score=market.price_wall_score,
        manipulation_risk_score=0.35,
        confidence_score=market.confidence_score,
    )

    result = evaluate_opportunity(recipe, eligibility, craft, market, AnalyzerConfig(budget=1_000_000))

    assert result.rejected
    assert "profit is anomalous for a volatile market" in result.rejection_reasons


def test_plain_text_table_format_helpers():
    lines = format_plain_table(
        ["#", "Item", "Profit", "Sales/day", "Sell time", "Risk", "Batch"],
        [["1", "Wand of Healing", short_coins(42_000), "18.4", "1.2h", "Low", "5"]],
    )

    assert lines[0].startswith("#  Item")
    assert lines[1] == "1  Wand of Healing  42k     18.4       1.2h       Low   5"
