from __future__ import annotations

from .bazaar import BazaarClient, BazaarProduct
from .market_speed import combine_bazaar_side_speeds
from .models import RejectedItem
from .scoring import AnalyzerConfig
from .bazaar_spread_models import (
    MIN_BOTTLENECK_SPEED,
    BazaarOrderBookStats,
    BazaarSpreadOpportunity,
    _BookLevel,
    _SpreadCandidate,
)
from .bazaar_spread_history import _history_stats, load_spread_history, save_spread_history
from .bazaar_spread_risk import (
    _apply_spread_risk_floor,
    _risk_label,
    _should_test_first,
    _suggest_test_order_size,
    _volatile_min_risk,
    _volatile_reason,
)
from .bazaar_spread_support import (
    _choose_pricing_for_size,
    _competition_score,
    _confidence_score,
    _depth_at_or_above,
    _depth_at_or_below,
    _format_price,
    _has_huge_wall,
    _max_estimated_bottleneck_minutes,
    _max_estimated_buy_minutes,
    _max_estimated_sell_minutes,
    _min_profit_per_unit,
    _min_speed_confidence,
    _min_spread_volume_week,
    _minutes_text,
    _normalized_order_books,
    _risk_penalty,
    _rough_profit_per_unit,
    _safe_spread_capital,
    _spread_limit,
    estimate_spread_side_speed,
    get_bazaar_tick_size,
    score_bazaar_spread,
)


def find_bazaar_spread_flips(
    bazaar: BazaarClient,
    config: AnalyzerConfig,
) -> tuple[list[BazaarSpreadOpportunity], list[RejectedItem]]:
    accepted: list[BazaarSpreadOpportunity] = []
    rejected: list[RejectedItem] = []
    history = load_spread_history()
    for product_id in sorted(bazaar.products()):
        metrics = bazaar.product_metrics(product_id)
        if metrics is None:
            continue
        flip = evaluate_bazaar_spread_product(metrics, config, history=history.get(product_id, []))
        if isinstance(flip, BazaarSpreadOpportunity):
            accepted.append(flip)
        else:
            rejected.append(flip)
    accepted.sort(
        key=lambda item: (
            item.coins_per_hour,
            item.profit_per_minute,
            item.final_score,
            item.bottleneck_speed.speed_score,
            item.estimated_total_profit,
        ),
        reverse=True,
    )
    save_spread_history(history, accepted)
    return accepted[: _spread_limit(config)], rejected


