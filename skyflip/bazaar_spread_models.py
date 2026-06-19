from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .market_speed import SpeedResult


BAZAAR_FEE_RATE = 0.0125
MIN_BOTTLENECK_SPEED = 25.0
HISTORY_FILE = Path(".skyflip") / "bazaar_spread_history.json"
HISTORY_TTL_SECONDS = 60 * 60 * 6
MAX_HISTORY_PRODUCTS = 250
MAX_HISTORY_RECORDS_PER_PRODUCT = 40
RISK_RULES_FILE = Path("data") / "bazaar_risk_rules.json"
RISK_ORDER = {"Low": 0, "Medium": 1, "High": 2}


@dataclass(frozen=True)
class BazaarSpreadPricing:
    best_buy_order_price: float
    second_best_buy_order_price: float | None
    best_sell_offer_price: float
    second_best_sell_offer_price: float | None
    realistic_buy_price: float
    realistic_sell_price: float
    raw_spread: float
    fee_rate: float
    net_sell_price: float
    net_profit_per_unit: float
    profit_percent: float


@dataclass(frozen=True)
class BazaarOrderBookStats:
    buy_order_depth: float
    sell_offer_depth: float
    buy_moving_week: float
    sell_moving_week: float
    buy_volume: float
    sell_volume: float
    depth_at_or_above_buy_price: float
    depth_at_or_below_sell_price: float
    estimated_buy_fill_speed: SpeedResult
    estimated_sell_fill_speed: SpeedResult
    bottleneck_speed: SpeedResult
    competition_score: float
    manipulation_risk: float
    confidence_score: float
    history_seen_count: int = 0
    history_stability_score: float = 0.0


@dataclass(frozen=True)
class BazaarSpreadOpportunity:
    product_id: str
    pricing: BazaarSpreadPricing
    order_book: BazaarOrderBookStats
    suggested_order_size: int
    estimated_total_profit: float
    capital_required: float
    profit_per_minute: float
    coins_per_hour: float
    final_score: float
    risk: str
    reason: str
    manual_action: str
    suggested_full_size: int
    suggested_test_size: int
    should_test_first: bool

    @property
    def best_buy_order_price(self) -> float:
        return self.pricing.best_buy_order_price

    @property
    def second_best_buy_order_price(self) -> float | None:
        return self.pricing.second_best_buy_order_price

    @property
    def best_sell_offer_price(self) -> float:
        return self.pricing.best_sell_offer_price

    @property
    def second_best_sell_offer_price(self) -> float | None:
        return self.pricing.second_best_sell_offer_price

    @property
    def realistic_buy_price(self) -> float:
        return self.pricing.realistic_buy_price

    @property
    def realistic_sell_price(self) -> float:
        return self.pricing.realistic_sell_price

    @property
    def raw_spread(self) -> float:
        return self.pricing.raw_spread

    @property
    def net_profit_per_unit(self) -> float:
        return self.pricing.net_profit_per_unit

    @property
    def profit_percent(self) -> float:
        return self.pricing.profit_percent

    @property
    def buy_order_depth(self) -> float:
        return self.order_book.buy_order_depth

    @property
    def sell_offer_depth(self) -> float:
        return self.order_book.sell_offer_depth

    @property
    def buy_moving_week(self) -> float:
        return self.order_book.buy_moving_week

    @property
    def sell_moving_week(self) -> float:
        return self.order_book.sell_moving_week

    @property
    def buy_volume(self) -> float:
        return self.order_book.buy_volume

    @property
    def sell_volume(self) -> float:
        return self.order_book.sell_volume

    @property
    def estimated_buy_fill_speed(self) -> SpeedResult:
        return self.order_book.estimated_buy_fill_speed

    @property
    def estimated_sell_fill_speed(self) -> SpeedResult:
        return self.order_book.estimated_sell_fill_speed

    @property
    def bottleneck_speed(self) -> SpeedResult:
        return self.order_book.bottleneck_speed

    @property
    def competition_score(self) -> float:
        return self.order_book.competition_score

    @property
    def manipulation_risk(self) -> float:
        return self.order_book.manipulation_risk

    @property
    def confidence_score(self) -> float:
        return self.order_book.confidence_score

    @property
    def estimated_buy_fill_minutes(self) -> float | None:
        return self.estimated_buy_fill_speed.estimated_minutes

    @property
    def estimated_sell_fill_minutes(self) -> float | None:
        return self.estimated_sell_fill_speed.estimated_minutes

    @property
    def estimated_bottleneck_minutes(self) -> float | None:
        return self.bottleneck_speed.estimated_minutes

    @property
    def buy_speed_label(self) -> str:
        return self.estimated_buy_fill_speed.risk_label

    @property
    def sell_speed_label(self) -> str:
        return self.estimated_sell_fill_speed.risk_label

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
        return self.order_book.depth_at_or_above_buy_price

    @property
    def depth_ahead_sell(self) -> float:
        return self.order_book.depth_at_or_below_sell_price


@dataclass(frozen=True)
class _BookLevel:
    price: float
    amount: float


@dataclass(frozen=True)
class _SpreadCandidate:
    pricing: BazaarSpreadPricing
    size: int
    buy_speed: SpeedResult
    sell_speed: SpeedResult
    bottleneck: SpeedResult
    estimated_profit: float
    capital_required: float
    profit_per_minute: float
    coins_per_hour: float
    depth_at_buy: float
    depth_at_sell: float
    manipulation_risk: float
    warnings: list[str]


@dataclass(frozen=True)
class BazaarRiskRules:
    volatile_prefixes: tuple[str, ...]
    volatile_contains: tuple[str, ...]
    min_risk_by_pattern: dict[str, str]
