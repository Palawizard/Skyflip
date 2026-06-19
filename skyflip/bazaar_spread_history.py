from __future__ import annotations

import json
import time
from pathlib import Path

from .bazaar_spread_models import (
    HISTORY_FILE,
    HISTORY_TTL_SECONDS,
    MAX_HISTORY_PRODUCTS,
    MAX_HISTORY_RECORDS_PER_PRODUCT,
    BazaarSpreadOpportunity,
    BazaarSpreadPricing,
)


def load_spread_history(path: Path = HISTORY_FILE) -> dict[str, list[dict]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    cutoff = time.time() - HISTORY_TTL_SECONDS
    history: dict[str, list[dict]] = {}
    for product_id, records in raw.items():
        if not isinstance(product_id, str) or not isinstance(records, list):
            continue
        recent = [record for record in records if isinstance(record, dict) and float(record.get("ts", 0.0) or 0.0) >= cutoff]
        if recent:
            history[product_id] = recent[-MAX_HISTORY_RECORDS_PER_PRODUCT:]
    return _trim_spread_history(history)


def save_spread_history(
    history: dict[str, list[dict]],
    accepted: list[BazaarSpreadOpportunity],
    path: Path = HISTORY_FILE,
) -> None:
    now = time.time()
    cutoff = now - HISTORY_TTL_SECONDS
    updated: dict[str, list[dict]] = {}
    for product_id, records in history.items():
        recent = [record for record in records if isinstance(record, dict) and float(record.get("ts", 0.0) or 0.0) >= cutoff]
        if recent:
            updated[product_id] = recent[-MAX_HISTORY_RECORDS_PER_PRODUCT:]
    for item in accepted:
        records = updated.setdefault(item.product_id, [])
        records.append(
            {
                "ts": now,
                "buy": item.realistic_buy_price,
                "sell": item.realistic_sell_price,
                "spread": item.net_profit_per_unit,
                "coins_per_hour": item.coins_per_hour,
            }
        )
        updated[item.product_id] = records[-MAX_HISTORY_RECORDS_PER_PRODUCT:]
    updated = _trim_spread_history(updated)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(updated, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        return


def _trim_spread_history(history: dict[str, list[dict]]) -> dict[str, list[dict]]:
    ranked: list[tuple[float, str, list[dict]]] = []
    for product_id, records in history.items():
        if not records:
            continue
        limited = records[-MAX_HISTORY_RECORDS_PER_PRODUCT:]
        timestamps = [float(record.get("ts", 0.0) or 0.0) for record in limited if isinstance(record, dict)]
        if not timestamps:
            continue
        newest = max(timestamps)
        ranked.append((newest, product_id, limited))
    ranked.sort(reverse=True)
    return {product_id: records for _, product_id, records in ranked[:MAX_HISTORY_PRODUCTS]}


def _history_stats(records: list[dict], pricing: BazaarSpreadPricing) -> tuple[int, float]:
    if not records or pricing.net_profit_per_unit <= 0:
        return 0, 0.0
    cutoff = time.time() - HISTORY_TTL_SECONDS
    recent = [record for record in records if float(record.get("ts", 0.0) or 0.0) >= cutoff]
    if not recent:
        return 0, 0.0
    stable = 0
    for record in recent:
        previous_spread = float(record.get("spread", 0.0) or 0.0)
        previous_buy = float(record.get("buy", 0.0) or 0.0)
        previous_sell = float(record.get("sell", 0.0) or 0.0)
        spread_close = previous_spread > 0 and abs(previous_spread - pricing.net_profit_per_unit) / max(pricing.net_profit_per_unit, 1.0) <= 0.35
        buy_close = previous_buy <= 0 or abs(previous_buy - pricing.realistic_buy_price) / max(pricing.realistic_buy_price, 1.0) <= 0.10
        sell_close = previous_sell <= 0 or abs(previous_sell - pricing.realistic_sell_price) / max(pricing.realistic_sell_price, 1.0) <= 0.10
        if spread_close and buy_close and sell_close:
            stable += 1
    stability = min(1.0, stable / 3)
    return len(recent), stability


