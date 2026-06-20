from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable

from .bazaar import BazaarClient
from .cofl import CoflClient
from .profile_parser import PlayerProfile
from .accessory_database import augment_with_hypixel_accessories, load_accessory_database, normalize_item_id
from .accessory_filters import apply_accessory_filters
from .accessory_models import (
    INCOMPLETE_ACCESSORY_DATA_WARNING,
    RARITY_MP,
    RARITY_ORDER,
    AccessoryAh,
    AccessoryAnalysis,
    AccessoryCraftCost,
    AccessoryDatabase,
    AccessoryEntry,
    AccessoryFilters,
    AccessoryOwnership,
    AccessoryRecommendation,
    AccessorySummary,
    BestOwnedFamilyTier,
)


def analyze_accessories(
    profile: PlayerProfile,
    bazaar: BazaarClient,
    cofl: CoflClient,
    *,
    database: AccessoryDatabase | None = None,
    database_path: str | Path = "data/accessories.json",
    filters: AccessoryFilters | None = None,
    days: int = 7,
) -> AccessoryAnalysis:
    db = database or load_accessory_database(database_path)
    if database is None:
        db = augment_with_hypixel_accessories(db, getattr(cofl, "http", None))
    filters = filters or AccessoryFilters()
    ownership = detect_owned_accessories(profile, db)
    rows: list[AccessoryRecommendation] = []
    ah_checks = 0
    for item in db.accessories:
        if not filters.include_uncertain and (
            item.uncertain_requirements or item.confidence == "low" or item.requires_manual_verification
        ):
            continue
        item_missing = item.item_id not in ownership.owned_exact and item.item_id not in ownership.covered_by_higher_tier
        skip_low_confidence_ah = item.auto_generated and item.confidence == "low" and not _is_upgrade_from_owned_family(item, ownership, db)
        fetch_ah = bool(filters.include_ah and item_missing and not skip_low_confidence_ah and ah_checks < filters.max_ah_checks)
        if fetch_ah:
            ah_checks += 1
        rows.append(evaluate_accessory(item, profile, db, ownership, bazaar, cofl, filters=filters, days=days, fetch_ah=fetch_ah))
    all_missing = [row for row in rows if not row.owned_exact and not row.covered_by_higher_tier]
    craftable = [row for row in all_missing if row.craftable_now]
    ah_available = [row for row in all_missing if row.available_on_ah]
    upgrades = [row for row in all_missing if _is_upgrade_from_owned_family(row.entry, ownership, db)]
    locked = [row for row in all_missing if row.status in {"Locked", "Unknown requirements", "Unknown recipe", "Soulbound / manual unlock", "Not craftable / source only"}]
    owned = [row for row in rows if row.owned_exact or row.covered_by_higher_tier]
    recommended = apply_accessory_filters([row for row in all_missing if row.score > 0 and not row.ah.overpriced and not row.ah.manipulated], filters)
    recommended = recommended[: filters.max_recommendations]
    cheapest = sorted(
        [row for row in all_missing if row.estimated_cost is not None and row.mp_gain > 0],
        key=lambda row: (row.coin_per_mp if row.coin_per_mp is not None else math.inf, row.estimated_cost or math.inf),
    )[:5]
    warnings = list(ownership.warnings)
    summary = AccessorySummary(
        magical_power=profile.magical_power,
        accessory_bag_slots=profile.accessory_bag_slots,
        owned_count=max(len(ownership.owned_exact), profile.accessory_bag_slots or 0, len(profile.accessory_bag_item_ids)),
        missing_count=len(all_missing),
        craftable_count=len(craftable),
        ah_count=len(ah_available),
        cheapest_upgrades=cheapest,
        warnings=warnings,
    )
    return AccessoryAnalysis(
        view=filters.view,
        summary=summary,
        recommendations=recommended,
        craftable=apply_accessory_filters(craftable, filters),
        ah_available=apply_accessory_filters(ah_available, filters),
        upgrades=apply_accessory_filters(upgrades, filters),
        locked=apply_accessory_filters(locked, AccessoryFilters(**{**filters.__dict__, "hide_locked": False, "show_locked": True})),
        all_missing=apply_accessory_filters(all_missing, AccessoryFilters(**{**filters.__dict__, "hide_locked": False})),
        owned=apply_accessory_filters(owned, AccessoryFilters(**{**filters.__dict__, "show_owned": True, "hide_locked": False})),
        rows=apply_accessory_filters(rows, filters),
        ownership=ownership,
    )


