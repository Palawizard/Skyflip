from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Iterable

from .dashboard_modules import DashboardModule
from .terminal import compact_number
from .warning_summary import compact_warnings


SECTION_ATTRS = {
    "craft": "craft",
    "bazaar-spread": "bazaar_spreads",
    "bazaar-order": "bazaar_orders",
    "bazaar-compression": "conversions",
    "ah-underpriced": "ah_underpriced",
}

MODULE_WARNING_KEYWORDS = {
    "bazaar": ("bazaar", "hypixel bazaar", "spread", "order"),
    "craft": ("craft", "recipe", "skycofl", "cofl"),
    "accessories": ("talisman", "accessory", "accessories", "inventory", "skycofl", "cofl"),
    "compression": ("compression", "conversion", "bazaar"),
    "ah-bin": ("ah", "underpriced", "bin", "skycofl", "cofl"),
}


def module_candidate_rows(data, module: DashboardModule) -> list[tuple[str, object]]:
    rows: list[tuple[str, object]] = []
    for section in module.sections:
        if section == "talisman":
            analysis = getattr(data, "talisman_helper", None)
            for item in getattr(analysis, "recommendations", []) or []:
                rows.append((section, item))
            continue
        attr = SECTION_ATTRS.get(section)
        if attr:
            rows.extend((section, item) for item in getattr(data, attr, []) or [])
    return rows


def module_rejections(data, module: DashboardModule) -> list[object]:
    sections = set(module.sections)
    return [item for item in getattr(data, "rejected", []) or [] if getattr(item, "section", "") in sections]


def module_warnings(data, module: DashboardModule) -> list[str]:
    warnings = list(getattr(data, "warnings", []) or [])
    keywords = MODULE_WARNING_KEYWORDS.get(module.key, ())
    return [warning for warning in warnings if _matches_warning(warning, keywords)]


def module_summary_lines(data, module: DashboardModule, *, last_refresh: str | None) -> list[str]:
    rows = module_candidate_rows(data, module)
    rejected = module_rejections(data, module)
    warnings = module_warnings(data, module)
    best = best_candidate(rows)
    return [
        f"Last refresh: {last_refresh or 'never'}",
        f"Candidates: {len(rows)} accepted / {len(rejected)} filtered",
        f"Best candidate: {candidate_label(best[1], best[0]) if best else 'none'}",
        f"Risk: {risk_summary(row for _, row in rows)}",
        f"Warnings: {len(warnings)}",
    ]


def best_candidate(rows: list[tuple[str, object]]) -> tuple[str, object] | None:
    if not rows:
        return None
    return max(rows, key=lambda pair: _score(pair[1]))


def risk_summary(items: Iterable[object]) -> str:
    counts = {"Low": 0, "Medium": 0, "High": 0, "Test first": 0}
    total = 0
    for item in items:
        total += 1
        counts[normalize_risk(item)] += 1
    if total == 0:
        return "none"
    parts = [f"{key} {value}" for key, value in counts.items() if value]
    return ", ".join(parts)


def normalize_risk(item: object) -> str:
    text = str(getattr(item, "risk", "") or "")
    if bool(getattr(item, "should_test_first", False)):
        return "Test first"
    lowered = text.lower()
    if "test" in lowered:
        return "Test first"
    if "high" in lowered or "too slow" in lowered:
        return "High"
    if "medium" in lowered or "med" in lowered or "slow" in lowered:
        return "Medium"
    return "Low"


def empty_state_hint(module_key: str, section: str) -> str:
    if module_key == "bazaar":
        return "No candidates here. Try Recommended settings, raise Rows shown, or relax speed strictness."
    if module_key == "craft":
        return "No craft candidates here. Lower Min profit or Min margin, or check unlock requirements."
    if module_key == "accessories":
        return "No accessory rows here. Raise Max price or include AH, craftable, uncertain, or locked items."
    if module_key == "compression":
        return "No conversions here. Try Balanced mode, lower Min profit, or raise Rows shown."
    if module_key == "ah-bin":
        return "No AH BIN candidates here. Lower Min profit or allow a longer max sell time."
    return f"No {section} rows matched the current settings."


def detail_lines(item: object, section: str) -> list[tuple[str, str]]:
    return [
        ("Item", candidate_label(item, section)),
        ("Action", action_text(item, section)),
        ("Why", reason_text(item)),
        ("Risk", normalize_risk(item)),
        ("Cost/capital", cost_text(item, section)),
        ("Profit", profit_text(item)),
        ("Confidence", confidence_text(item)),
        ("Verify", verify_text(section)),
    ]


def candidate_label(item: object, section: str) -> str:
    if section == "craft":
        recipe = getattr(item, "recipe", None)
        return getattr(recipe, "name", "craft")
    if section == "bazaar-compression":
        return str(getattr(item, "name", "conversion"))
    if section == "ah-underpriced":
        return str(getattr(item, "item", "AH item"))
    if section == "talisman":
        entry = getattr(item, "entry", None)
        return str(getattr(entry, "display_name", "accessory"))
    return str(getattr(item, "product_id", "product"))


def action_text(item: object, section: str) -> str:
    manual_action = getattr(item, "manual_action", None)
    if manual_action:
        return str(manual_action).replace("Suggested manual action: ", "")
    if section == "craft":
        return f"Craft up to {compact_number(getattr(item, 'max_batch_size', 1))}, then list manually."
    if section == "talisman":
        return _accessory_action(item)
    return "Review manually before spending coins."


