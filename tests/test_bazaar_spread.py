import json
import time
from types import SimpleNamespace

from skyflip.bazaar import BazaarProduct
from skyflip.bazaar_spread import (
    BazaarSpreadOpportunity,
    MAX_HISTORY_PRODUCTS,
    MAX_HISTORY_RECORDS_PER_PRODUCT,
    estimate_spread_side_speed,
    evaluate_bazaar_spread_product,
    get_bazaar_tick_size,
    load_spread_history,
    save_spread_history,
)
from skyflip.dashboard import hide_duplicate_bazaar_results
from skyflip.dashboard_menu import _draw_header
from skyflip.models import RejectedItem
from skyflip.profile_parser import PlayerProfile
from skyflip.scoring import AnalyzerConfig
from skyflip.terminal import print_dashboard


def config(**overrides):
    values = {
        "budget": 21_600_000,
        "min_profit": 1_000,
        "min_profit_percent": 1,
        "max_capital_percent_per_flip": 10,
        "limit": 10,
        "min_spread_volume_week": 1_000,
    }
    values.update(overrides)
    return AnalyzerConfig(**values)


def spread_product(
    *,
    tag="TEST",
    buy=100,
    sell=120,
    second_buy=99.9,
    second_sell=120.1,
    buy_depth=1_000,
    sell_depth=1_000,
    buy_moving_week=1_000_000,
    sell_moving_week=1_000_000,
):
    return BazaarProduct(
        tag=tag,
        buy_price=buy,
        sell_price=sell,
        buy_volume=100_000,
        sell_volume=100_000,
        buy_moving_week=buy_moving_week,
        sell_moving_week=sell_moving_week,
        best_buy_order=buy,
        best_sell_offer=sell,
        top_buy_order_depth=buy_depth,
        top_sell_offer_depth=sell_depth,
        buy_summary=(
            {"pricePerUnit": buy, "amount": buy_depth},
            {"pricePerUnit": second_buy, "amount": 2_000},
            {"pricePerUnit": second_buy - 0.1, "amount": 3_000},
        ),
        sell_summary=(
            {"pricePerUnit": sell, "amount": sell_depth},
            {"pricePerUnit": second_sell, "amount": 2_000},
            {"pricePerUnit": second_sell + 0.1, "amount": 3_000},
        ),
    )


def test_spread_calculation_uses_realistic_prices_and_fee():
    result = evaluate_bazaar_spread_product(spread_product(), config())

    assert isinstance(result, BazaarSpreadOpportunity)
    assert result.realistic_buy_price == 100.1
    assert result.realistic_sell_price == 119.9
    assert round(result.net_profit_per_unit, 4) == 18.3013
    assert round(result.profit_percent, 2) == 18.28
    assert result.estimated_total_profit > 0
    assert result.capital_required > 0
    assert result.profit_per_minute > 0
    assert result.coins_per_hour > 0


def test_spread_normalizes_hypixel_summary_orientation():
    product = spread_product(buy=120, second_buy=120.1, sell=100, second_sell=99.9)
    result = evaluate_bazaar_spread_product(product, config(min_profit=100))

    assert isinstance(result, BazaarSpreadOpportunity)
    assert result.best_buy_order_price == 100
    assert result.best_sell_offer_price == 120
    assert result.net_profit_per_unit > 0


def test_bazaar_tick_size_steps():
    assert get_bazaar_tick_size(0.5) == 0.0001
    assert get_bazaar_tick_size(5) == 0.001
    assert get_bazaar_tick_size(50) == 0.01
    assert get_bazaar_tick_size(500) == 0.1
    assert get_bazaar_tick_size(5_000) == 1
    assert get_bazaar_tick_size(50_000) == 10


