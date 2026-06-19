from __future__ import annotations

from dataclasses import dataclass, field

from .market_speed import normalize_ah_speed
from .pricing import AH_FEE_RATE, CraftCost, MarketMetrics
from .recipes import Eligibility, Recipe


@dataclass(frozen=True)
class AnalyzerConfig:
    budget: float
    min_profit: float = 5_000
    min_profit_percent: float = 4
    min_sales_per_day: float = 2
    max_median_sell_time_hours: float = 12
    max_craft_cost: float | None = None
    max_capital_percent_per_flip: float = 35.0
    limit: int = 30
    min_spread_profit_per_unit: float = 0.0
    min_spread_volume_week: float = 25_000.0
    max_spread_depth_ratio: float = 1.25
    spread_limit: int | None = None
    bazaar_fee_rate: float = 0.0125
    max_estimated_buy_minutes: float | None = None
    max_estimated_sell_minutes: float | None = None
    max_estimated_bottleneck_minutes: float = 240.0
    min_speed_confidence: float = 35.0
    conservative_speed: bool = True


@dataclass(frozen=True)
class Opportunity:
    recipe: Recipe
    eligibility: Eligibility
    craft_cost: CraftCost
    market: MarketMetrics
    safe_sell_price: float
    estimated_profit: float
    profit_percent: float
    score: float
    confidence: float
    speed_label: str
    max_batch_size: int
    suggested_listing_price: float
    reasons: list[str]
    risks: list[str]
    rejected: bool = False
    rejection_reasons: list[str] = field(default_factory=list)


def evaluate_opportunity(
    recipe: Recipe,
    eligibility: Eligibility,
    craft_cost: CraftCost,
    market: MarketMetrics,
    config: AnalyzerConfig,
) -> Opportunity:
    rejection_reasons: list[str] = []
    reasons: list[str] = []
    risks: list[str] = list(recipe.risk_tags)

    if not eligibility.eligible:
        rejection_reasons.extend(eligibility.missing)
    if craft_cost.unavailable:
        rejection_reasons.extend(craft_cost.unavailable)

    if market.safe_sell_price is None:
        rejection_reasons.append("safe sell price could not be estimated reliably")
        safe_sell = 0.0
    else:
        safe_sell = market.safe_sell_price

    net_sell = safe_sell * (1 - AH_FEE_RATE)
    profit = net_sell - craft_cost.per_output_cost
    profit_percent = (profit / craft_cost.per_output_cost * 100) if craft_cost.per_output_cost > 0 else 0.0

    if config.max_craft_cost is not None and craft_cost.per_output_cost > config.max_craft_cost:
        rejection_reasons.append(f"craft cost exceeds max craft cost {config.max_craft_cost:,.0f}")

    safe_capital = config.budget * (config.max_capital_percent_per_flip / 100)
    if craft_cost.per_output_cost > config.budget:
        rejection_reasons.append("craft cost exceeds available budget")
    elif craft_cost.per_output_cost > safe_capital and not (
        profit_percent >= max(30, config.min_profit_percent + 20)
        and market.analysis.sales_per_day >= config.min_sales_per_day * 3
    ):
        rejection_reasons.append("craft cost is too large relative to budget")

    if profit < config.min_profit:
        rejection_reasons.append(f"profit below {config.min_profit:,.0f}")
    if profit_percent < config.min_profit_percent:
        rejection_reasons.append(f"profit percent below {config.min_profit_percent:g}%")
    if market.analysis.sales_per_day < config.min_sales_per_day:
        rejection_reasons.append(f"sales/day below {config.min_sales_per_day:g}")

    median_time = market.analysis.median_sell_time_hours
    if median_time is not None and median_time > config.max_median_sell_time_hours:
        exceptional = profit_percent >= config.min_profit_percent + 25 and market.analysis.sales_per_day >= config.min_sales_per_day * 4
        if not exceptional:
            rejection_reasons.append(f"median sell time above {config.max_median_sell_time_hours:g}h")

    if market.active.active_count > 50 and market.active.active_count > market.analysis.sales_per_day * 5:
        rejection_reasons.append("active listings are high relative to daily sales")

    if market.confidence_score < 0.35:
        rejection_reasons.append("market data confidence is too low")

    if profit_percent > 500 and market.volatility >= 0.6:
        rejection_reasons.append("profit is anomalous for a volatile market")
    if profit_percent > 500 and market.manipulation_risk_score >= 0.35 and market.active.active_count < 10:
        rejection_reasons.append("profit is anomalous with manipulation risk")

    score = _score(profit, profit_percent, craft_cost.per_output_cost, market, eligibility, config)
    batch = batch_size(craft_cost.per_output_cost, market, config.budget, score, config.max_capital_percent_per_flip)
    if batch <= 0 and not rejection_reasons:
        rejection_reasons.append("capital lock risk too high for this budget")

    if profit > 0:
        reasons.append(f"expected net profit {profit:,.0f} coins after estimated AH fee")
    if market.analysis.sales_per_day:
        reasons.append(f"{market.analysis.sales_per_day:.1f} sales/day over market window")
    if eligibility.reasons:
        reasons.append(f"unlocked: {eligibility.reasons[0]}")

    if market.volatility >= 0.5:
        risks.append("volatile_price")
    if market.price_wall_score >= 0.6:
        risks.append("price_wall")
    if market.confidence_score < 0.6:
        risks.append("thin_market_data")
    if craft_cost.per_output_cost > config.budget * 0.2:
        risks.append("large_budget_share")

    return Opportunity(
        recipe=recipe,
        eligibility=eligibility,
        craft_cost=craft_cost,
        market=market,
        safe_sell_price=safe_sell,
        estimated_profit=profit,
        profit_percent=profit_percent,
        score=score,
        confidence=market.confidence_score * 100,
        speed_label=normalize_ah_speed(
            sales_per_day=market.analysis.sales_per_day,
            median_sell_time_hours=market.analysis.median_sell_time_hours,
            sold_sample_count=max(market.analysis.total_sales, market.sold.sale_count),
            active_bin_count=market.active.active_count,
        ).risk_label,
        max_batch_size=batch,
        suggested_listing_price=market.suggested_listing_price or safe_sell,
        reasons=reasons,
        risks=dedupe(risks),
        rejected=bool(rejection_reasons),
        rejection_reasons=dedupe(rejection_reasons),
    )