def detect_owned_accessories(profile: PlayerProfile, database: AccessoryDatabase) -> AccessoryOwnership:
    known_ids = set(database.by_id)
    ownership_source = set(profile.accessory_bag_item_ids) or set(profile.item_ids)
    alias_to_id = {
        alias: item.item_id
        for item in database.accessories
        for alias in item.aliases
    }
    name_to_id = {
        normalize_item_id(item.display_name): item.item_id
        for item in database.accessories
    }
    owned_exact: set[str] = set()
    for raw_item_id in ownership_source:
        owned_exact.update(_resolve_owned_item_ids(raw_item_id, database, known_ids, alias_to_id, name_to_id))
    for raw_item_id in profile.item_ids:
        owned_exact.update(_resolve_owned_item_ids(raw_item_id, database, known_ids, alias_to_id, name_to_id))
    _apply_family_prefix_coverage(ownership_source, owned_exact, database)
    best_by_family = get_best_owned_tier_by_family(owned_exact, database)
    best = {family_id: best.item_id for family_id, best in best_by_family.items()}
    covered = {item_id for best in best_by_family.values() for item_id in best.covered_lower_tiers}
    warnings: list[str] = []
    confidence = 1.0
    if not profile.inventory_api_enabled:
        confidence = 0.45
        warnings.append(INCOMPLETE_ACCESSORY_DATA_WARNING)
    elif not owned_exact:
        confidence = 0.65
        warnings.append("No known accessories were detected in decoded inventory data.")
    unknown_bag_items = set(profile.accessory_bag_item_ids) - known_ids - set(alias_to_id)
    if unknown_bag_items:
        warnings.append(f"{len(unknown_bag_items)} accessory bag item(s) are not in data/accessories.json yet.")
    return AccessoryOwnership(owned_exact, best, covered, warnings, confidence)


def get_best_owned_tier_by_family(
    owned_accessories: Iterable[str],
    accessory_database: AccessoryDatabase,
) -> dict[str, BestOwnedFamilyTier]:
    by_id = accessory_database.by_id
    owned_ids = {normalize_item_id(item_id) for item_id in owned_accessories}
    best: dict[str, AccessoryEntry] = {}
    for item_id in owned_ids:
        item = by_id.get(item_id)
        if item is None:
            continue
        current = best.get(item.family_id)
        if current is None or item.tier_index > current.tier_index:
            best[item.family_id] = item
    result: dict[str, BestOwnedFamilyTier] = {}
    for family_id, best_item in best.items():
        covered = {
            item.item_id
            for item in accessory_database.by_family.get(family_id, [])
            if item.tier_index < best_item.tier_index
        }
        result[family_id] = BestOwnedFamilyTier(
            family_id=family_id,
            item_id=best_item.item_id,
            tier_index=best_item.tier_index,
            covered_lower_tiers=covered,
        )
    return result


def is_downgrade_covered(accessory: AccessoryEntry, best_owned_by_family: dict[str, BestOwnedFamilyTier]) -> bool:
    best = best_owned_by_family.get(accessory.family_id)
    return bool(best and best.tier_index > accessory.tier_index)


