from __future__ import annotations

import math
from typing import Any, Iterable

from .accessory_models import AccessoryFilters, AccessoryRecommendation, RARITY_ORDER


def apply_accessory_filters(rows: Iterable[AccessoryRecommendation], filters: AccessoryFilters) -> list[AccessoryRecommendation]:
    result = list(rows)
    if filters.rarities:
        result = [row for row in result if row.entry.rarity in filters.rarities]
    if filters.max_price is not None:
        result = [row for row in result if row.estimated_cost is None or row.estimated_cost <= filters.max_price]
    if filters.only_craftable:
        result = [row for row in result if row.craftable_now]
    if filters.only_ah:
        result = [row for row in result if row.available_on_ah]
    if filters.hide_locked and not filters.show_locked:
        result = [row for row in result if row.status not in {"Locked", "Unknown requirements", "Unknown recipe"}]
    if not filters.show_owned:
        result = [row for row in result if not row.owned_exact and not row.covered_by_higher_tier]
    if not filters.include_uncertain:
        result = [
            row
            for row in result
            if row.owned_exact
            or row.covered_by_higher_tier
            or not (row.entry.uncertain_requirements or row.entry.confidence == "low")
        ]
    if not filters.include_manual:
        result = [row for row in result if "manual" not in row.entry.source_types and row.status != "Soulbound / manual unlock"]
    if not filters.include_craftable:
        result = [row for row in result if not row.craftable_now]
    if not filters.include_ah:
        result = [row for row in result if not row.available_on_ah]
    if filters.search:
        needle = filters.search.lower()
        result = [row for row in result if needle in row.entry.display_name.lower() or needle in row.entry.item_id.lower()]
    return sorted(result, key=lambda row: _sort_value(row, filters.sort_key), reverse=filters.descending)

def filters_from_args(args: Any) -> AccessoryFilters:
    rarities = {
        part.strip().lower()
        for part in str(getattr(args, "accessory_rarity", "") or "").split(",")
        if part.strip()
    }
    max_price = getattr(args, "max_accessory_price", None)
    if max_price is None and getattr(args, "budget", None):
        max_price = float(getattr(args, "budget")) * 0.10
    return AccessoryFilters(
        view=str(getattr(args, "accessory_view", "recommended") or "recommended"),
        sort_key=str(getattr(args, "accessory_sort", "score") or "score"),
        descending=not bool(getattr(args, "accessory_ascending", False)),
        rarities=rarities,
        max_price=max_price,
        show_owned=bool(getattr(args, "show_owned", False)),
        show_locked=bool(getattr(args, "show_locked", False)) or bool(getattr(args, "include_locked_accessories", False)),
        only_craftable=bool(getattr(args, "only_craftable", False)),
        only_ah=bool(getattr(args, "only_ah", False)),
        hide_locked=not bool(getattr(args, "show_locked", False) and getattr(args, "include_locked_accessories", True)),
        search=getattr(args, "accessory_search", None),
        include_uncertain=bool(getattr(args, "include_uncertain_accessories", False)),
        include_manual=bool(getattr(args, "include_manual_unlocks", True)),
        include_ah=bool(getattr(args, "include_ah_accessories", True)),
        include_craftable=bool(getattr(args, "include_craftable_accessories", True)),
        max_recommendations=int(getattr(args, "max_accessory_recommendations", 15) or 15),
        max_ah_checks=int(getattr(args, "max_accessory_ah_checks", 60) or 60),
    )



def _sort_value(row: AccessoryRecommendation, key: str) -> Any:
    normalized = key.replace("_", "-")
    if normalized == "score":
        return row.score
    if normalized == "rarity":
        return RARITY_ORDER.get(row.entry.rarity, 0)
    if normalized in {"price", "estimated-ah-price"}:
        return row.estimated_cost if row.estimated_cost is not None else math.inf
    if normalized == "craft-cost":
        return row.craft_cost if row.craft_cost is not None else math.inf
    if normalized == "coin-per-mp":
        return row.coin_per_mp if row.coin_per_mp is not None else math.inf
    if normalized == "craftable":
        return int(row.craftable_now)
    if normalized in {"ah", "ah-availability"}:
        return int(row.available_on_ah)
    if normalized == "collection":
        return max(row.entry.requirements.collections.values(), default=0)
    if normalized == "skill":
        return max(row.entry.requirements.skills.values(), default=0)
    if normalized == "slayer":
        return max(row.entry.requirements.slayers.values(), default=0)
    if normalized == "name":
        return row.entry.display_name.lower()
    return row.score

