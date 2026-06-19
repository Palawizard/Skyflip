from __future__ import annotations


from .bazaar import BazaarProduct
from .market_speed import SpeedResult, estimate_bazaar_order_fill_speed
from .scoring import AnalyzerConfig
from .bazaar_spread_models import (
    BAZAAR_FEE_RATE,
    BazaarOrderBookStats,
    BazaarSpreadPricing,
    _BookLevel,
)


def get_bazaar_tick_size(price: float) -> float:
    if price < 1:
        return 0.0001
    if price < 10:
        return 0.001
    if price < 100:
        return 0.01
    if price < 1_000:
        return 0.1
    if price < 10_000:
        return 1.0
    return 10.0

def _rough_profit_per_unit(buy_price: float, sell_price: float, config: AnalyzerConfig) -> float:
    if buy_price <= 0 or sell_price <= 0:
        return 0.0
    realistic_buy = buy_price + get_bazaar_tick_size(buy_price)
    realistic_sell = max(0.0, sell_price - get_bazaar_tick_size(sell_price))
    fee_rate = float(getattr(config, "bazaar_fee_rate", BAZAAR_FEE_RATE))
    return realistic_sell * (1 - fee_rate) - realistic_buy


def estimate_spread_side_speed(
    *,
    moving_week: float,
    live_volume: float,
    top_depth: float,
    depth_at_price: float,
    side: str,
    order_summary: tuple[dict[str, float], ...] = (),
    recommended_price: float = 0.0,
    order_size: float = 0.0,
) -> SpeedResult:
    blocking_depth = max(0.0, max(top_depth, depth_at_price))
    return estimate_bazaar_order_fill_speed(
        side=side,
        recommended_price=recommended_price,
        order_summary=order_summary,
        moving_week=moving_week,
        live_volume=live_volume,
        depth_ahead=blocking_depth,
        order_size=order_size,
    )


