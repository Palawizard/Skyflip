from __future__ import annotations

from dataclasses import dataclass

from .bazaar import BazaarClient, BazaarProduct
from .market_speed import SpeedResult, combine_bazaar_side_speeds, estimate_bazaar_order_fill_speed
from .models import RejectedItem
from .scoring import AnalyzerConfig, score_generic_opportunity


BAZAAR_TAX_RATE = 0.0125


@dataclass(frozen=True)
class BazaarOrderFlip:
    product_id: str
    buy_order_price: float
    sell_order_price: float
    net_profit_per_unit: float
    profit_percent: float
    buy_moving_week: float
    sell_moving_week: float
    speed: SpeedResult
    buy_fill_speed: SpeedResult
    sell_fill_speed: SpeedResult
    suggested_order_size: int
    estimated_profit: float
    risk: str
    reason: str
    score: float
    manual_action: str

    @property
    def estimated_buy_fill_minutes(self) -> float | None:
        return self.buy_fill_speed.estimated_minutes

    @property
    def estimated_sell_fill_minutes(self) -> float | None:
        return self.sell_fill_speed.estimated_minutes

    @property
    def estimated_bottleneck_minutes(self) -> float | None:
        return self.speed.estimated_minutes

    @property
    def buy_speed_label(self) -> str:
        return self.buy_fill_speed.risk_label

    @property
    def sell_speed_label(self) -> str:
        return self.sell_fill_speed.risk_label

    @property
    def bottleneck_speed_label(self) -> str:
        return self.speed.risk_label

    @property
    def speed_confidence(self) -> float:
        return self.speed.confidence_score

    @property
    def speed_reason(self) -> str:
        return self.speed.reason

    @property
    def depth_ahead_buy(self) -> float:
        return self.buy_fill_speed.depth_ahead

    @property
    def depth_ahead_sell(self) -> float:
        return self.sell_fill_speed.depth_ahead


def find_bazaar_order_flips(
    bazaar: BazaarClient,
    config: AnalyzerConfig,
) -> tuple[list[BazaarOrderFlip], list[RejectedItem]]:
    accepted: list[BazaarOrderFlip] = []
    rejected: list[RejectedItem] = []
    for product_id in sorted(bazaar.products()):
        metrics = bazaar.product_metrics(product_id)
        if metrics is None:
            continue
        flip = evaluate_bazaar_order_product(metrics, config)
        if isinstance(flip, BazaarOrderFlip):
            accepted.append(flip)
        else:
            rejected.append(flip)
    accepted.sort(key=lambda item: (item.score, item.speed.speed_score, item.estimated_profit), reverse=True)
    return accepted[: config.limit], rejected