def _apply_family_prefix_coverage(raw_item_ids: set[str], owned_exact: set[str], database: AccessoryDatabase) -> None:
    by_id = database.by_id
    for raw_item_id in raw_item_ids:
        item_id = normalize_item_id(raw_item_id)
        if item_id.startswith("CAMPFIRE_TALISMAN_"):
            for entry in database.by_family.get("campfire_badge", []):
                if entry.item_id in by_id:
                    owned_exact.add(entry.item_id)


def _resolve_owned_item_ids(
    raw_item_id: str,
    database: AccessoryDatabase,
    known_ids: set[str],
    alias_to_id: dict[str, str],
    name_to_id: dict[str, str],
) -> set[str]:
    item_id = normalize_item_id(raw_item_id)
    if not item_id:
        return set()
    if item_id in known_ids:
        return {item_id}
    if item_id in alias_to_id:
        return {alias_to_id[item_id]}
    if item_id in name_to_id:
        return {name_to_id[item_id]}

    # Reforged/recombobulated/enriched display names are often prefixed while the
    # underlying SkyBlock item id remains stable. Match only full suffix tokens.
    matches = {
        resolved_id
        for normalized_name, resolved_id in name_to_id.items()
        if item_id.endswith(f"_{normalized_name}")
    }
    if len(matches) == 1:
        return matches

    id_matches = {known_id for known_id in known_ids if item_id.endswith(f"_{known_id}")}
    if len(id_matches) == 1:
        return id_matches
    return set()


def _is_upgrade_from_owned_family(entry: AccessoryEntry, ownership: AccessoryOwnership, database: AccessoryDatabase) -> bool:
    family_best = ownership.owned_family_best.get(entry.family_id)
    if not family_best:
        return False
    best = database.by_id.get(family_best)
    return bool(best and entry.tier_index > best.tier_index)


def evaluate_accessory(
    entry: AccessoryEntry,
    profile: PlayerProfile,
    database: AccessoryDatabase,
    ownership: AccessoryOwnership,
    bazaar: BazaarClient,
    cofl: CoflClient,
    *,
    filters: AccessoryFilters,
    days: int,
    fetch_ah: bool = True,
) -> AccessoryRecommendation:
    owned_exact = entry.item_id in ownership.owned_exact
    family_best = ownership.owned_family_best.get(entry.family_id)
    best_by_family = get_best_owned_tier_by_family(ownership.owned_exact, database)
    covered = entry.item_id in ownership.covered_by_higher_tier or is_downgrade_covered(entry, best_by_family)
    missing_useful = not owned_exact and not covered
    requirement_missing = missing_requirements(entry, profile)
    ah = _ah_state(entry, cofl, days=days) if missing_useful and fetch_ah else AccessoryAh()
    craft = _craft_state(entry, profile, database, ownership, bazaar, cofl, stack=()) if missing_useful else AccessoryCraftCost(None, [], [])

    status = _status(entry, requirement_missing, craft, ah)
    craftable_now = status == "Craftable now"
    available_on_ah = bool(ah.active.lowest_bin and not entry.soulbound and entry.auctionable and not ah.overpriced and not ah.manipulated)
    estimated_cost = _best_cost(craft.total_cost, ah.safe_price if available_on_ah else None)
    mp_gain = 0 if owned_exact or covered else RARITY_MP.get(entry.rarity, 3)
    coin_per_mp = estimated_cost / mp_gain if estimated_cost is not None and mp_gain > 0 else None
    label, best_method = _label_and_method(entry, status, craft, ah, available_on_ah)
    score, reasons = _score(entry, status, estimated_cost, coin_per_mp, ownership.confidence, ah, craft, requirement_missing)
    if _is_upgrade_from_owned_family(entry, ownership, database):
        score = min(100.0, score + 12.0)
        reasons.insert(0, "upgrade owned tier")

    if owned_exact:
        label = "Owned"
        best_method = "Already owned"
        score = 0
        reasons = ["already owned"]
    elif covered:
        label = "Covered"
        best_method = f"Covered by {database.by_id[family_best].display_name}" if family_best else "Covered by higher tier"
        score = 0
        reasons = [f"covered by higher tier: {family_best}" if family_best else "covered by higher tier"]

    return AccessoryRecommendation(
        entry=entry,
        owned_exact=owned_exact,
        owned_family_best=family_best,
        covered_by_higher_tier=covered,
        missing_useful=missing_useful,
        status=status,
        best_method=best_method,
        label=label,
        estimated_cost=estimated_cost,
        craft_cost=craft.total_cost,
        coin_per_mp=coin_per_mp,
        mp_gain=mp_gain,
        score=score,
        reasons=reasons,
        missing_requirements=requirement_missing,
        shopping_list=craft.shopping_list,
        ah=ah,
        craftable_now=craftable_now,
        available_on_ah=available_on_ah,
        confidence=min(1.0, ownership.confidence * max(0.35, ah.confidence or 1.0)),
    )


