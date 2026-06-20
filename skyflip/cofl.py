from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from .http import ApiError, HttpClient


BASE_URL = "https://sky.coflnet.com/api"


@dataclass(frozen=True)
class SoldSummary:
    median_price: float | None = None
    mean_price: float | None = None
    sale_count: int = 0
    min_price: float | None = None
    max_price: float | None = None
    confidence: float = 0.0


@dataclass(frozen=True)
class MarketAnalysis:
    total_sales: int = 0
    sales_per_day: float = 0.0
    average_price: float | None = None
    median_price: float | None = None
    average_sell_time_hours: float | None = None
    median_sell_time_hours: float | None = None
    price_std_dev: float | None = None
    coeff_variation: float | None = None
    bin_percentage: float | None = None
    sell_speed_buckets: list[dict[str, Any]] = field(default_factory=list)
    hourly_breakdown: list[dict[str, Any]] = field(default_factory=list)
    source: str = "none"


@dataclass(frozen=True)
class ActiveAuctions:
    prices: list[float] = field(default_factory=list)
    active_count: int = 0
    lowest_bin: float | None = None
    second_lowest_bin: float | None = None
    third_lowest_bin: float | None = None
    source: str = "none"


class CoflClient:
    def __init__(self, http: HttpClient) -> None:
        self.http = http
        self.warnings: list[str] = []
        self._unsupported_tags: set[str] = set()
        self._rate_limited = False

    def analysis(self, tag: str, days: int) -> MarketAnalysis | None:
        if self._should_skip(tag):
            return None
        url = f"{BASE_URL}/item/price/{quote(tag)}/analysis?days={days}"
        try:
            result = self.http.get_json(url)
        except ApiError as exc:
            self._record_failure("analysis", tag, exc)
            return None
        payload = result.payload if isinstance(result.payload, dict) else {}
        return normalize_analysis(payload, source=result.source)

    def active_bins(self, tag: str) -> ActiveAuctions:
        if self._should_skip(tag):
            return ActiveAuctions()
        url = f"{BASE_URL}/auctions/tag/{quote(tag)}/active/bin"
        try:
            result = self.http.get_json(url)
            active = normalize_active(result.payload, source=f"{result.source}:active/bin")
            if active.active_count:
                return active
        except ApiError as exc:
            self._record_failure("active/bin", tag, exc)
            if tag in self._unsupported_tags or self._rate_limited:
                return ActiveAuctions()

        overview_url = f"{BASE_URL}/auctions/tag/{quote(tag)}/active/overview?orderBy=LOWEST_PRICE"
        try:
            result = self.http.get_json(overview_url)
            return normalize_active(result.payload, source=f"{result.source}:active/overview")
        except ApiError as exc:
            self._record_failure("active overview", tag, exc)
            return ActiveAuctions()

    def sold_summary(self, tag: str) -> SoldSummary:
        if self._should_skip(tag):
            return SoldSummary()
        url = f"{BASE_URL}/auctions/tag/{quote(tag)}/sold?page=0&pageSize=100"
        try:
            result = self.http.get_json(url)
        except ApiError as exc:
            self._record_failure("sold auctions", tag, exc)
            return SoldSummary()
        return normalize_sold(result.payload)

    def bazaar_snapshot_price(self, tag: str) -> float | None:
        if self._should_skip(tag):
            return None
        url = f"{BASE_URL}/bazaar/{quote(tag)}/snapshot"
        try:
            result = self.http.get_json(url)
        except ApiError as exc:
            self._record_failure("Bazaar snapshot", tag, exc)
            return None
        payload = result.payload
        if not isinstance(payload, dict):
            return None
        for key in ("sellPrice", "buyPrice", "price", "lowestBin"):
            value = payload.get(key)
            if isinstance(value, (int, float)) and value > 0:
                return float(value)
        return None

    def _should_skip(self, tag: str) -> bool:
        return self._rate_limited or tag in self._unsupported_tags

    def _record_failure(self, operation: str, tag: str, exc: ApiError) -> None:
        text = str(exc)
        if "429" in text:
            if not self._rate_limited:
                self.warnings.append(f"SkyCofl {operation} unavailable for {tag}: {exc}")
            self._rate_limited = True
            return
        if "400" in text or "Bad Request" in text:
            self._unsupported_tags.add(tag)
        self.warnings.append(f"SkyCofl {operation} unavailable for {tag}: {exc}")


def normalize_analysis(payload: dict[str, Any], *, source: str = "live") -> MarketAnalysis:
    total_sales = int(payload.get("totalSales") or payload.get("sales") or payload.get("count") or 0)
    avg_seconds = _float_or_none(payload.get("avgSellTimeSeconds") or payload.get("averageSellTimeSeconds"))
    median_seconds = _float_or_none(payload.get("medianSellTimeSeconds"))
    return MarketAnalysis(
        total_sales=total_sales,
        sales_per_day=float(payload.get("salesPerDay") or 0),
        average_price=_float_or_none(payload.get("avgPrice") or payload.get("averagePrice")),
        median_price=_float_or_none(payload.get("medianPrice")),
        average_sell_time_hours=avg_seconds / 3600 if avg_seconds is not None else None,
        median_sell_time_hours=median_seconds / 3600 if median_seconds is not None else None,
        price_std_dev=_float_or_none(payload.get("priceStdDev")),
        coeff_variation=_float_or_none(payload.get("priceCoeffVariation")),
        bin_percentage=_float_or_none(payload.get("binPercentage")),
        sell_speed_buckets=list(payload.get("sellSpeedBuckets") or []),
        hourly_breakdown=list(payload.get("hourlyBreakdown") or []),
        source=source,
    )


def normalize_active(payload: Any, *, source: str = "live") -> ActiveAuctions:
    rows = payload.get("auctions") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return ActiveAuctions(source=source)
    prices: list[float] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        value = _float_or_none(
            row.get("price")
            or row.get("startingBid")
            or row.get("starting_bid")
            or row.get("lowestBin")
            or row.get("highestBidAmount")
            or row.get("highest_bid_amount")
        )
        if value is not None and value > 0:
            prices.append(value)
    prices.sort()
    return ActiveAuctions(
        prices=prices,
        active_count=len(prices),
        lowest_bin=_nth(prices, 0),
        second_lowest_bin=_nth(prices, 1),
        third_lowest_bin=_nth(prices, 2),
        source=source,
    )


def normalize_sold(payload: Any) -> SoldSummary:
    rows = payload.get("auctions") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return SoldSummary()
    prices: list[float] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        value = row.get("highestBidAmount") or row.get("price") or row.get("starting_bid")
        price = _float_or_none(value)
        if price is not None and price > 0:
            prices.append(price)
    if not prices:
        return SoldSummary()
    return SoldSummary(
        median_price=statistics.median(prices),
        mean_price=statistics.fmean(prices),
        sale_count=len(prices),
        min_price=min(prices),
        max_price=max(prices),
        confidence=min(1.0, len(prices) / 30),
    )


def _nth(values: list[float], index: int) -> float | None:
    return values[index] if len(values) > index else None


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
