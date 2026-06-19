from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .http import ApiError, HttpClient


BAZAAR_URL = "https://api.hypixel.net/v2/skyblock/bazaar"


@dataclass(frozen=True)
class BazaarPrice:
    tag: str
    unit_price: float
    source_field: str
    available: bool = True


@dataclass(frozen=True)
class BazaarProduct:
    tag: str
    buy_price: float
    sell_price: float
    buy_volume: float
    sell_volume: float
    buy_moving_week: float
    sell_moving_week: float
    best_buy_order: float | None
    best_sell_offer: float | None
    top_buy_order_depth: float
    top_sell_offer_depth: float
    buy_summary: tuple[dict[str, float], ...] = ()
    sell_summary: tuple[dict[str, float], ...] = ()


class BazaarClient:
    def __init__(self, http: HttpClient) -> None:
        self.http = http
        self._products: dict[str, Any] | None = None
        self.warnings: list[str] = []
        self.last_source: str | None = None

    def products(self) -> dict[str, Any]:
        if self._products is not None:
            return self._products
        try:
            result = self.http.get_json(BAZAAR_URL)
        except ApiError as exc:
            self.warnings.append(f"Hypixel Bazaar unavailable: {exc}")
            self._products = {}
            return self._products
        self.last_source = result.source
        payload = result.payload if isinstance(result.payload, dict) else {}
        self._products = payload.get("products", {}) or {}
        return self._products

    def price_for(self, tag: str, *, use_buy_order_cost: bool = False) -> BazaarPrice | None:
        product = self.products().get(tag)
        if not isinstance(product, dict):
            return None

        if use_buy_order_cost:
            value = _summary_price(product.get("buy_summary")) or _quick_price(product, "buyPrice")
            field = "buy_summary.pricePerUnit/quick_status.buyPrice"
        else:
            value = _summary_price(product.get("sell_summary")) or _quick_price(product, "sellPrice")
            field = "sell_summary.pricePerUnit/quick_status.sellPrice"

        if value is None or value <= 0:
            return None
        return BazaarPrice(tag=tag, unit_price=float(value), source_field=field)

    def product_metrics(self, tag: str) -> BazaarProduct | None:
        product = self.products().get(tag)
        if not isinstance(product, dict):
            return None
        quick = product.get("quick_status")
        if not isinstance(quick, dict):
            quick = {}
        buy_summary = product.get("buy_summary")
        sell_summary = product.get("sell_summary")
        buy_price = _quick_price(product, "buyPrice") or 0.0
        sell_price = _quick_price(product, "sellPrice") or 0.0
        return BazaarProduct(
            tag=tag,
            buy_price=buy_price,
            sell_price=sell_price,
            buy_volume=float(quick.get("buyVolume") or 0),
            sell_volume=float(quick.get("sellVolume") or 0),
            buy_moving_week=float(quick.get("buyMovingWeek") or 0),
            sell_moving_week=float(quick.get("sellMovingWeek") or 0),
            best_buy_order=_summary_price(buy_summary),
            best_sell_offer=_summary_price(sell_summary),
            top_buy_order_depth=_summary_amount(buy_summary),
            top_sell_offer_depth=_summary_amount(sell_summary),
            buy_summary=_clean_summary(buy_summary),
            sell_summary=_clean_summary(sell_summary),
        )


def _summary_price(summary: Any) -> float | None:
    if isinstance(summary, list) and summary:
        first = summary[0]
        if isinstance(first, dict):
            value = first.get("pricePerUnit")
            if value is not None:
                return float(value)
    return None


def _summary_amount(summary: Any) -> float:
    if isinstance(summary, list) and summary:
        first = summary[0]
        if isinstance(first, dict):
            for key in ("amount", "orders", "amountLeft"):
                value = first.get(key)
                if value is not None:
                    return float(value)
    return 0.0


def _clean_summary(summary: Any) -> tuple[dict[str, float], ...]:
    if not isinstance(summary, list):
        return ()
    levels: list[dict[str, float]] = []
    for item in summary:
        if not isinstance(item, dict):
            continue
        price = item.get("pricePerUnit")
        if price is None:
            continue
        amount = 0.0
        for key in ("amount", "orders", "amountLeft"):
            value = item.get(key)
            if value is not None:
                amount = float(value)
                break
        levels.append({"pricePerUnit": float(price), "amount": amount})
    return tuple(levels)


def _quick_price(product: dict[str, Any], key: str) -> float | None:
    quick = product.get("quick_status")
    if not isinstance(quick, dict):
        return None
    value = quick.get(key)
    return float(value) if value is not None else None
