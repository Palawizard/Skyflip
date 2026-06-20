from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .bazaar import BazaarClient
from .bazaar_order import BAZAAR_TAX_RATE
from .market_speed import SpeedResult, estimate_bazaar_order_fill_speed, normalize_conversion_speed
from .models import RejectedItem
from .scoring import AnalyzerConfig, score_generic_opportunity


@dataclass(frozen=True)
class BazaarConversion:
    name: str
    input_product_id: str
    input_amount: float
    output_product_id: str
    output_amount: float
    conversion_type: str
    craftable_manually: bool
    manual_craft_operations: int = 1
    manual_effort: str = "low"
    requires_collection: dict[str, int] | None = None
    verified: bool = False
    confidence: str = "medium"
    disabled: bool = False
    disabled_reason: str = ""
    requires_manual_verification: bool = False
    notes: str = ""


@dataclass(frozen=True)
class ConversionFlip:
    name: str
    input_shopping_list: str
    output: str
    input_cost: float
    output_value: float
    profit: float
    profit_percent: float
    output_moving_week: float
    input_acquisition_speed: SpeedResult
    output_sale_speed: SpeedResult
    bottleneck_speed: SpeedResult
    suggested_batch_size: int
    risk: str
    reason: str
    score: float
    manual_action: str

    @property
    def estimated_buy_fill_minutes(self) -> float | None:
        return self.input_acquisition_speed.estimated_minutes

    @property
    def estimated_sell_fill_minutes(self) -> float | None:
        return self.output_sale_speed.estimated_minutes

    @property
    def estimated_bottleneck_minutes(self) -> float | None:
        return self.bottleneck_speed.estimated_minutes

    @property
    def buy_speed_label(self) -> str:
        return self.input_acquisition_speed.risk_label

    @property
    def sell_speed_label(self) -> str:
        return self.output_sale_speed.risk_label

    @property
    def bottleneck_speed_label(self) -> str:
        return self.bottleneck_speed.risk_label

    @property
    def speed_confidence(self) -> float:
        return self.bottleneck_speed.confidence_score

    @property
    def speed_reason(self) -> str:
        return self.bottleneck_speed.reason

    @property
    def depth_ahead_buy(self) -> float:
        return self.input_acquisition_speed.depth_ahead

    @property
    def depth_ahead_sell(self) -> float:
        return self.output_sale_speed.depth_ahead


def load_conversions(path: Path | str = "data/bazaar_conversions.json") -> list[BazaarConversion]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    conversions: list[BazaarConversion] = []
    for item in raw.get("conversions", []):
        if item.get("disabled"):
            continue
        conversions.append(
            BazaarConversion(
                name=item.get("name") or f"{item['input_product_id']} -> {item['output_product_id']}",
                input_product_id=item["input_product_id"],
                input_amount=float(item["input_amount"]),
                output_product_id=item["output_product_id"],
                output_amount=float(item.get("output_amount", 1)),
                conversion_type=item.get("conversion_type", "compression"),
                craftable_manually=bool(item.get("craftable_manually", True)),
                manual_craft_operations=int(item.get("manual_craft_operations", 1) or 1),
                manual_effort=str(item.get("manual_effort", "low")).lower(),
                requires_collection={str(k).upper(): int(v) for k, v in (item.get("requires_collection") or {}).items()},
                verified=bool(item.get("verified", False)),
                confidence=str(item.get("confidence", "medium")).lower(),
                disabled=bool(item.get("disabled", False)),
                disabled_reason=str(item.get("disabled_reason", "")),
                requires_manual_verification=bool(item.get("requires_manual_verification", False)),
                notes=item.get("notes", ""),
            )
        )
    return conversions


def find_conversion_flips(
    bazaar: BazaarClient,
    config: AnalyzerConfig,
    conversions: list[BazaarConversion],
    *,
    mode: str = "realistic",
) -> tuple[list[ConversionFlip], list[RejectedItem]]:
    accepted: list[ConversionFlip] = []
    rejected: list[RejectedItem] = []
    for conversion in conversions:
        flip = evaluate_conversion(conversion, bazaar, config, mode=mode)
        if isinstance(flip, ConversionFlip):
            accepted.append(flip)
        else:
            rejected.append(flip)
    accepted.sort(key=lambda item: (item.score, item.bottleneck_speed.speed_score, item.profit), reverse=True)
    return accepted[: config.limit], rejected