def evaluate_bazaar_order_product(product: BazaarProduct, config: AnalyzerConfig) -> BazaarOrderFlip | RejectedItem:
    rejection: list[str] = []
    best_buy, best_sell, top_buy_depth, top_sell_depth, buy_summary, sell_summary = _normalized_top_book(product)
    if best_buy <= 0 or best_sell <= 0:
        return RejectedItem("bazaar-order", product.tag, "missing usable buy/sell order prices")

    buy_order_price = best_buy + _tick(best_buy)
    sell_order_price = max(0.0, best_sell - _tick(best_sell))
    net_profit = sell_order_price * (1 - BAZAAR_TAX_RATE) - buy_order_price
    profit_percent = net_profit / buy_order_price * 100 if buy_order_price > 0 else 0.0
    buy_speed = estimate_bazaar_order_fill_speed(
        side="buy",
        recommended_price=buy_order_price,
        order_summary=buy_summary,
        moving_week=product.sell_moving_week,
        live_volume=product.sell_volume,
        depth_ahead=_depth_at_or_above(buy_summary, buy_order_price) or top_buy_depth,
    )
    sell_speed = estimate_bazaar_order_fill_speed(
        side="sell",
        recommended_price=sell_order_price,
        order_summary=sell_summary,
        moving_week=product.buy_moving_week,
        live_volume=product.buy_volume,
        depth_ahead=_depth_at_or_below(sell_summary, sell_order_price) or top_sell_depth,
    )
    speed = combine_bazaar_side_speeds(buy_speed, sell_speed)
    daily_volume = min(product.buy_moving_week, product.sell_moving_week) / 7
    top_depth = max(top_buy_depth, top_sell_depth)
    safe_capital = config.budget * (config.max_capital_percent_per_flip / 100)
    volume_cap = max(1, int(daily_volume * 0.01))
    depth_cap = max(1, int(max(1.0, top_depth) * 0.5)) if top_depth else volume_cap
    capital_cap = int(safe_capital // buy_order_price)
    if config.budget <= 50_000_000:
        capital_cap = min(capital_cap, int((config.budget * 0.08) // buy_order_price))
    size = max(0, min(volume_cap, depth_cap, capital_cap))
    if size > 0:
        buy_speed = estimate_bazaar_order_fill_speed(
            side="buy",
            recommended_price=buy_order_price,
            order_summary=buy_summary,
            moving_week=product.sell_moving_week,
            live_volume=product.sell_volume,
            depth_ahead=_depth_at_or_above(buy_summary, buy_order_price) or top_buy_depth,
            order_size=size,
        )
        sell_speed = estimate_bazaar_order_fill_speed(
            side="sell",
            recommended_price=sell_order_price,
            order_summary=sell_summary,
            moving_week=product.buy_moving_week,
            live_volume=product.buy_volume,
            depth_ahead=_depth_at_or_below(sell_summary, sell_order_price) or top_sell_depth,
            order_size=size,
        )
        speed = combine_bazaar_side_speeds(buy_speed, sell_speed)
    estimated_profit = net_profit * size

    if net_profit <= 0:
        rejection.append("spread is negative after Bazaar tax")
    if profit_percent < max(0.5, config.min_profit_percent / 10):
        rejection.append("spread too small after tax")
    if daily_volume < max(2_000, config.min_sales_per_day * 500):
        rejection.append("moving week volume is too low")
    if speed.estimated_hours is not None and speed.estimated_hours > config.max_median_sell_time_hours:
        rejection.append("estimated fill time too long")
    if buy_speed.estimated_minutes is not None and _max_estimated_buy_minutes(config) is not None and buy_speed.estimated_minutes > _max_estimated_buy_minutes(config):
        rejection.append(f"estimated buy fill above {_max_estimated_buy_minutes(config):g}m")
    if sell_speed.estimated_minutes is not None and _max_estimated_sell_minutes(config) is not None and sell_speed.estimated_minutes > _max_estimated_sell_minutes(config):
        rejection.append(f"estimated sell fill above {_max_estimated_sell_minutes(config):g}m")
    if speed.estimated_minutes is None or speed.estimated_minutes > _max_estimated_bottleneck_minutes(config):
        rejection.append(f"estimated bottleneck above {_max_estimated_bottleneck_minutes(config):g}m")
    if speed.confidence_score < _min_speed_confidence(config):
        rejection.append("speed confidence is too low")
    if top_depth > daily_volume * 0.75 and daily_volume > 0:
        rejection.append("top order depth is huge compared to moving volume")
    if size <= 0:
        rejection.append("required capital exceeds safe budget fraction")
    if estimated_profit < config.min_profit:
        rejection.append(f"estimated order profit below {config.min_profit:,.0f}")
    if product.sell_price > 0 and product.buy_price > 0 and product.sell_price > product.buy_price * 3 and daily_volume < 25_000:
        rejection.append("suspicious spread with weak volume")

    competition = max(0.0, min(100.0, 100.0 - (top_depth / max(1.0, daily_volume) * 100)))
    budget_fit = max(0.0, min(100.0, 100.0 * (1 - (buy_order_price * max(size, 1)) / max(1.0, safe_capital))))
    risk_penalty = 15.0 if speed.speed_score < 45 else 0.0
    score = score_generic_opportunity(
        profit=estimated_profit,
        profit_percent=profit_percent,
        speed_score=speed.speed_score,
        confidence_score=speed.confidence_score,
        budget_fit_score=budget_fit,
        competition_score=competition,
        risk_penalty=risk_penalty,
    )

    if rejection:
        return RejectedItem("bazaar-order", product.tag, " and ".join(rejection[:3]))

    reason = f"{speed.reason}; {profit_percent:.2f}% net spread"
    return BazaarOrderFlip(
        product_id=product.tag,
        buy_order_price=buy_order_price,
        sell_order_price=sell_order_price,
        net_profit_per_unit=net_profit,
        profit_percent=profit_percent,
        buy_moving_week=product.buy_moving_week,
        sell_moving_week=product.sell_moving_week,
        speed=speed,
        buy_fill_speed=buy_speed,
        sell_fill_speed=sell_speed,
        suggested_order_size=size,
        estimated_profit=estimated_profit,
        risk=speed.risk_label,
        reason=reason,
        score=score,
        manual_action=(
            f"Suggested manual action: place buy order for {size:,} units at {buy_order_price:,.1f}, "
            f"then sell order at {sell_order_price:,.1f}."
        ),
    )


def _tick(price: float) -> float:
    if price >= 1_000_000:
        return 100.0
    if price >= 10_000:
        return 1.0
    if price >= 100:
        return 0.1
    return 0.01


def _normalized_top_book(product: BazaarProduct) -> tuple[float, float, float, float, tuple[dict[str, float], ...], tuple[dict[str, float], ...]]:
    raw_buy_summary_price = _first_price(product.buy_summary)
    raw_sell_summary_price = _first_price(product.sell_summary)
    if raw_buy_summary_price is not None and raw_sell_summary_price is not None and raw_buy_summary_price > raw_sell_summary_price:
        return (
            raw_sell_summary_price,
            raw_buy_summary_price,
            _first_amount(product.sell_summary, product.top_sell_offer_depth),
            _first_amount(product.buy_summary, product.top_buy_order_depth),
            product.sell_summary,
            product.buy_summary,
        )
    return (
        product.best_buy_order or product.buy_price,
        product.best_sell_offer or product.sell_price,
        product.top_buy_order_depth,
        product.top_sell_offer_depth,
        product.buy_summary,
        product.sell_summary,
    )


def _first_price(summary: tuple[dict[str, float], ...]) -> float | None:
    if not summary:
        return None
    value = summary[0].get("pricePerUnit")
    return float(value) if value is not None else None


def _first_amount(summary: tuple[dict[str, float], ...], fallback: float) -> float:
    if not summary:
        return fallback
    return float(summary[0].get("amount") or fallback or 0.0)


def _depth_at_or_above(summary: tuple[dict[str, float], ...], price: float) -> float:
    return sum(float(item.get("amount") or 0.0) for item in summary if float(item.get("pricePerUnit") or 0.0) >= price)


def _depth_at_or_below(summary: tuple[dict[str, float], ...], price: float) -> float:
    return sum(float(item.get("amount") or 0.0) for item in summary if float(item.get("pricePerUnit") or 0.0) <= price)


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
