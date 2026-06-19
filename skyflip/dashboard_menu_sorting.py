from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from .dashboard_menu_ui import _muted


SECTION_SORTS = {
    "craft": [
        ("default", "Default"),
        ("score", "Score"),
        ("profit", "Profit"),
        ("percent", "Profit %"),
        ("sales", "Sales/day"),
        ("name", "Name"),
    ],
    "bazaar-spread": [
        ("default", "Default"),
        ("coins-hour", "Coins/h"),
        ("profit", "Profit"),
        ("percent", "Profit %"),
        ("profit-minute", "Profit/min"),
        ("capital", "Capital"),
        ("risk", "Safest"),
    ],
    "bazaar-order": [
        ("default", "Default"),
        ("score", "Score"),
        ("profit", "Profit"),
        ("percent", "Profit %"),
        ("speed", "Speed"),
        ("risk", "Safest"),
    ],
    "bazaar-compression": [
        ("default", "Default"),
        ("score", "Score"),
        ("profit", "Profit"),
        ("percent", "Profit %"),
        ("speed", "Speed"),
        ("risk", "Safest"),
    ],
    "ah-underpriced": [
        ("default", "Default"),
        ("score", "Score"),
        ("profit", "Profit"),
        ("discount", "Discount"),
        ("sales", "Sales/day"),
        ("risk", "Safest"),
    ],
    "rejected": [
        ("default", "Default"),
        ("section", "Section"),
        ("item", "Item"),
    ],
}
SORT_PREFERENCES_FILE = Path(".skyflip") / "dashboard_sorts.json"


def _sort_options(section: str) -> list[tuple[str, str]]:
    return SECTION_SORTS.get(section, [])


def _section_sort_key(state: _MenuState, section: str) -> str:
    options = _sort_options(section)
    if not options:
        return "default"
    current = state.section_sorts.get(section, options[0][0])
    valid = {key for key, _ in options}
    return current if current in valid else options[0][0]


def _cycle_section_sort(state: _MenuState, section: str, direction: int) -> None:
    options = _sort_options(section)
    if not options:
        return
    keys = [key for key, _ in options]
    current = _section_sort_key(state, section)
    index = keys.index(current)
    state.section_sorts[section] = keys[(index + direction) % len(keys)]
    if state.persist_sort_preferences:
        save_sort_preferences(state.section_sorts)


def load_sort_preferences(path: Path = SORT_PREFERENCES_FILE) -> dict[str, str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, str] = {}
    for section, sort_key in raw.items():
        if not isinstance(section, str) or not isinstance(sort_key, str):
            continue
        valid = {key for key, _ in _sort_options(section)}
        if sort_key in valid:
            result[section] = sort_key
    return result


def save_sort_preferences(section_sorts: dict[str, str], path: Path = SORT_PREFERENCES_FILE) -> None:
    valid: dict[str, str] = {}
    for section, sort_key in section_sorts.items():
        allowed = {key for key, _ in _sort_options(section)}
        if sort_key in allowed and sort_key != "default":
            valid[section] = sort_key
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(valid, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        return


def _draw_sort_hint(section: str, sort_key: str) -> None:
    options = _sort_options(section)
    if not options:
        return
    label = next((label for key, label in options if key == sort_key), "Default")
    print(f"Sort: {label}  {_muted('use Left/Right')}")


def _sorted_section_data(data, section: str, sort_key: str):
    if sort_key == "default":
        return data
    attr = {
        "craft": "craft",
        "bazaar-spread": "bazaar_spreads",
        "bazaar-order": "bazaar_orders",
        "bazaar-compression": "conversions",
        "ah-underpriced": "ah_underpriced",
        "rejected": "rejected",
    }.get(section)
    if attr is None:
        return data
    items = list(getattr(data, attr, []) or [])
    sorted_items = _sort_section_items(section, items, sort_key)
    values = dict(vars(data)) if hasattr(data, "__dict__") else {}
    values[attr] = sorted_items
    return SimpleNamespace(**values)


def _sort_section_items(section: str, items: list, sort_key: str) -> list:
    reverse = sort_key not in {"name", "risk", "section", "item"}
    if section == "craft":
        key_func = {
            "score": lambda item: _num(getattr(item, "score", 0)),
            "profit": lambda item: _num(getattr(item, "estimated_profit", 0)),
            "percent": lambda item: _num(getattr(item, "profit_percent", 0)),
            "sales": lambda item: _num(getattr(getattr(item, "market", None), "analysis", None).sales_per_day if getattr(getattr(item, "market", None), "analysis", None) else 0),
            "name": lambda item: getattr(getattr(item, "recipe", None), "name", ""),
        }.get(sort_key)
    elif section == "bazaar-spread":
        key_func = {
            "coins-hour": lambda item: _num(getattr(item, "coins_per_hour", 0)),
            "profit": lambda item: _num(getattr(item, "estimated_total_profit", 0)),
            "percent": lambda item: _num(getattr(item, "profit_percent", 0)),
            "profit-minute": lambda item: _num(getattr(item, "profit_per_minute", 0)),
            "capital": lambda item: _num(getattr(item, "capital_required", 0)),
            "risk": lambda item: (_risk_rank(getattr(item, "risk", "")), -_num(getattr(item, "coins_per_hour", 0))),
        }.get(sort_key)
    elif section == "bazaar-order":
        key_func = {
            "score": lambda item: _num(getattr(item, "score", 0)),
            "profit": lambda item: _num(getattr(item, "estimated_profit", 0)),
            "percent": lambda item: _num(getattr(item, "profit_percent", 0)),
            "speed": lambda item: _num(getattr(getattr(item, "speed", None), "speed_score", 0)),
            "risk": lambda item: (_risk_rank(getattr(item, "risk", "")), -_num(getattr(item, "score", 0))),
        }.get(sort_key)
    elif section == "bazaar-compression":
        key_func = {
            "score": lambda item: _num(getattr(item, "score", 0)),
            "profit": lambda item: _num(getattr(item, "profit", 0)),
            "percent": lambda item: _num(getattr(item, "profit_percent", 0)),
            "speed": lambda item: _num(getattr(getattr(item, "bottleneck_speed", None), "speed_score", 0)),
            "risk": lambda item: (_risk_rank(getattr(item, "risk", "")), -_num(getattr(item, "score", 0))),
        }.get(sort_key)
    elif section == "ah-underpriced":
        key_func = {
            "score": lambda item: _num(getattr(item, "score", 0)),
            "profit": lambda item: _num(getattr(item, "expected_profit", 0)),
            "discount": lambda item: _num(getattr(item, "underpriced_percent", 0)),
            "sales": lambda item: _num(getattr(item, "sales_per_day", 0)),
            "risk": lambda item: (_risk_rank(getattr(item, "risk", "")), -_num(getattr(item, "score", 0))),
        }.get(sort_key)
    elif section == "rejected":
        key_func = {
            "section": lambda item: (getattr(item, "section", ""), getattr(item, "item", "")),
            "item": lambda item: (getattr(item, "item", ""), getattr(item, "section", "")),
        }.get(sort_key)
    else:
        key_func = None
    if key_func is None:
        return items
    return sorted(items, key=key_func, reverse=reverse)


def _risk_rank(value: str) -> int:
    text = str(value).lower()
    if "low" in text:
        return 0
    if "medium" in text or "med" in text:
        return 1
    if "high" in text:
        return 2
    return 3


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