def evaluate_conversion(
    conversion: BazaarConversion,
    bazaar: BazaarClient,
    config: AnalyzerConfig,
    *,
    mode: str = "realistic",
) -> ConversionFlip | RejectedItem:
    rejection: list[str] = []
    input_product = bazaar.product_metrics(conversion.input_product_id)
    output_product = bazaar.product_metrics(conversion.output_product_id)
    if input_product is None or output_product is None:
        return RejectedItem("bazaar-compression", conversion.name, "missing Bazaar product")

    if mode == "conservative":
        input_unit = input_product.sell_price
        output_unit = output_product.buy_price * (1 - BAZAAR_TAX_RATE)
    else:
        input_unit = input_product.best_buy_order or input_product.buy_price
        output_unit = (output_product.best_sell_offer or output_product.sell_price) * (1 - BAZAAR_TAX_RATE)

    if input_unit <= 0 or output_unit <= 0:
        return RejectedItem("bazaar-compression", conversion.name, "missing usable input/output price")

    input_cost = input_unit * conversion.input_amount
    output_value = output_unit * conversion.output_amount
    profit = output_value - input_cost
    profit_percent = profit / input_cost * 100 if input_cost > 0 else 0.0

    input_speed = estimate_bazaar_order_fill_speed(
        side="buy",
        recommended_price=input_unit,
        order_summary=input_product.buy_summary,
        moving_week=input_product.sell_moving_week,
        live_volume=input_product.sell_volume,
        depth_ahead=_depth_at_or_above(input_product.buy_summary, input_unit) or input_product.top_buy_order_depth,
        order_size=conversion.input_amount,
    )
    output_speed = estimate_bazaar_order_fill_speed(
        side="sell",
        recommended_price=output_unit / max(1 - BAZAAR_TAX_RATE, 0.0001),
        order_summary=output_product.sell_summary,
        moving_week=output_product.buy_moving_week,
        live_volume=output_product.buy_volume,
        depth_ahead=_depth_at_or_below(output_product.sell_summary, output_unit / max(1 - BAZAAR_TAX_RATE, 0.0001)) or output_product.top_sell_offer_depth,
        order_size=conversion.output_amount,
    )
    speed = normalize_conversion_speed([input_speed], output_speed)
    bottleneck_volume = min(input_product.buy_moving_week, input_product.sell_moving_week, output_product.buy_moving_week, output_product.sell_moving_week) / 7
    safe_capital = config.budget * (config.max_capital_percent_per_flip / 100)
    batch_by_budget = int(safe_capital // input_cost)
    batch_by_volume = max(1, int(bottleneck_volume * 0.005))
    batch = max(0, min(batch_by_budget, batch_by_volume, 64))
    if batch > 0:
        input_speed = estimate_bazaar_order_fill_speed(
            side="buy",
            recommended_price=input_unit,
            order_summary=input_product.buy_summary,
            moving_week=input_product.sell_moving_week,
            live_volume=input_product.sell_volume,
            depth_ahead=_depth_at_or_above(input_product.buy_summary, input_unit) or input_product.top_buy_order_depth,
            order_size=conversion.input_amount * batch,
        )
        output_speed = estimate_bazaar_order_fill_speed(
            side="sell",
            recommended_price=output_unit / max(1 - BAZAAR_TAX_RATE, 0.0001),
            order_summary=output_product.sell_summary,
            moving_week=output_product.buy_moving_week,
            live_volume=output_product.buy_volume,
            depth_ahead=_depth_at_or_below(output_product.sell_summary, output_unit / max(1 - BAZAAR_TAX_RATE, 0.0001)) or output_product.top_sell_offer_depth,
            order_size=conversion.output_amount * batch,
        )
        speed = normalize_conversion_speed([input_speed], output_speed)
    total_profit = profit * batch

    if not conversion.craftable_manually:
        rejection.append("conversion is not marked manually craftable")
    if profit < config.min_profit and total_profit < config.min_profit:
        rejection.append(f"profit below {config.min_profit:,.0f}")
    if profit_percent < config.min_profit_percent:
        rejection.append(f"profit percent below {config.min_profit_percent:g}%")
    if bottleneck_volume < max(500, config.min_sales_per_day * 250):
        rejection.append("input/output moving volume too low")
    if output_speed.speed_score < 45:
        rejection.append("output sale speed is bad")
    if speed.estimated_hours is not None and speed.estimated_hours > config.max_median_sell_time_hours:
        rejection.append("expected fill/sell time too long")
    if speed.estimated_minutes is None or speed.estimated_minutes > _max_estimated_bottleneck_minutes(config):
        rejection.append(f"estimated bottleneck above {_max_estimated_bottleneck_minutes(config):g}m")
    if speed.confidence_score < _min_speed_confidence(config):
        rejection.append("speed confidence is too low")
    if batch <= 0:
        rejection.append("conversion requires too much capital")
    if output_product.top_sell_offer_depth > bottleneck_volume * 0.75 and bottleneck_volume > 0:
        rejection.append("output spread depends on thin or crowded orders")

    budget_fit = max(0.0, min(100.0, 100.0 * (1 - input_cost * max(batch, 1) / max(1.0, safe_capital))))
    competition = max(0.0, min(100.0, 100.0 - output_product.top_sell_offer_depth / max(1.0, bottleneck_volume) * 100))
    effort_penalty = _manual_effort_penalty(conversion)
    score = score_generic_opportunity(
        profit=total_profit,
        profit_percent=profit_percent,
        speed_score=speed.speed_score,
        confidence_score=speed.confidence_score,
        budget_fit_score=budget_fit,
        competition_score=competition,
        effort_penalty=effort_penalty,
    )

    if rejection:
        return RejectedItem("bazaar-compression", conversion.name, " and ".join(rejection[:3]))

    reason = f"{speed.reason}; {conversion.conversion_type}"
    manual = (
        f"Suggested manual action: buy {conversion.input_amount * batch:,.0f}x {conversion.input_product_id}, "
        f"manually convert to {conversion.output_amount * batch:,.0f}x {conversion.output_product_id}, then sell manually."
    )
    return ConversionFlip(
        name=conversion.name,
        input_shopping_list=f"{conversion.input_amount * batch:,.0f}x {conversion.input_product_id}",
        output=f"{conversion.output_amount * batch:,.0f}x {conversion.output_product_id}",
        input_cost=input_cost * batch,
        output_value=output_value * batch,
        profit=total_profit,
        profit_percent=profit_percent,
        output_moving_week=output_product.sell_moving_week,
        input_acquisition_speed=input_speed,
        output_sale_speed=output_speed,
        bottleneck_speed=speed,
        suggested_batch_size=batch,
        risk=speed.risk_label,
        reason=reason,
        score=score,
        manual_action=manual,
    )


def _depth_at_or_above(summary: tuple[dict[str, float], ...], price: float) -> float:
    return sum(float(item.get("amount") or 0.0) for item in summary if float(item.get("pricePerUnit") or 0.0) >= price)


def _depth_at_or_below(summary: tuple[dict[str, float], ...], price: float) -> float:
    return sum(float(item.get("amount") or 0.0) for item in summary if float(item.get("pricePerUnit") or 0.0) <= price)


def _max_estimated_bottleneck_minutes(config: AnalyzerConfig) -> float:
    return float(getattr(config, "max_estimated_bottleneck_minutes", 240.0) or 240.0)


def _min_speed_confidence(config: AnalyzerConfig) -> float:
    return float(getattr(config, "min_speed_confidence", 35.0) or 0.0)


def _manual_effort_penalty(conversion: BazaarConversion) -> float:
    base = {"low": 4.0, "medium": 10.0, "high": 18.0}.get(conversion.manual_effort, 8.0)
    operations = max(1, conversion.manual_craft_operations)
    confidence = 0.0
    if conversion.confidence == "medium":
        confidence = 4.0
    elif conversion.confidence == "low" or conversion.requires_manual_verification:
        confidence = 12.0
    return base + min(12.0, operations * 1.5) + confidence
