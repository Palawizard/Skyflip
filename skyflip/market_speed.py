from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpeedResult:
    speed_score: float
    confidence_score: float
    risk_label: str
    reason: str
    estimated_hours: float | None = None
    estimated_minutes: float | None = None
    depth_ahead: float = 0.0
    hourly_flow: float = 0.0
    has_huge_wall: bool = False
    has_missing_data: bool = False


def normalize_ah_speed(
    *,
    sales_per_day: float,
    median_sell_time_hours: float | None,
    sold_sample_count: int,
    active_bin_count: int,
) -> SpeedResult:
    volume_score = min(45.0, max(0.0, sales_per_day) / 30 * 45)
    if median_sell_time_hours is None:
        time_score = 12.0
    else:
        time_score = max(0.0, min(35.0, 35.0 * (1 - median_sell_time_hours / 24)))
    pressure_penalty = 0.0
    if sales_per_day > 0:
        pressure_penalty = min(25.0, max(0.0, active_bin_count / sales_per_day - 2) * 4)
    sample_score = min(20.0, sold_sample_count / 30 * 20)
    speed_score = max(0.0, min(100.0, volume_score + time_score + sample_score - pressure_penalty))
    confidence = min(100.0, 25.0 + min(45.0, sold_sample_count / 30 * 45) + min(30.0, sales_per_day / 20 * 30))
    risk = _risk_label(speed_score)
    reason_parts = [f"{sales_per_day:.1f} sales/day"]
    if median_sell_time_hours is not None:
        reason_parts.append(f"{median_sell_time_hours:.1f}h median")
    if pressure_penalty:
        reason_parts.append("listing pressure")
    return SpeedResult(speed_score, confidence, risk, ", ".join(reason_parts), median_sell_time_hours)


def normalize_bazaar_speed(
    *,
    buy_moving_week: float,
    sell_moving_week: float,
    buy_volume: float,
    sell_volume: float,
    top_order_depth: float,
) -> SpeedResult:
    buy = estimate_bazaar_order_fill_speed(
        side="buy",
        recommended_price=0,
        order_summary=(),
        moving_week=sell_moving_week,
        live_volume=sell_volume,
        depth_ahead=top_order_depth,
    )
    sell = estimate_bazaar_order_fill_speed(
        side="sell",
        recommended_price=0,
        order_summary=(),
        moving_week=buy_moving_week,
        live_volume=buy_volume,
        depth_ahead=top_order_depth,
    )
    return combine_bazaar_side_speeds(buy, sell)


def normalize_conversion_speed(input_speeds: list[SpeedResult], output_speed: SpeedResult) -> SpeedResult:
    if not input_speeds:
        return output_speed
    input_score = min(speed.speed_score for speed in input_speeds)
    input_confidence = min(speed.confidence_score for speed in input_speeds)
    bottleneck = min([*input_speeds, output_speed], key=lambda s: s.speed_score)
    speed_score = max(0.0, min(100.0, bottleneck.speed_score - 8.0))
    confidence = max(0.0, min(100.0, min(output_speed.confidence_score, input_confidence) - 5.0))
    minutes = max((speed.estimated_minutes for speed in [*input_speeds, output_speed] if speed.estimated_minutes is not None), default=None)
    hours = None if minutes is None else minutes / 60
    label = _label_for_minutes(minutes, confidence, has_huge_wall=any(s.has_huge_wall for s in [*input_speeds, output_speed]), has_missing_data=any(s.has_missing_data for s in [*input_speeds, output_speed]))
    reason = f"manual effort penalty; bottleneck {bottleneck.reason}"
    return SpeedResult(speed_score, confidence, label, reason, hours, minutes, bottleneck.depth_ahead, bottleneck.hourly_flow, bottleneck.has_huge_wall, bottleneck.has_missing_data)


def estimate_bazaar_order_fill_speed(
    *,
    side: str,
    recommended_price: float,
    order_summary: tuple[dict[str, float], ...] | list[dict[str, float]],
    moving_week: float,
    live_volume: float,
    depth_ahead: float | None = None,
    order_size: float = 0.0,
    stale_cache: bool = False,
) -> SpeedResult:
    levels = _clean_levels(order_summary)
    missing_book = not levels and depth_ahead is None
    if depth_ahead is None:
        if side == "buy":
            depth = sum(amount for price, amount in levels if recommended_price <= 0 or price >= recommended_price)
        else:
            depth = sum(amount for price, amount in levels if recommended_price <= 0 or price <= recommended_price)
    else:
        depth = max(0.0, depth_ahead)
    depth += max(0.0, order_size)
    hourly_flow = max(0.0, moving_week) / 7 / 24
    estimated_minutes = None if hourly_flow <= 0 else depth / max(hourly_flow, 1e-9) * 60
    daily_flow = hourly_flow * 24
    top_depth = levels[0][1] if levels else depth
    huge_wall = daily_flow <= 0 or top_depth > max(1.0, daily_flow * 0.35) or depth > max(1.0, daily_flow * 0.75)
    low_movement = moving_week < 25_000
    thin_book = len(levels) < 2
    suspicious = _top_price_suspicious(levels, side)
    size_penalty = 18.0 if order_size and hourly_flow and order_size > hourly_flow else 0.0
    confidence = 85.0
    if missing_book:
        confidence -= 45.0
    if thin_book:
        confidence -= 18.0
    if low_movement:
        confidence -= 25.0
    if live_volume <= 0:
        confidence -= 12.0
    if huge_wall:
        confidence -= 25.0
    if suspicious:
        confidence -= 22.0
    if stale_cache:
        confidence -= 35.0
    confidence -= size_penalty
    confidence = max(0.0, min(100.0, confidence))
    label = _label_for_minutes(estimated_minutes, confidence, has_huge_wall=huge_wall, has_missing_data=missing_book or hourly_flow <= 0 or stale_cache)
    speed_score = _score_for_minutes(estimated_minutes, confidence, label)
    reason = _bazaar_reason(side, estimated_minutes, depth, huge_wall, missing_book, low_movement, suspicious, stale_cache)
    hours = None if estimated_minutes is None else estimated_minutes / 60
    return SpeedResult(speed_score, confidence, label, reason, hours, estimated_minutes, depth, hourly_flow, huge_wall, missing_book or stale_cache)