def evaluate_bazaar_spread_product(product: BazaarProduct, config: AnalyzerConfig, *, history: list[dict] | None = None) -> BazaarSpreadOpportunity | RejectedItem:
    rejection: list[str] = []
    warnings: list[str] = []

    buy_orders, sell_offers = _normalized_order_books(product)
    if not buy_orders and product.best_buy_order:
        buy_orders = [_BookLevel(product.best_buy_order, product.top_buy_order_depth)]
    if not sell_offers and product.best_sell_offer:
        sell_offers = [_BookLevel(product.best_sell_offer, product.top_sell_offer_depth)]

    if len(buy_orders) < 2 or len(sell_offers) < 2:
        return RejectedItem("bazaar-spread", product.tag, "order book is too thin")

    candidate = choose_best_spread_candidate(product, buy_orders, sell_offers, config)
    if candidate is None:
        return RejectedItem("bazaar-spread", product.tag, "no profitable depth-aware order size")
    pricing = candidate.pricing
    manipulation_risk = candidate.manipulation_risk
    warnings.extend(candidate.warnings)

    buy_depth = buy_orders[0].amount
    sell_depth = sell_offers[0].amount
    depth_at_buy = candidate.depth_at_buy
    depth_at_sell = candidate.depth_at_sell
    buy_speed = candidate.buy_speed
    sell_speed = candidate.sell_speed
    bottleneck = candidate.bottleneck
    history_seen_count, history_stability_score = _history_stats(history or [], pricing)
    confidence = _confidence_score(product, buy_orders, sell_offers, buy_speed, sell_speed, manipulation_risk)
    confidence = max(0.0, min(100.0, confidence + history_stability_score * 8.0))
    competition = _competition_score(product, buy_depth, sell_depth, depth_at_buy, depth_at_sell)
    order_book = BazaarOrderBookStats(
        buy_order_depth=buy_depth,
        sell_offer_depth=sell_depth,
        buy_moving_week=product.buy_moving_week,
        sell_moving_week=product.sell_moving_week,
        buy_volume=product.buy_volume,
        sell_volume=product.sell_volume,
        depth_at_or_above_buy_price=depth_at_buy,
        depth_at_or_below_sell_price=depth_at_sell,
        estimated_buy_fill_speed=buy_speed,
        estimated_sell_fill_speed=sell_speed,
        bottleneck_speed=bottleneck,
        competition_score=competition,
        manipulation_risk=manipulation_risk,
        confidence_score=confidence,
        history_seen_count=history_seen_count,
        history_stability_score=history_stability_score,
    )
    suggested_size = candidate.size
    estimated_profit = candidate.estimated_profit

    if pricing.net_profit_per_unit <= 0:
        rejection.append("spread is negative after Bazaar fee")
    if pricing.net_profit_per_unit < _min_profit_per_unit(config):
        rejection.append(f"profit/unit below {_min_profit_per_unit(config):,.2f}")
    if pricing.profit_percent < config.min_profit_percent:
        rejection.append(f"profit percent below {config.min_profit_percent:g}%")
    if estimated_profit < config.min_profit:
        rejection.append(f"estimated total profit below {config.min_profit:,.0f}")
    if min(product.buy_moving_week, product.sell_moving_week) < _min_spread_volume_week(config):
        rejection.append(f"weekly movement below {_min_spread_volume_week(config):,.0f}")
    if _has_huge_wall(product.sell_moving_week, buy_depth, config) or _has_huge_wall(product.buy_moving_week, sell_depth, config):
        rejection.append("top order depth is huge compared to weekly movement")
    if bottleneck.speed_score < MIN_BOTTLENECK_SPEED:
        rejection.append("bottleneck speed is too low")
    if buy_speed.estimated_minutes is not None and _max_estimated_buy_minutes(config) is not None and buy_speed.estimated_minutes > _max_estimated_buy_minutes(config):
        rejection.append(f"estimated buy fill above {_max_estimated_buy_minutes(config):g}m")
    if sell_speed.estimated_minutes is not None and _max_estimated_sell_minutes(config) is not None and sell_speed.estimated_minutes > _max_estimated_sell_minutes(config):
        rejection.append(f"estimated sell fill above {_max_estimated_sell_minutes(config):g}m")
    if buy_speed.risk_label in {"Slow", "Too slow"} and buy_speed.speed_score < 45:
        rejection.append("buy side is too slow for fast spread flipping")
    if sell_speed.risk_label in {"Slow", "Too slow"} and sell_speed.speed_score < 45:
        rejection.append("sell side is too slow for fast spread flipping")
    if bottleneck.estimated_minutes is None or bottleneck.estimated_minutes > _max_estimated_bottleneck_minutes(config):
        rejection.append(f"estimated bottleneck above {_max_estimated_bottleneck_minutes(config):g}m")
    if bottleneck.confidence_score < _min_speed_confidence(config):
        rejection.append("speed confidence is too low")
    if manipulation_risk >= 70:
        rejection.append("spread depends on suspicious outlier orders")
    if suggested_size <= 0:
        rejection.append("suggested order size exceeds safe budget fraction")

    speed_gap = abs(buy_speed.speed_score - sell_speed.speed_score)
    high_capital_usage = candidate.capital_required / max(1.0, _safe_spread_capital(config))
    price_ratio = pricing.realistic_sell_price / pricing.realistic_buy_price if pricing.realistic_buy_price > 0 else 0.0
    volatile_reason = _volatile_reason(product.tag)
    volatile_min_risk = _volatile_min_risk(product.tag)
    strong_history = history_seen_count >= 3 and history_stability_score >= 1.0
    thin_depth = (
        min(buy_depth, sell_depth) < max(5.0, suggested_size * 0.20)
        or buy_speed.has_huge_wall
        or sell_speed.has_huge_wall
        or buy_speed.has_missing_data
        or sell_speed.has_missing_data
        or confidence < 50
    )
    penalties = _risk_penalty(
        manipulation_risk=manipulation_risk,
        confidence_score=confidence,
        top_buy_depth=buy_depth,
        top_sell_depth=sell_depth,
        buy_moving_week=product.buy_moving_week,
        sell_moving_week=product.sell_moving_week,
        speed_gap=speed_gap,
        high_capital_usage=high_capital_usage,
        profit_percent=pricing.profit_percent,
        price_ratio=price_ratio,
    )
    score = score_bazaar_spread(
        estimated_total_profit=estimated_profit,
        profit_percent=pricing.profit_percent,
        profit_per_minute=candidate.profit_per_minute,
        coins_per_hour=candidate.coins_per_hour,
        bottleneck_speed_score=bottleneck.speed_score,
        confidence_score=confidence,
        competition_score=competition,
        history_stability_score=history_stability_score,
        penalty=penalties,
        config=config,
    )

    if rejection:
        return RejectedItem("bazaar-spread", product.tag, " and ".join(rejection[:3]))

    base_risk = _risk_label(score, manipulation_risk, bottleneck.speed_score)
    risk, risk_reasons = _apply_spread_risk_floor(
        base_risk=base_risk,
        profit_percent=pricing.profit_percent,
        price_ratio=price_ratio,
        estimated_profit=estimated_profit,
        capital_required=candidate.capital_required,
        thin_depth=thin_depth,
        history_seen_count=history_seen_count,
        strong_history=strong_history,
        confidence_score=confidence,
        volatile_reason=volatile_reason,
        volatile_min_risk=volatile_min_risk,
    )
    should_test_first = _should_test_first(
        risk=risk,
        profit_percent=pricing.profit_percent,
        estimated_profit=estimated_profit,
        capital_required=candidate.capital_required,
        history_seen_count=history_seen_count,
        confidence_score=confidence,
        volatile_reason=volatile_reason,
    )
    suggested_test_size = _suggest_test_order_size(
        full_size=suggested_size,
        buy_price=pricing.realistic_buy_price,
        risk=risk,
        config=config,
    )
    reason_parts = [
        f"buy {_minutes_text(buy_speed.estimated_minutes)}",
        f"sell {_minutes_text(sell_speed.estimated_minutes)}",
        f"{pricing.profit_percent:.1f}% spread",
        f"bottleneck {bottleneck.risk_label}",
    ]
    if history_seen_count:
        reason_parts.append(f"seen {history_seen_count} recent refreshes")
    if history_stability_score >= 1.0:
        reason_parts.append("local history stable")
    reason_parts.extend(risk_reasons[:4])
    if warnings:
        reason_parts.append(warnings[0])
    if should_test_first:
        reason_parts.append("test small first")
        manual_action = (
            f"Test first: place buy order for {suggested_test_size:,}x {product.tag} at around {_format_price(pricing.realistic_buy_price)}. "
            f"If it fills and sells quickly, scale up toward {suggested_size:,}x. "
            "Re-check Bazaar before doing it."
        )
    else:
        manual_action = (
            f"Place buy order for {suggested_size:,}x {product.tag} at around {_format_price(pricing.realistic_buy_price)}. "
            f"After filled, place sell order at around {_format_price(pricing.realistic_sell_price)}. "
            "Re-check Bazaar before doing it."
        )
    return BazaarSpreadOpportunity(
        product_id=product.tag,
        pricing=pricing,
        order_book=order_book,
        suggested_order_size=suggested_size,
        estimated_total_profit=estimated_profit,
        capital_required=candidate.capital_required,
        profit_per_minute=candidate.profit_per_minute,
        coins_per_hour=candidate.coins_per_hour,
        final_score=score,
        risk=risk,
        reason="; ".join(reason_parts),
        manual_action=manual_action,
        suggested_full_size=suggested_size,
        suggested_test_size=suggested_test_size,
        should_test_first=should_test_first,
    )