def reason_text(item: object) -> str:
    reasons = getattr(item, "reasons", None)
    if isinstance(reasons, list) and reasons:
        return str(reasons[0])
    return str(getattr(item, "reason", "") or "Matched the current filters.")


def cost_text(item: object, section: str) -> str:
    if section == "craft":
        cost = getattr(getattr(item, "craft_cost", None), "per_output_cost", None)
        batch = getattr(item, "max_batch_size", None)
        return f"{compact_number(cost)} each / batch {compact_number(batch)}"
    if section == "bazaar-spread":
        return compact_number(getattr(item, "capital_required", None))
    if section == "bazaar-order":
        price = getattr(item, "buy_order_price", None)
        size = getattr(item, "suggested_order_size", None)
        return f"{compact_number((price or 0) * (size or 0))}"
    if section == "bazaar-compression":
        cost = getattr(item, "input_cost", 0) * getattr(item, "suggested_batch_size", 1)
        return compact_number(cost)
    if section == "ah-underpriced":
        return compact_number(getattr(item, "lowest_bin", None))
    if section == "talisman":
        return compact_number(getattr(item, "estimated_cost", None))
    return "unknown"


def profit_text(item: object) -> str:
    value = (
        getattr(item, "estimated_profit", None)
        or getattr(item, "estimated_total_profit", None)
        or getattr(item, "profit", None)
        or getattr(item, "expected_profit", None)
    )
    percent = getattr(item, "profit_percent", None) or getattr(item, "underpriced_percent", None)
    if percent is None:
        return compact_number(value)
    return f"{compact_number(value)} / {float(percent):.1f}%"


def confidence_text(item: object) -> str:
    for field in ("confidence", "confidence_score", "speed_confidence"):
        value = getattr(item, field, None)
        if value is not None:
            return f"{float(value):.0f}%"
    if hasattr(item, "ah"):
        value = getattr(getattr(item, "ah", None), "confidence", None)
        if value is not None:
            return f"{float(value) * 100:.0f}%"
    return "unknown"


def verify_text(section: str) -> str:
    if section == "craft":
        return "Re-check AH price, sales speed, ingredients, and unlocks before listing."
    if section in {"bazaar-spread", "bazaar-order"}:
        return "Use a small test order first if risk is not Low; watch top order walls."
    if section == "bazaar-compression":
        return "Confirm the conversion path and input/output order book depth manually."
    if section == "ah-underpriced":
        return "Inspect attributes, upgrades, enchants, and recent sold listings before buying."
    if section == "talisman":
        return "Confirm ownership, soulbound requirements, and AH price before buying or crafting."
    return "Verify market data manually before acting."


def merge_module_data(existing, updated, module: DashboardModule):
    if existing is None:
        return updated
    values = dict(vars(existing))
    for section in module.sections:
        if section == "talisman":
            values["talisman_helper"] = getattr(updated, "talisman_helper", None)
            continue
        attr = SECTION_ATTRS.get(section)
        if attr:
            values[attr] = getattr(updated, attr, [])
    module_sections = set(module.sections)
    old_rejected = [
        item
        for item in getattr(existing, "rejected", []) or []
        if getattr(item, "section", "") not in module_sections
    ]
    new_rejected = [
        item
        for item in getattr(updated, "rejected", []) or []
        if getattr(item, "section", "") in module_sections
    ]
    values["rejected"] = [*old_rejected, *new_rejected]
    values["warnings"] = _merge_warnings(existing, updated, module)
    values["profile"] = getattr(updated, "profile", getattr(existing, "profile", None))
    values["budget"] = getattr(updated, "budget", getattr(existing, "budget", 0))
    values["cache_ttl"] = getattr(updated, "cache_ttl", getattr(existing, "cache_ttl", 0))
    if hasattr(existing, "__dataclass_fields__"):
        return replace(existing, **values)
    return SimpleNamespace(**values)


def _merge_warnings(existing, updated, module: DashboardModule) -> list[str]:
    old_warnings = list(getattr(existing, "warnings", []) or [])
    new_warnings = list(getattr(updated, "warnings", []) or [])
    keywords = MODULE_WARNING_KEYWORDS.get(module.key, ())
    kept = [warning for warning in old_warnings if not _matches_warning(warning, keywords)]
    return compact_warnings([*kept, *new_warnings])


def _matches_warning(warning: str, keywords: tuple[str, ...]) -> bool:
    text = str(warning).lower()
    return any(keyword in text for keyword in keywords)


def _score(item: object) -> float:
    for field in ("score", "final_score"):
        value = getattr(item, field, None)
        if value is not None:
            return float(value)
    value = (
        getattr(item, "estimated_profit", None)
        or getattr(item, "estimated_total_profit", None)
        or getattr(item, "profit", None)
        or getattr(item, "expected_profit", None)
        or 0
    )
    return float(value or 0)


def _accessory_action(item: object) -> str:
    if getattr(item, "covered_by_higher_tier", False) or getattr(item, "owned_exact", False):
        return "Skip; already owned or covered."
    if getattr(item, "craftable_now", False):
        return "Craft manually after checking material prices."
    if getattr(item, "available_on_ah", False):
        return "Buy from AH manually after checking lowest BIN."
    status = str(getattr(item, "status", ""))
    if "locked" in status.lower():
        return "Unlock requirements first."
    return "Review manually before spending coins."