def test_buy_and_sell_speed_estimates_and_bottleneck():
    fast_buy = estimate_spread_side_speed(moving_week=1_000_000, live_volume=100_000, top_depth=1_000, depth_at_price=0, side="buy")
    slow_sell = estimate_spread_side_speed(moving_week=5_000, live_volume=100, top_depth=10_000, depth_at_price=10_000, side="sell")
    result = evaluate_bazaar_spread_product(
        spread_product(buy_moving_week=500_000, sell_moving_week=1_000_000, sell_depth=3_000),
        config(min_profit=10, max_spread_depth_ratio=20, max_estimated_bottleneck_minutes=2000),
    )

    assert fast_buy.speed_score > slow_sell.speed_score
    assert isinstance(result, BazaarSpreadOpportunity)
    assert result.estimated_sell_fill_speed.speed_score < result.estimated_buy_fill_speed.speed_score
    assert result.bottleneck_speed.speed_score == result.estimated_sell_fill_speed.speed_score


def test_slow_sell_side_is_rejected_for_fast_spread_flips():
    result = evaluate_bazaar_spread_product(
        spread_product(buy_moving_week=500_000, sell_moving_week=1_000_000, sell_depth=10_000),
        config(min_profit=10, max_spread_depth_ratio=20, max_estimated_bottleneck_minutes=2000),
    )

    assert isinstance(result, RejectedItem)
    assert "sell side is too slow" in result.reason


def test_slow_buy_side_is_rejected_for_fast_spread_flips():
    result = evaluate_bazaar_spread_product(
        spread_product(buy_moving_week=1_000_000, sell_moving_week=500_000, buy_depth=30_000),
        config(min_profit=10, max_spread_depth_ratio=20, max_estimated_bottleneck_minutes=2000),
    )

    assert isinstance(result, RejectedItem)
    assert "buy side is too slow" in result.reason


def test_total_profit_floor_applies_before_hourly_ranking():
    result = evaluate_bazaar_spread_product(
        spread_product(buy=100, sell=101, second_buy=99.9, second_sell=101.1),
        config(min_profit=10_000, min_profit_percent=0.1),
    )

    assert isinstance(result, RejectedItem)


def test_huge_order_wall_rejection():
    result = evaluate_bazaar_spread_product(spread_product(buy_depth=50_000, sell_depth=50_000, buy_moving_week=100_000, sell_moving_week=100_000), config())

    assert isinstance(result, RejectedItem)
    assert "top order depth is huge" in result.reason


def test_outlier_spread_rejection():
    result = evaluate_bazaar_spread_product(
        spread_product(buy=100, second_buy=50, sell=120, second_sell=300),
        config(min_profit=10),
    )

    assert isinstance(result, RejectedItem)
    assert "suspicious outlier" in result.reason


def test_suggested_order_size_respects_safe_budget_fraction():
    result = evaluate_bazaar_spread_product(spread_product(buy=10_000, second_buy=9_990, sell=12_000, second_sell=12_010), config())

    assert isinstance(result, BazaarSpreadOpportunity)
    assert result.realistic_buy_price * result.suggested_order_size <= 2_160_000


def test_duplicate_hiding_keeps_higher_scoring_section():
    spread = SimpleNamespace(product_id="A", final_score=80)
    order = SimpleNamespace(product_id="A", score=70)
    spreads, orders, hidden = hide_duplicate_bazaar_results([spread], [order])

    assert spreads == [spread]
    assert orders == []
    assert hidden[0].section == "bazaar-order"


def test_dashboard_renders_bazaar_spread_section(capsys):
    profile = PlayerProfile("PalaMC", "id", 1_000_000, 9_000_000)
    result = evaluate_bazaar_spread_product(spread_product(), config())
    assert isinstance(result, BazaarSpreadOpportunity)

    print_dashboard(
        profile=profile,
        budget=21_600_000,
        craft=[],
        bazaar_spreads=[result],
        bazaar_orders=[],
        conversions=[],
        ah_underpriced=[],
        rejected=[],
        warnings=[],
        show_rejected=False,
        cache_ttl=300,
    )

    output = capsys.readouterr().out
    assert "Best Bazaar Spread Flips" in output
    assert "TEST x" in output
    assert "Coins/h" in output
    assert "Profit %" in output
    assert "Profit/min" in output
    assert "Min" not in output
    assert "Details" not in output
    assert "Action:" not in output
    assert "Why:" not in output