def combine_bazaar_side_speeds(buy_speed: SpeedResult, sell_speed: SpeedResult) -> SpeedResult:
    minutes = max((value for value in (buy_speed.estimated_minutes, sell_speed.estimated_minutes) if value is not None), default=None)
    confidence = min(buy_speed.confidence_score, sell_speed.confidence_score)
    huge_wall = buy_speed.has_huge_wall or sell_speed.has_huge_wall
    missing = buy_speed.has_missing_data or sell_speed.has_missing_data
    slower = buy_speed if buy_speed.speed_score <= sell_speed.speed_score else sell_speed
    if abs(buy_speed.speed_score - sell_speed.speed_score) >= 35:
        confidence = max(0.0, confidence - 12.0)
    label = _label_for_minutes(minutes, confidence, has_huge_wall=huge_wall, has_missing_data=missing)
    score = min(buy_speed.speed_score, sell_speed.speed_score)
    if label == "Very fast":
        score = max(score, 85.0)
    elif label == "Too slow":
        score = min(score, 24.0)
    reason = f"buy {_minutes_text(buy_speed.estimated_minutes)}, sell {_minutes_text(sell_speed.estimated_minutes)}"
    if slower.has_huge_wall:
        reason = f"top {('buy' if slower is buy_speed else 'sell')} wall too large; {slower.reason}"
    elif slower.has_missing_data:
        reason = f"low confidence; {slower.reason}"
    hours = None if minutes is None else minutes / 60
    return SpeedResult(score, confidence, label, reason, hours, minutes, max(buy_speed.depth_ahead, sell_speed.depth_ahead), min(buy_speed.hourly_flow, sell_speed.hourly_flow), huge_wall, missing)


def _risk_label(score: float) -> str:
    if score >= 85:
        return "Very fast"
    if score >= 70:
        return "Fast"
    if score >= 45:
        return "Medium"
    if score >= 25:
        return "Slow"
    return "Too slow"


def _label_for_minutes(minutes: float | None, confidence: float, *, has_huge_wall: bool, has_missing_data: bool) -> str:
    if minutes is None or has_missing_data or confidence < 30:
        return "Too slow"
    if minutes <= 15 and confidence >= 75 and not has_huge_wall:
        return "Very fast"
    if minutes <= 60 and confidence >= 50:
        return "Fast"
    if minutes <= 240 and confidence >= 40:
        return "Medium"
    if minutes <= 720 and confidence >= 30:
        return "Slow"
    return "Too slow"


def _score_for_minutes(minutes: float | None, confidence: float, label: str) -> float:
    if minutes is None:
        return min(20.0, confidence * 0.25)
    if label == "Very fast":
        base = 92.0
    elif label == "Fast":
        base = 78.0
    elif label == "Medium":
        base = 55.0
    elif label == "Slow":
        base = 33.0
    else:
        base = 12.0
    time_penalty = min(20.0, max(0.0, minutes - 15) / 720 * 20)
    return max(0.0, min(100.0, base - time_penalty + (confidence - 60) * 0.15))


def _clean_levels(summary: tuple[dict[str, float], ...] | list[dict[str, float]]) -> list[tuple[float, float]]:
    levels: list[tuple[float, float]] = []
    for item in summary:
        try:
            price = float(item.get("pricePerUnit", 0.0))
            amount = float(item.get("amount", 0.0))
        except (TypeError, ValueError, AttributeError):
            continue
        if price > 0 and amount > 0:
            levels.append((price, amount))
    return levels


def _top_price_suspicious(levels: list[tuple[float, float]], side: str) -> bool:
    if len(levels) < 2:
        return False
    first, second = levels[0][0], levels[1][0]
    if side == "buy":
        return second > 0 and first > second * 1.12
    return first > 0 and second > first * 1.12


def _bazaar_reason(side: str, minutes: float | None, depth: float, huge_wall: bool, missing_book: bool, low_movement: bool, suspicious: bool, stale_cache: bool) -> str:
    action = "buy fill" if side == "buy" else "sell fill"
    if missing_book:
        return f"missing order book; {action} speed cannot be trusted"
    if minutes is None:
        return f"missing movement data; {action} speed cannot be estimated"
    parts = [f"estimated {action} {_minutes_text(minutes)}"]
    if huge_wall:
        parts.append("top order wall too large")
    if low_movement:
        parts.append("low weekly movement")
    if suspicious:
        parts.append("top price looks like an outlier")
    if stale_cache:
        parts.append("cache is stale")
    if not any([huge_wall, low_movement, suspicious, stale_cache]):
        parts.append("no major walls")
    parts.append(f"depth ahead {_compact(depth)}")
    return ", ".join(parts)


def _minutes_text(minutes: float | None) -> str:
    if minutes is None:
        return "unknown"
    if minutes < 60:
        return f"~{minutes:.0f}m"
    return f"~{minutes / 60:.1f}h"


def _compact(value: float) -> str:
    absolute = abs(value)
    if absolute >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if absolute >= 1_000:
        return f"{value / 1_000:.1f}k"
    return f"{value:.0f}"