def choose_best_spread_candidate(
    product: BazaarProduct,
    buy_orders: list[_BookLevel],
    sell_offers: list[_BookLevel],
    config: AnalyzerConfig,
) -> _SpreadCandidate | None:
    candidates: list[_SpreadCandidate] = []
    for size in candidate_order_sizes(product, buy_orders, sell_offers, config):
        pricing, manipulation_risk, warnings = _choose_pricing_for_size(product, buy_orders, sell_offers, size, config)
        if pricing.realistic_buy_price <= 0 or pricing.net_profit_per_unit <= 0:
            continue
        depth_at_buy = _depth_at_or_above(buy_orders, pricing.realistic_buy_price)
        depth_at_sell = _depth_at_or_below(sell_offers, pricing.realistic_sell_price)
        buy_speed = estimate_spread_side_speed(
            moving_week=product.sell_moving_week,
            live_volume=product.sell_volume,
            top_depth=buy_orders[0].amount,
            depth_at_price=depth_at_buy,
            order_summary=product.buy_summary,
            recommended_price=pricing.realistic_buy_price,
            side="buy",
            order_size=size,
        )
        sell_speed = estimate_spread_side_speed(
            moving_week=product.buy_moving_week,
            live_volume=product.buy_volume,
            top_depth=sell_offers[0].amount,
            depth_at_price=depth_at_sell,
            order_summary=product.sell_summary,
            recommended_price=pricing.realistic_sell_price,
            side="sell",
            order_size=size,
        )
        bottleneck = combine_bazaar_side_speeds(buy_speed, sell_speed)
        estimated_profit = pricing.net_profit_per_unit * size
        if estimated_profit < config.min_profit:
            continue
        capital_required = pricing.realistic_buy_price * size
        minutes = bottleneck.estimated_minutes
        profit_per_minute = estimated_profit / minutes if minutes and minutes > 0 else 0.0
        coins_per_hour = profit_per_minute * 60
        candidates.append(
            _SpreadCandidate(
                pricing=pricing,
                size=size,
                buy_speed=buy_speed,
                sell_speed=sell_speed,
                bottleneck=bottleneck,
                estimated_profit=estimated_profit,
                capital_required=capital_required,
                profit_per_minute=profit_per_minute,
                coins_per_hour=coins_per_hour,
                depth_at_buy=depth_at_buy,
                depth_at_sell=depth_at_sell,
                manipulation_risk=manipulation_risk,
                warnings=warnings,
            )
        )
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (
            item.coins_per_hour,
            item.profit_per_minute,
            item.estimated_profit,
            item.bottleneck.speed_score,
        ),
    )