def test_huge_spread_is_not_low_risk_and_uses_test_first():
    result = evaluate_bazaar_spread_product(
        spread_product(
            buy=100,
            sell=230,
            second_buy=99.9,
            second_sell=230.1,
            buy_depth=10_000,
            sell_depth=10_000,
            buy_moving_week=50_000_000,
            sell_moving_week=50_000_000,
        ),
        config(min_profit=100, min_profit_percent=1),
    )

    assert isinstance(result, BazaarSpreadOpportunity)
    assert result.profit_percent >= 100
    assert result.risk == "High"
    assert result.should_test_first is True
    assert result.suggested_test_size < result.suggested_full_size
    assert "Test first: place buy order" in result.manual_action
    assert "extreme spread" in result.reason
    assert "%" in result.reason
    assert "buy ~" in result.reason
    assert "sell ~" in result.reason


def test_shard_high_spread_is_not_low_risk():
    result = evaluate_bazaar_spread_product(
        spread_product(
            tag="SHARD_CHILL",
            buy=2_200,
            sell=5_100,
            second_buy=2_199,
            second_sell=5_110,
            buy_depth=10_000,
            sell_depth=10_000,
            buy_moving_week=50_000_000,
            sell_moving_week=50_000_000,
        ),
        config(min_profit=1_000, min_profit_percent=1, budget=100_000_000),
    )

    assert isinstance(result, BazaarSpreadOpportunity)
    assert result.risk in {"Medium", "High"}
    assert result.should_test_first is True
    assert "shard item with large spread" in result.reason


def test_low_risk_safe_spread_uses_full_size_action():
    result = evaluate_bazaar_spread_product(
        spread_product(
            buy=100,
            sell=108,
            second_buy=99.9,
            second_sell=108.1,
            buy_depth=10_000,
            sell_depth=10_000,
            buy_moving_week=50_000_000,
            sell_moving_week=50_000_000,
        ),
        config(min_profit=100, min_profit_percent=1),
    )

    assert isinstance(result, BazaarSpreadOpportunity)
    assert result.risk == "Low"
    assert result.should_test_first is False
    assert result.suggested_test_size == result.suggested_full_size
    assert result.manual_action.startswith("Place buy order for")


def test_menu_header_uses_loaded_profile_name(capsys):
    args = SimpleNamespace(profile_file=None, player_name="PalaMC", budget=1_000_000, active_settings_profile=None)
    state = SimpleNamespace(
        latest=SimpleNamespace(profile=PlayerProfile("PalaMC", "id", 1, 2, profile_name="Zucchini", profile_source="api")),
        last_refresh="2026-06-18 21:00:00",
        auto_refresh=False,
        status_message=None,
    )

    _draw_header("Test", args, state)

    output = capsys.readouterr().out
    assert "Profile: Zucchini" in output
    assert "Profile: not set" not in output


def test_spread_history_round_trip(tmp_path):
    path = tmp_path / "spread_history.json"
    result = evaluate_bazaar_spread_product(spread_product(), config())
    assert isinstance(result, BazaarSpreadOpportunity)

    save_spread_history({}, [result], path)
    loaded = load_spread_history(path)

    assert list(loaded) == ["TEST"]
    assert loaded["TEST"][0]["spread"] == result.net_profit_per_unit


def test_spread_history_cache_is_rotated(tmp_path):
    path = tmp_path / "spread_history.json"
    now = time.time()
    raw = {
        f"ITEM_{index:03d}": [
            {"ts": now - record_index, "buy": 100, "sell": 120, "spread": 10, "coins_per_hour": 1000}
            for record_index in range(MAX_HISTORY_RECORDS_PER_PRODUCT + 10)
        ]
        for index in range(MAX_HISTORY_PRODUCTS + 10)
    }
    path.write_text(json.dumps(raw), encoding="utf-8")

    loaded = load_spread_history(path)

    assert len(loaded) == MAX_HISTORY_PRODUCTS
    assert all(len(records) == MAX_HISTORY_RECORDS_PER_PRODUCT for records in loaded.values())