def batch_size(craft_cost: float, market: MarketMetrics, budget: float, score: float, max_capital_percent: float = 35.0) -> int:
    if craft_cost <= 0 or craft_cost > budget:
        return 0
    safe_capital = budget * (max_capital_percent / 100)
    if craft_cost > safe_capital:
        return 0
    affordable = int(budget // craft_cost)
    affordable = min(affordable, int(safe_capital // craft_cost))
    if craft_cost > safe_capital * 0.72 and score < 85:
        return 0
    if craft_cost > safe_capital * 0.57:
        return min(1, affordable)

    sales = market.analysis.sales_per_day
    median_hours = market.analysis.median_sell_time_hours or 24
    active = market.active.active_count
    if median_hours <= 2 and sales >= 20 and active < sales * 3:
        cap = 10
    elif median_hours <= 8 and sales >= 5:
        cap = 3
    else:
        cap = 1
    return max(0, min(affordable, cap))


def _score(
    profit: float,
    profit_percent: float,
    craft_cost: float,
    market: MarketMetrics,
    eligibility: Eligibility,
    config: AnalyzerConfig,
) -> float:
    speed = normalize_ah_speed(
        sales_per_day=market.analysis.sales_per_day,
        median_sell_time_hours=market.analysis.median_sell_time_hours,
        sold_sample_count=max(market.analysis.total_sales, market.sold.sale_count),
        active_bin_count=market.active.active_count,
    )
    profit_score = min(30.0, max(0.0, profit / 200_000 * 15 + profit_percent / 50 * 15))
    speed_score = speed.speed_score * 0.30
    confidence_score = min(20.0, min(market.confidence_score * 100, speed.confidence_score) * 0.20)
    budget_score = max(0.0, 10.0 * (1 - craft_cost / max(1.0, config.budget * (config.max_capital_percent_per_flip / 100))))
    competition_score = max(0.0, 10.0 * (1 - market.price_wall_score))

    risk_penalty = 0.0
    risk_penalty += market.volatility * 6
    risk_penalty += market.price_wall_score * 6
    risk_penalty += market.manipulation_risk_score * 8
    if market.active.active_count > market.analysis.sales_per_day * 5:
        risk_penalty += 8
    if craft_cost > config.budget * 0.2:
        risk_penalty += 6
    if eligibility.confidence < 1:
        risk_penalty += (1 - eligibility.confidence) * 8

    score = profit_score + speed_score + confidence_score + budget_score + competition_score - risk_penalty
    return max(0.0, min(100.0, score))


def score_generic_opportunity(
    *,
    profit: float,
    profit_percent: float,
    speed_score: float,
    confidence_score: float,
    budget_fit_score: float,
    competition_score: float,
    risk_penalty: float = 0.0,
    effort_penalty: float = 0.0,
) -> float:
    profit_quality = min(100.0, max(0.0, profit / 200_000 * 50 + profit_percent / 50 * 50))
    score = (
        profit_quality * 0.30
        + max(0.0, min(100.0, speed_score)) * 0.30
        + max(0.0, min(100.0, confidence_score)) * 0.20
        + max(0.0, min(100.0, budget_fit_score)) * 0.10
        + max(0.0, min(100.0, competition_score)) * 0.10
        - risk_penalty
        - effort_penalty
    )
    return max(0.0, min(100.0, score))


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result
