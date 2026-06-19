from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .scoring import AnalyzerConfig
from .bazaar_spread_models import RISK_ORDER, RISK_RULES_FILE, BazaarRiskRules
from .bazaar_spread_support import _safe_spread_capital


def _risk_label(score: float, manipulation_risk: float, bottleneck_speed: float) -> str:
    if manipulation_risk >= 45 or score < 35 or bottleneck_speed < 35:
        return "High"
    if manipulation_risk >= 20 or score < 65 or bottleneck_speed < 60:
        return "Medium"
    return "Low"


def _apply_spread_risk_floor(
    *,
    base_risk: str,
    profit_percent: float,
    price_ratio: float,
    estimated_profit: float,
    capital_required: float,
    thin_depth: bool,
    history_seen_count: int,
    strong_history: bool,
    confidence_score: float,
    volatile_reason: str | None,
    volatile_min_risk: str | None,
) -> tuple[str, list[str]]:
    risk = base_risk
    reasons: list[str] = []
    missing_history = history_seen_count <= 0
    extreme_spread = profit_percent >= 100 or price_ratio >= 2.0
    large_spread = profit_percent >= 40 or price_ratio >= 1.5

    if volatile_reason and large_spread:
        risk = _max_risk(risk, volatile_min_risk or "Medium")
        reasons.append(volatile_reason)
        if thin_depth or missing_history:
            risk = _max_risk(risk, "High")
    if profit_percent >= 40 and not strong_history:
        risk = _max_risk(risk, "Medium")
        reasons.append("large spread; test small first")
    if profit_percent >= 75:
        risk = _max_risk(risk, "Medium")
        reasons.append("very large spread")
    if extreme_spread and not strong_history:
        risk = _max_risk(risk, "High")
        reasons.append("extreme spread without repeated local success")
    if large_spread and estimated_profit > max(500_000.0, capital_required * 0.35):
        risk = _max_risk(risk, "Medium")
        reasons.append("profit unusually high versus capital")
    if large_spread and missing_history:
        risk = _max_risk(risk, "Medium")
        reasons.append("no local success history")
    if large_spread and confidence_score < 55:
        risk = _max_risk(risk, "Medium")
        reasons.append("low confidence on high spread")
    if large_spread and thin_depth:
        risk = _max_risk(risk, "High" if extreme_spread or volatile_reason else "Medium")
        reasons.append("thin depth on high spread")
    return risk, _dedupe(reasons)


def _should_test_first(
    *,
    risk: str,
    profit_percent: float,
    estimated_profit: float,
    capital_required: float,
    history_seen_count: int,
    confidence_score: float,
    volatile_reason: str | None,
) -> bool:
    return (
        risk in {"Medium", "High"}
        or profit_percent >= 40
        or volatile_reason is not None
        or (history_seen_count <= 0 and profit_percent >= 25)
        or estimated_profit > max(500_000.0, capital_required * 0.35)
        or confidence_score < 60
    )


def _suggest_test_order_size(*, full_size: int, buy_price: float, risk: str, config: AnalyzerConfig) -> int:
    if full_size <= 1 or buy_price <= 0:
        return max(1, full_size)
    if risk == "High":
        fraction = 0.05
        capital_cap = min(250_000.0, _safe_spread_capital(config) * 0.12)
        unit_cap = 5_000
    elif risk == "Medium":
        fraction = 0.12
        capital_cap = min(500_000.0, _safe_spread_capital(config) * 0.20)
        unit_cap = 10_000
    else:
        fraction = 1.0
        capital_cap = _safe_spread_capital(config)
        unit_cap = full_size
    by_fraction = max(1, int(full_size * fraction))
    by_capital = max(1, int(capital_cap // buy_price))
    return max(1, min(full_size, by_fraction, by_capital, unit_cap))


def _max_risk(left: str, right: str) -> str:
    return left if RISK_ORDER.get(left, 0) >= RISK_ORDER.get(right, 0) else right


@lru_cache(maxsize=1)
def load_bazaar_risk_rules(path_text: str = str(RISK_RULES_FILE)) -> BazaarRiskRules:
    try:
        raw = json.loads(Path(path_text).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    prefixes = _string_tuple(raw.get("volatile_prefixes"), ("SHARD_",))
    contains = _string_tuple(raw.get("volatile_contains"), ("KUUDRA", "LOTUS", "GIFT"))
    min_risk_raw = raw.get("min_risk_by_pattern")
    min_risk = {
        str(key).upper(): str(value).title()
        for key, value in min_risk_raw.items()
        if isinstance(min_risk_raw, dict) and str(value).title() in RISK_ORDER
    } if isinstance(min_risk_raw, dict) else {"SHARD_": "Medium"}
    return BazaarRiskRules(prefixes, contains, min_risk)


def _volatile_reason(product_id: str) -> str | None:
    product = product_id.upper()
    rules = load_bazaar_risk_rules()
    for prefix in rules.volatile_prefixes:
        if product.startswith(prefix):
            if prefix == "SHARD_":
                return "shard item with large spread"
            return "volatile item family"
    for needle in rules.volatile_contains:
        if needle and needle in product:
            return "volatile item family"
    return None


def _volatile_min_risk(product_id: str) -> str | None:
    product = product_id.upper()
    rules = load_bazaar_risk_rules()
    for pattern, risk in rules.min_risk_by_pattern.items():
        if product.startswith(pattern) or pattern in product:
            return risk
    return None


def _string_tuple(value: object, default: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(value, list):
        return default
    items = tuple(str(item).upper() for item in value if isinstance(item, str) and item.strip())
    return items or default


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