def missing_requirements(entry: AccessoryEntry, profile: PlayerProfile) -> list[str]:
    missing: list[str] = []
    for collection, required in entry.requirements.collections.items():
        actual = profile.collection_tiers.get(collection)
        if actual is None:
            missing.append(f"unknown {collection} collection tier, requires {required}")
        elif actual < required:
            missing.append(f"{collection} collection tier {actual} < {required}")
    for skill, required in entry.requirements.skills.items():
        actual = profile.skills.get(skill)
        if actual is None:
            missing.append(f"unknown {skill} level, requires {required}")
        elif actual < required:
            missing.append(f"{skill} {actual} < {required}")
    for slayer, required in entry.requirements.slayers.items():
        actual = profile.slayer_levels.get(slayer)
        if actual is None:
            missing.append(f"unknown {slayer} slayer level, requires {required}")
        elif actual < required:
            missing.append(f"{slayer} slayer {actual} < {required}")
    for floor in entry.requirements.catacombs_floor_completions:
        if profile.catacombs_floor_completions.get(floor, 0) <= 0:
            missing.append(f"catacombs floor {floor} completion required")
    if entry.requirements.skyblock_level is not None:
        if profile.skyblock_level is None:
            missing.append(f"unknown SkyBlock level, requires {entry.requirements.skyblock_level}")
        elif profile.skyblock_level < entry.requirements.skyblock_level:
            missing.append(f"SkyBlock level {profile.skyblock_level} < {entry.requirements.skyblock_level}")
    if entry.uncertain_requirements:
        missing.append("requirements are uncertain; verify in game")
    return missing