def candidate_order_sizes(
    product: BazaarProduct,
    buy_orders: list[_BookLevel],
    sell_offers: list[_BookLevel],
    config: AnalyzerConfig,
) -> list[int]:
    base_price = max(0.0001, buy_orders[0].price + get_bazaar_tick_size(buy_orders[0].price))
    safe_capital = _safe_spread_capital(config)
    capital_cap = int(safe_capital // base_price)
    if capital_cap <= 0:
        return []
    daily = min(product.buy_moving_week, product.sell_moving_week) / 7
    hourly = max(1.0, daily / 24)
    visible_depth = max(1.0, min(buy_orders[0].amount, sell_offers[0].amount))
    raw_sizes = {
        1,
        5,
        10,
        int(hourly * 0.10),
        int(hourly * 0.25),
        int(hourly * 0.50),
        int(visible_depth * 0.10),
        int(visible_depth * 0.25),
        int(visible_depth * 0.50),
        int(visible_depth),
        int(max(config.min_profit, 1.0) / max(0.0001, _rough_profit_per_unit(buy_orders[0].price, sell_offers[0].price, config))),
    }
    raw_sizes.update(int(level.amount * factor) for level in [*buy_orders[:3], *sell_offers[:3]] for factor in (0.1, 0.25, 0.5))
    return sorted({max(1, min(capital_cap, size)) for size in raw_sizes if size and size > 0})