def suggest_spread_order_size(
    product: BazaarProduct,
    pricing: BazaarSpreadPricing,
    order_book: BazaarOrderBookStats,
    config: AnalyzerConfig,
) -> int:
    if pricing.realistic_buy_price <= 0 or pricing.net_profit_per_unit <= 0:
        return 0
    safe_capital = _safe_spread_capital(config)
    speed_factor = max(0.0, order_book.bottleneck_speed.speed_score / 100)
    confidence_factor = max(0.0, order_book.confidence_score / 100)
    quality_factor = 0.25 + 0.75 * min(speed_factor, confidence_factor)
    capital_cap = int((safe_capital * quality_factor) // pricing.realistic_buy_price)
    bottleneck_daily = min(product.buy_moving_week, product.sell_moving_week) / 7
    volume_cap = max(1, int(bottleneck_daily * (0.004 + 0.026 * speed_factor * confidence_factor)))
    visible_depth = min(order_book.buy_order_depth, order_book.sell_offer_depth)
    depth_cap = max(1, int(max(visible_depth, 1.0) * (0.15 + 0.35 * speed_factor)))
    profit_cap = max(1, int(max(config.min_profit, 1.0) * 4 / pricing.net_profit_per_unit))
    return max(0, min(capital_cap, volume_cap, depth_cap, profit_cap))


def score_bazaar_spread(
    *,
    estimated_total_profit: float,
    profit_percent: float,
    profit_per_minute: float,
    coins_per_hour: float,
    bottleneck_speed_score: float,
    confidence_score: float,
    competition_score: float,
    history_stability_score: float,
    penalty: float,
    config: AnalyzerConfig,
) -> float:
    hourly_target = max(config.min_profit * 12, 250_000.0)
    minute_target = max(hourly_target / 60, 1.0)
    hourly_score = min(100.0, max(0.0, coins_per_hour / hourly_target * 100))
    minute_score = min(100.0, max(0.0, profit_per_minute / minute_target * 100))
    profit_score = min(100.0, max(0.0, estimated_total_profit / max(config.min_profit * 4, 100_000) * 100))
    percent_score = min(100.0, max(0.0, profit_percent / max(config.min_profit_percent * 3, 30) * 100))
    score = (
        hourly_score * 0.35
        + minute_score * 0.18
        + profit_score * 0.10
        + percent_score * 0.10
        + max(0.0, min(100.0, bottleneck_speed_score)) * 0.15
        + max(0.0, min(100.0, confidence_score)) * 0.06
        + max(0.0, min(100.0, competition_score)) * 0.04
        + max(0.0, min(1.0, history_stability_score)) * 2.0
        - penalty
    )
    return max(0.0, min(100.0, score))


def _choose_pricing(
    product: BazaarProduct,
    buy_orders: list[_BookLevel],
    sell_offers: list[_BookLevel],
    config: AnalyzerConfig,
) -> tuple[BazaarSpreadPricing, float, list[str]]:
    warnings: list[str] = []
    best_buy = buy_orders[0].price
    second_buy = buy_orders[1].price if len(buy_orders) > 1 else None
    best_sell = sell_offers[0].price
    second_sell = sell_offers[1].price if len(sell_offers) > 1 else None
    manipulation_risk = 0.0

    buy_base = best_buy
    if second_buy is not None and _is_high_outlier(best_buy, second_buy):
        buy_base = second_buy
        manipulation_risk += 45.0
        warnings.append("top buy order looks like an outlier")
    sell_base = best_sell
    if second_sell is not None and _is_low_outlier(best_sell, second_sell):
        sell_base = second_sell
        manipulation_risk += 45.0
        warnings.append("lowest sell offer looks like an outlier")

    realistic_buy = buy_base + get_bazaar_tick_size(buy_base)
    realistic_sell = max(0.0, sell_base - get_bazaar_tick_size(sell_base))
    raw_spread = realistic_sell - realistic_buy
    fee_rate = float(getattr(config, "bazaar_fee_rate", BAZAAR_FEE_RATE))
    net_sell_price = realistic_sell * (1 - fee_rate)
    net_profit = net_sell_price - realistic_buy
    profit_percent = net_profit / realistic_buy * 100 if realistic_buy > 0 else 0.0
    price_ratio = realistic_sell / realistic_buy if realistic_buy > 0 else 0.0
    if profit_percent >= 100 or price_ratio >= 2.0:
        manipulation_risk += 30.0
        warnings.append("spread is extremely wide")
    elif profit_percent >= 40 or price_ratio >= 1.5:
        manipulation_risk += 12.0
        warnings.append("spread is unusually wide")
    if product.sell_price > 0 and product.buy_price > 0 and product.sell_price > product.buy_price * 3:
        manipulation_risk += 25.0
        warnings.append("quick_status spread is unusually wide")

    return (
        BazaarSpreadPricing(
            best_buy_order_price=best_buy,
            second_best_buy_order_price=second_buy,
            best_sell_offer_price=best_sell,
            second_best_sell_offer_price=second_sell,
            realistic_buy_price=realistic_buy,
            realistic_sell_price=realistic_sell,
            raw_spread=raw_spread,
            fee_rate=fee_rate,
            net_sell_price=net_sell_price,
            net_profit_per_unit=net_profit,
            profit_percent=profit_percent,
        ),
        min(100.0, manipulation_risk),
        warnings,
    )


def _choose_pricing_for_size(
    product: BazaarProduct,
    buy_orders: list[_BookLevel],
    sell_offers: list[_BookLevel],
    size: int,
    config: AnalyzerConfig,
) -> tuple[BazaarSpreadPricing, float, list[str]]:
    warnings: list[str] = []
    best_buy = buy_orders[0].price
    second_buy = buy_orders[1].price if len(buy_orders) > 1 else None
    best_sell = sell_offers[0].price
    second_sell = sell_offers[1].price if len(sell_offers) > 1 else None
    manipulation_risk = 0.0

    buy_base, buy_note = _durable_buy_base(buy_orders, size)
    sell_base, sell_note = _durable_sell_base(sell_offers, size)
    if buy_note:
        manipulation_risk += 18.0
        warnings.append(buy_note)
    if sell_note:
        manipulation_risk += 18.0
        warnings.append(sell_note)

    if second_buy is not None and _is_high_outlier(best_buy, second_buy):
        buy_base = min(buy_base, second_buy)
        manipulation_risk += 45.0
        warnings.append("top buy order looks like an outlier")
    if second_sell is not None and _is_low_outlier(best_sell, second_sell):
        sell_base = max(sell_base, second_sell)
        manipulation_risk += 45.0
        warnings.append("lowest sell offer looks like an outlier")

    realistic_buy = buy_base + get_bazaar_tick_size(buy_base)
    realistic_sell = max(0.0, sell_base - get_bazaar_tick_size(sell_base))
    raw_spread = realistic_sell - realistic_buy
    fee_rate = float(getattr(config, "bazaar_fee_rate", BAZAAR_FEE_RATE))
    net_sell_price = realistic_sell * (1 - fee_rate)
    net_profit = net_sell_price - realistic_buy
    profit_percent = net_profit / realistic_buy * 100 if realistic_buy > 0 else 0.0
    price_ratio = realistic_sell / realistic_buy if realistic_buy > 0 else 0.0
    if profit_percent >= 100 or price_ratio >= 2.0:
        manipulation_risk += 30.0
        warnings.append("spread is extremely wide")
    elif profit_percent >= 40 or price_ratio >= 1.5:
        manipulation_risk += 12.0
        warnings.append("spread is unusually wide")
    if product.sell_price > 0 and product.buy_price > 0 and product.sell_price > product.buy_price * 3:
        manipulation_risk += 25.0
        warnings.append("quick_status spread is unusually wide")

    return (
        BazaarSpreadPricing(
            best_buy_order_price=best_buy,
            second_best_buy_order_price=second_buy,
            best_sell_offer_price=best_sell,
            second_best_sell_offer_price=second_sell,
            realistic_buy_price=realistic_buy,
            realistic_sell_price=realistic_sell,
            raw_spread=raw_spread,
            fee_rate=fee_rate,
            net_sell_price=net_sell_price,
            net_profit_per_unit=net_profit,
            profit_percent=profit_percent,
        ),
        min(100.0, manipulation_risk),
        warnings,
    )


def _durable_buy_base(levels: list[_BookLevel], size: int) -> tuple[float, str | None]:
    if not levels:
        return 0.0, "missing buy order depth"
    threshold = max(1.0, size * 0.10)
    cumulative = 0.0
    for index, level in enumerate(levels):
        cumulative += max(0.0, level.amount)
        if cumulative >= threshold:
            warning = "top buy depth is thin for selected size" if index > 0 else None
            return level.price, warning
    return levels[-1].price, "buy-side depth is thin for selected size"


def _durable_sell_base(levels: list[_BookLevel], size: int) -> tuple[float, str | None]:
    if not levels:
        return 0.0, "missing sell offer depth"
    threshold = max(1.0, size * 0.10)
    cumulative = 0.0
    for index, level in enumerate(levels):
        cumulative += max(0.0, level.amount)
        if cumulative >= threshold:
            warning = "top sell depth is thin for selected size" if index > 0 else None
            return level.price, warning
    return levels[-1].price, "sell-side depth is thin for selected size"


def _levels(summary: tuple[dict[str, float], ...], *, descending: bool) -> list[_BookLevel]:
    levels = [
        _BookLevel(float(item["pricePerUnit"]), float(item.get("amount", 0.0)))
        for item in summary
        if item.get("pricePerUnit", 0) and float(item.get("pricePerUnit", 0)) > 0
    ]
    return sorted(levels, key=lambda item: item.price, reverse=descending)


def _normalized_order_books(product: BazaarProduct) -> tuple[list[_BookLevel], list[_BookLevel]]:
    first_buy_summary = _summary_first_price(product.buy_summary)
    first_sell_summary = _summary_first_price(product.sell_summary)
    if first_buy_summary is not None and first_sell_summary is not None and first_buy_summary > first_sell_summary:
        return (
            _levels(product.sell_summary, descending=True),
            _levels(product.buy_summary, descending=False),
        )
    return (
        _levels(product.buy_summary, descending=True),
        _levels(product.sell_summary, descending=False),
    )


def _summary_first_price(summary: tuple[dict[str, float], ...]) -> float | None:
    if not summary:
        return None
    value = summary[0].get("pricePerUnit")
    return float(value) if value is not None else None


def _depth_at_or_above(levels: list[_BookLevel], price: float) -> float:
    return sum(level.amount for level in levels if level.price >= price)


def _depth_at_or_below(levels: list[_BookLevel], price: float) -> float:
    return sum(level.amount for level in levels if level.price <= price)


def _bottleneck_speed(buy_speed: SpeedResult, sell_speed: SpeedResult) -> SpeedResult:
    slower = buy_speed if buy_speed.speed_score <= sell_speed.speed_score else sell_speed
    confidence = min(buy_speed.confidence_score, sell_speed.confidence_score)
    hours = max(
        [value for value in (buy_speed.estimated_hours, sell_speed.estimated_hours) if value is not None],
        default=None,
    )
    reason = f"bottleneck {slower.risk_label.lower()}: {slower.reason}"
    return SpeedResult(slower.speed_score, confidence, _speed_label(slower.speed_score), reason, hours)


def _competition_score(
    product: BazaarProduct,
    buy_depth: float,
    sell_depth: float,
    depth_at_buy: float,
    depth_at_sell: float,
) -> float:
    buy_daily = max(1.0, product.sell_moving_week / 7)
    sell_daily = max(1.0, product.buy_moving_week / 7)
    pressure = max(
        buy_depth / buy_daily,
        sell_depth / sell_daily,
        depth_at_buy / buy_daily,
        depth_at_sell / sell_daily,
    )
    return max(0.0, min(100.0, 100.0 - pressure * 120))


def _confidence_score(
    product: BazaarProduct,
    buy_orders: list[_BookLevel],
    sell_offers: list[_BookLevel],
    buy_speed: SpeedResult,
    sell_speed: SpeedResult,
    manipulation_risk: float,
) -> float:
    book_score = min(25.0, min(len(buy_orders), len(sell_offers)) / 5 * 25)
    movement_score = min(25.0, min(product.buy_moving_week, product.sell_moving_week) / 700_000 * 25)
    volume_score = min(20.0, min(product.buy_volume, product.sell_volume) / 100_000 * 20)
    speed_confidence = min(buy_speed.confidence_score, sell_speed.confidence_score) * 0.30
    return max(0.0, min(100.0, book_score + movement_score + volume_score + speed_confidence - manipulation_risk * 0.45))


def _risk_penalty(
    *,
    manipulation_risk: float,
    confidence_score: float,
    top_buy_depth: float,
    top_sell_depth: float,
    buy_moving_week: float,
    sell_moving_week: float,
    speed_gap: float,
    high_capital_usage: float,
    profit_percent: float,
    price_ratio: float,
) -> float:
    buy_daily = max(1.0, sell_moving_week / 7)
    sell_daily = max(1.0, buy_moving_week / 7)
    wall_penalty = min(16.0, max(top_buy_depth / buy_daily, top_sell_depth / sell_daily) * 12)
    thin_penalty = max(0.0, 45.0 - confidence_score) * 0.25
    movement_penalty = 8.0 if min(buy_moving_week, sell_moving_week) < 100_000 else 0.0
    asymmetry_penalty = max(0.0, speed_gap - 25.0) * 0.25
    capital_penalty = max(0.0, high_capital_usage - 0.65) * 16
    spread_penalty = 0.0
    if profit_percent >= 100 or price_ratio >= 2.0:
        spread_penalty = 24.0
    elif profit_percent >= 75:
        spread_penalty = 16.0
    elif profit_percent >= 40 or price_ratio >= 1.5:
        spread_penalty = 9.0
    return manipulation_risk * 0.20 + wall_penalty + thin_penalty + movement_penalty + asymmetry_penalty + capital_penalty + spread_penalty


def _is_high_outlier(best: float, second: float) -> bool:
    tick_gap = (best - second) / max(get_bazaar_tick_size(second), 0.0001)
    return second > 0 and best > second * 1.12 and tick_gap >= 5


def _is_low_outlier(best: float, second: float) -> bool:
    tick_gap = (second - best) / max(get_bazaar_tick_size(best), 0.0001)
    return best > 0 and second > best * 1.12 and tick_gap >= 5


def _has_huge_wall(moving_week: float, top_depth: float, config: AnalyzerConfig) -> bool:
    daily = moving_week / 7
    return daily <= 0 or top_depth / max(1.0, daily) > _max_spread_depth_ratio(config)


def _capital_used(pricing: BazaarSpreadPricing, suggested_size: int) -> float:
    return pricing.realistic_buy_price * max(0, suggested_size)


def _safe_spread_capital(config: AnalyzerConfig) -> float:
    safe_capital = config.budget * (config.max_capital_percent_per_flip / 100)
    if config.budget <= 50_000_000:
        safe_capital = min(safe_capital, config.budget * 0.10)
    return max(0.0, safe_capital)


def _min_profit_per_unit(config: AnalyzerConfig) -> float:
    return float(getattr(config, "min_spread_profit_per_unit", 0.0) or 0.0)


def _min_spread_volume_week(config: AnalyzerConfig) -> float:
    return float(getattr(config, "min_spread_volume_week", 50_000.0) or 0.0)


def _max_spread_depth_ratio(config: AnalyzerConfig) -> float:
    return float(getattr(config, "max_spread_depth_ratio", 0.75) or 0.75)


def _spread_limit(config: AnalyzerConfig) -> int:
    return int(getattr(config, "spread_limit", config.limit) or config.limit)


def _max_estimated_buy_minutes(config: AnalyzerConfig) -> float | None:
    value = getattr(config, "max_estimated_buy_minutes", None)
    return None if value is None else float(value)


def _max_estimated_sell_minutes(config: AnalyzerConfig) -> float | None:
    value = getattr(config, "max_estimated_sell_minutes", None)
    return None if value is None else float(value)


def _max_estimated_bottleneck_minutes(config: AnalyzerConfig) -> float:
    return float(getattr(config, "max_estimated_bottleneck_minutes", 240.0) or 240.0)


def _min_speed_confidence(config: AnalyzerConfig) -> float:
    return float(getattr(config, "min_speed_confidence", 35.0) or 0.0)


def _minutes_text(minutes: float | None) -> str:
    if minutes is None:
        return "unknown"
    if minutes < 60:
        return f"~{minutes:.0f}m"
    return f"~{minutes / 60:.1f}h"


def _speed_label(score: float) -> str:
    if score >= 85:
        return "Very fast"
    if score >= 70:
        return "Fast"
    if score >= 45:
        return "Medium"
    if score >= 25:
        return "Slow"
    return "Too slow"


def _compact(value: float) -> str:
    absolute = abs(value)
    if absolute >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if absolute >= 1_000:
        return f"{value / 1_000:.1f}k"
    return f"{value:.0f}"


def _format_price(value: float) -> str:
    if value < 1:
        return f"{value:,.4f}".rstrip("0").rstrip(".")
    if value < 10:
        return f"{value:,.3f}".rstrip("0").rstrip(".")
    if value < 100:
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    if value < 1_000:
        return f"{value:,.1f}".rstrip("0").rstrip(".")
    return f"{value:,.0f}"