def _craft_state(
    entry: AccessoryEntry,
    profile: PlayerProfile,
    database: AccessoryDatabase,
    ownership: AccessoryOwnership,
    bazaar: BazaarClient,
    cofl: CoflClient,
    *,
    stack: tuple[str, ...],
) -> AccessoryCraftCost:
    if "craft" not in entry.source_types:
        return AccessoryCraftCost(None, [], ["not a craft source"])
    if not entry.recipe_verified:
        return AccessoryCraftCost(None, [], ["craft recipe is not verified"])
    missing = [item for item in missing_requirements(entry, profile) if "uncertain" not in item]
    if missing:
        return AccessoryCraftCost(None, [], missing)
    if entry.item_id in stack:
        return AccessoryCraftCost(None, [], [f"recursive accessory recipe: {' > '.join(stack)}"])

    total = 0.0
    shopping: list[str] = []
    unavailable: list[str] = []
    used_previous_purchase = False
    for ingredient in entry.recipe:
        unit = None
        source = ingredient.source
        if source in {"fixed", "fixed_cost", "npc"}:
            unit = float(ingredient.fixed_coin_cost or 0)
            shopping.append(f"{ingredient.quantity:g}x {ingredient.display_name} from NPC/fixed ({_coins(unit * ingredient.quantity)})")
        elif source == "bazaar":
            price = bazaar.price_for(ingredient.item_id or "")
            if price is None:
                unavailable.append(f"missing Bazaar price for {ingredient.display_name}")
            else:
                unit = price.unit_price
                shopping.append(f"{ingredient.quantity:g}x {ingredient.display_name} from Bazaar ({_coins(unit * ingredient.quantity)})")
        elif source in {"previous_tier", "previous_recipe", "craft"} and ingredient.item_id in database.by_id:
            previous = database.by_id[ingredient.item_id]
            if previous.item_id in ownership.owned_exact:
                unit = 0
                shopping.append(f"use owned {previous.display_name}")
            else:
                nested = _craft_state(previous, profile, database, ownership, bazaar, cofl, stack=stack + (entry.item_id,))
                ah = _ah_state(previous, cofl, days=7) if previous.auctionable and not previous.soulbound else AccessoryAh()
                candidates = [value for value in [nested.total_cost, ah.safe_price] if value is not None and value > 0]
                if candidates:
                    unit = min(candidates)
                    used_previous_purchase = bool(ah.safe_price and unit == ah.safe_price)
                    if unit == nested.total_cost:
                        shopping.extend(nested.shopping_list)
                    else:
                        shopping.append(f"buy {previous.display_name} on AH ({_coins(unit)})")
                else:
                    unavailable.append(f"need {previous.display_name} first")
        elif source == "ah":
            active = cofl.active_bins(ingredient.item_id or "")
            if active.lowest_bin:
                unit = active.lowest_bin
                shopping.append(f"buy {ingredient.display_name} on AH ({_coins(unit)})")
            else:
                unavailable.append(f"missing AH BIN for {ingredient.display_name}")
        else:
            unavailable.append(f"unsupported ingredient source {source} for {ingredient.display_name}")

        if unit is not None:
            total += unit * ingredient.quantity

    if unavailable:
        return AccessoryCraftCost(None, shopping, unavailable, used_previous_purchase)
    return AccessoryCraftCost(total, shopping, [], used_previous_purchase)


def _ah_state(entry: AccessoryEntry, cofl: CoflClient, *, days: int) -> AccessoryAh:
    if entry.soulbound or not entry.auctionable:
        return AccessoryAh(warnings=["not auctionable"])
    active = cofl.active_bins(entry.item_id)
    sold = cofl.sold_summary(entry.item_id)
    lowest = active.lowest_bin
    median = sold.median_price
    safe = lowest
    warnings: list[str] = []
    manipulated = False
    overpriced = False
    if lowest and active.second_lowest_bin and active.second_lowest_bin > lowest * 1.8:
        manipulated = True
        warnings.append("lowest BIN is far below the next listing")
    if lowest and median and lowest > median * 1.6:
        overpriced = True
        warnings.append("lowest BIN is far above recent median sold price")
    if median and lowest:
        safe = min(lowest, median * 1.1)
    confidence = 0.0
    if lowest:
        confidence = 0.45 + min(0.35, active.active_count / 30) + min(0.2, sold.sale_count / 100)
    if manipulated or overpriced:
        confidence *= 0.45
    return AccessoryAh(active, sold, safe, min(1.0, confidence), manipulated, overpriced, warnings)


def _status(entry: AccessoryEntry, missing: list[str], craft: AccessoryCraftCost, ah: AccessoryAh) -> str:
    if entry.soulbound or ("manual" in entry.source_types and not entry.auctionable):
        return "Soulbound / manual unlock"
    if ah.active.lowest_bin:
        return "Available on AH"
    if entry.uncertain_requirements:
        return "Unknown requirements"
    if missing:
        return "Locked"
    if craft.total_cost is not None and craft.used_previous_tier_purchase:
        return "Craftable if buying previous tier"
    if craft.total_cost is not None:
        return "Craftable now"
    if "craft recipe is not verified" in craft.unavailable:
        return "Unknown recipe"
    if "craft" not in entry.source_types:
        return "Not craftable / source only"
    return "Locked"


def _best_cost(craft_cost: float | None, ah_price: float | None) -> float | None:
    candidates = [value for value in (craft_cost, ah_price) if value is not None and value >= 0]
    return min(candidates) if candidates else None


def _label_and_method(entry: AccessoryEntry, status: str, craft: AccessoryCraftCost, ah: AccessoryAh, available_on_ah: bool) -> tuple[str, str]:
    if status == "Craftable now":
        return "Craft now", "Craft"
    if status == "Craftable if buying previous tier":
        return "Craft now", "Craft after buying previous tier"
    if available_on_ah:
        return "Buy now", "Buy on AH"
    if status == "Soulbound / manual unlock":
        return "Manual unlock", entry.manual_unlock_notes or "Do quest/manual unlock"
    if status == "Locked":
        return "Locked", "Unlock requirement first"
    if status == "Unknown requirements":
        return "Unknown", "Verify requirements"
    if status == "Unknown recipe":
        return "Unknown", "Verify recipe/source"
    if ah.overpriced or ah.manipulated:
        return "Overpriced", "Wait for safer AH price"
    return "Good later", status


def _score(
    entry: AccessoryEntry,
    status: str,
    estimated_cost: float | None,
    coin_per_mp: float | None,
    ownership_confidence: float,
    ah: AccessoryAh,
    craft: AccessoryCraftCost,
    missing: list[str],
) -> tuple[float, list[str]]:
    if status in {"Locked", "Unknown requirements", "Unknown recipe"} and not ah.active.lowest_bin:
        if entry.confidence == "low" or entry.requires_manual_verification:
            return 0.0, missing[:2] or ["manual verification required"]
        return 8.0 if entry.uncertain_requirements else 0.0, missing[:2] or ["locked"]
    value_score = 18.0
    if coin_per_mp is not None:
        value_score = max(0.0, min(45.0, 45.0 - math.log10(max(1.0, coin_per_mp)) * 7.0))
    availability = 0.0
    if status == "Craftable now":
        availability = 28.0
    elif status == "Craftable if buying previous tier":
        availability = 22.0
    elif ah.active.lowest_bin:
        availability = 20.0
    elif status == "Soulbound / manual unlock":
        availability = 12.0
    rarity_bonus = max(0.0, 8.0 - RARITY_ORDER.get(entry.rarity, 3))
    stage_bonus = 8.0 if entry.recommended_for_stage in {"early", "early-mid", "any"} else 4.0
    confidence = 12.0 * min(ownership_confidence, ah.confidence or 1.0)
    penalty = 0.0
    if entry.uncertain_requirements:
        penalty += 12
    if entry.confidence == "medium":
        penalty += 4
    elif entry.confidence == "low" or entry.requires_manual_verification:
        penalty += 16
    if entry.soulbound:
        penalty += 8
    if ah.manipulated or ah.overpriced:
        penalty += 25
    if estimated_cost and estimated_cost > 25_000_000:
        penalty += 10
    score = max(0.0, min(100.0, value_score + availability + rarity_bonus + stage_bonus + confidence - penalty))
    reasons: list[str] = []
    if craft.total_cost is not None:
        reasons.append(f"craft cost {_coins(craft.total_cost)}")
    if ah.active.lowest_bin:
        reasons.append(f"lowest BIN {_coins(ah.active.lowest_bin)}")
    if coin_per_mp is not None:
        reasons.append(f"{_coins(coin_per_mp)}/MP")
    if missing:
        reasons.extend(missing[:2])
    if not reasons:
        reasons.append(status.lower())
    return score, reasons


def _coins(value: float | int | None) -> str:
    if value is None:
        return "?"
    absolute = abs(float(value))
    sign = "-" if float(value) < 0 else ""
    if absolute >= 1_000_000:
        return f"{sign}{absolute / 1_000_000:.1f}M"
    if absolute >= 1_000:
        return f"{sign}{absolute / 1_000:.1f}k"
    return f"{sign}{absolute:.0f}"
