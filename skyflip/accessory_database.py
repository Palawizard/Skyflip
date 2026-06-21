from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from .accessory_models import (
    FAMILY_SUFFIX_ORDER,
    FAMILY_TOKEN_STOPWORDS,
    RARITY_ORDER,
    AccessoryDatabase,
    AccessoryEntry,
    AccessoryIngredient,
    AccessoryRequirements,
)


_DATABASE_CACHE: dict[Path, tuple[int, AccessoryDatabase]] = {}


def load_accessory_database(path: str | Path = "data/accessories.json") -> AccessoryDatabase:
    target = Path(path)
    try:
        mtime_ns = target.stat().st_mtime_ns
    except OSError:
        mtime_ns = -1
    cached = _DATABASE_CACHE.get(target.resolve() if target.exists() else target)
    if cached and cached[0] == mtime_ns:
        return cached[1]
    raw = json.loads(target.read_text(encoding="utf-8"))
    accessories: list[AccessoryEntry] = []
    for item in raw.get("accessories", []):
        if item.get("disabled"):
            continue
        req = item.get("requirements") or {}
        recipe = item.get("recipe") or {}
        ingredients = recipe.get("ingredients", recipe if isinstance(recipe, list) else [])
        accessories.append(
            AccessoryEntry(
                item_id=normalize_item_id(item["item_id"]),
                aliases=[normalize_item_id(str(value)) for value in item.get("aliases", [])],
                display_name=str(item["display_name"]),
                rarity=str(item.get("rarity", "common")).lower(),
                family_id=str(item.get("family_id") or item["item_id"]),
                tier_index=int(item.get("tier_index", 0)),
                upgrade_from=_optional_item_id(item.get("upgrade_from")),
                upgrade_to=_optional_item_id(item.get("upgrade_to")),
                is_accessory=bool(item.get("is_accessory", True)),
                auctionable=bool(item.get("auctionable", True)),
                soulbound=bool(item.get("soulbound", False)),
                museum_required_if_any=item.get("museum_required_if_any"),
                source_types=[str(value).lower() for value in item.get("source_types", [])],
                requirements=AccessoryRequirements(
                    collections={str(k).upper(): int(v) for k, v in (req.get("collections") or {}).items()},
                    skills={str(k).lower(): int(v) for k, v in (req.get("skills") or {}).items()},
                    slayers={str(k).lower(): int(v) for k, v in (req.get("slayers") or {}).items()},
                    catacombs_floor_completions=[int(v) for v in req.get("catacombs_floor_completions", [])],
                    skyblock_level=int(req["skyblock_level"]) if req.get("skyblock_level") is not None else None,
                    quest_flags=[str(v) for v in req.get("quest_flags", [])],
                ),
                recipe=[
                    AccessoryIngredient(
                        item_id=_optional_item_id(ingredient.get("item_id")),
                        display_name=str(ingredient.get("display_name") or ingredient.get("item_id") or "Coins"),
                        quantity=float(ingredient.get("quantity", 1)),
                        source=str(ingredient.get("source", "bazaar")).lower(),
                        fixed_coin_cost=_float_or_none(ingredient.get("fixed_coin_cost")),
                    )
                    for ingredient in ingredients
                    if isinstance(ingredient, dict)
                ],
                recipe_verified=bool(item.get("recipe_verified", False)),
                auto_generated=bool(item.get("auto_generated", False)),
                notes=str(item.get("notes", "")),
                recommended_for_stage=str(item.get("recommended_for_stage", "any")),
                manual_unlock_notes=str(item.get("manual_unlock_notes", "")),
                uncertain_requirements=bool(item.get("uncertain_requirements", False) or item.get("confidence") == "low"),
                verified=bool(item.get("verified", False)),
                confidence=str(item.get("confidence", "medium")).lower(),
                source_notes=str(item.get("source_notes", "")),
                last_verified=str(item.get("last_verified", "")),
                requires_manual_verification=bool(item.get("requires_manual_verification", False)),
                recommendation_eligible=bool(item.get("recommendation_eligible", True)),
                ownership_detection_only=bool(item.get("ownership_detection_only", False)),
                market_source=str(item.get("market_source", "ah")).lower(),
                cofl_auction_supported=bool(item.get("cofl_auction_supported", True)),
                cofl_price_supported=bool(item.get("cofl_price_supported", True)),
            )
        )
    database = AccessoryDatabase(_infer_accessory_families([item for item in accessories if item.is_accessory]))
    _DATABASE_CACHE[target.resolve() if target.exists() else target] = (mtime_ns, database)
    return database


def augment_with_hypixel_accessories(database: AccessoryDatabase, http: Any) -> AccessoryDatabase:
    if http is None:
        return database
    try:
        result = http.get_json("https://api.hypixel.net/v2/resources/skyblock/items")
    except Exception:
        return database
    payload = result.payload if isinstance(result.payload, dict) else {}
    rows = payload.get("items")
    if not isinstance(rows, list):
        return database
    by_id = database.by_id
    known_aliases = {alias for item in database.accessories for alias in item.aliases}
    accessories = list(database.accessories)
    for row in rows:
        if not isinstance(row, dict) or row.get("category") != "ACCESSORY":
            continue
        item_id = _optional_item_id(row.get("id"))
        if not item_id or item_id in by_id or item_id in known_aliases:
            continue
        name = str(row.get("name") or item_id.replace("_", " ").title())
        rarity = _resource_rarity(row.get("tier"))
        soulbound = bool(row.get("soulbound", False))
        family_id, tier_index = _derived_family(item_id)
        accessories.append(
            AccessoryEntry(
                item_id=item_id,
                aliases=[],
                display_name=name,
                rarity=rarity,
                family_id=family_id,
                tier_index=tier_index,
                upgrade_from=None,
                upgrade_to=None,
                is_accessory=True,
                auctionable=not soulbound,
                soulbound=soulbound,
                museum_required_if_any=None,
                source_types=["manual"] if soulbound else ["ah"],
                requirements=AccessoryRequirements(),
                recipe=[],
                recipe_verified=False,
                auto_generated=True,
                notes="Imported from Hypixel item resources; requirements/source details unknown.",
                recommended_for_stage="any",
                manual_unlock_notes="Verify source in game or check AH if tradable.",
                uncertain_requirements=True,
                verified=True,
                confidence="low",
                source_notes="Imported from Hypixel item resources; accessory metadata only.",
                last_verified="",
                requires_manual_verification=True,
                recommendation_eligible=True,
                ownership_detection_only=True,
                market_source="ah" if not soulbound else "manual",
                cofl_auction_supported=not soulbound,
                cofl_price_supported=not soulbound,
            )
        )
    return AccessoryDatabase(_infer_accessory_families(accessories))

def normalize_item_id(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"(?:\u00a7|&)[0-9A-FK-ORa-fk-or]", "", text)
    text = text.upper().replace(" ", "_")
    if re.fullmatch(r"[A-Z0-9_:-]+", text) and ":" in text:
        text = text.rsplit(":", 1)[-1]
    text = re.sub(r"[^A-Z0-9_:-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text


def _resource_rarity(value: Any) -> str:
    rarity = str(value or "common").lower().replace("_", " ")
    return rarity if rarity in RARITY_ORDER else "common"


def _derived_family(item_id: str) -> tuple[str, int]:
    parts = item_id.lower().split("_")
    if parts and parts[-1] in FAMILY_SUFFIX_ORDER:
        return "_".join(parts[:-1]) or item_id.lower(), FAMILY_SUFFIX_ORDER[parts[-1]]
    if len(parts) > 1 and parts[-1].isdigit():
        return "_".join(parts[:-1]), int(parts[-1])
    return item_id.lower(), 0


def _infer_accessory_families(accessories: list[AccessoryEntry]) -> list[AccessoryEntry]:
    inferred = [_infer_direct_family(item) for item in accessories]
    replace_by_index: dict[int, AccessoryEntry] = {}
    assigned: set[int] = set()
    groups = _token_family_candidates(inferred)
    for token, indexes in groups:
        available = [index for index in indexes if index not in assigned and _can_infer_family(inferred[index])]
        if len(available) < 2:
            continue
        rarities = {inferred[index].rarity for index in available}
        if len(rarities) < 2 or len(rarities) != len(available):
            continue
        ordered = sorted(
            available,
            key=lambda index: (
                RARITY_ORDER.get(inferred[index].rarity, 0),
                _numeric_tier_hint(inferred[index].item_id),
                inferred[index].display_name.lower(),
            ),
        )
        family_id = token.lower()
        for tier_index, index in enumerate(ordered):
            previous_id = inferred[ordered[tier_index - 1]].item_id if tier_index > 0 else None
            next_id = inferred[ordered[tier_index + 1]].item_id if tier_index + 1 < len(ordered) else None
            replace_by_index[index] = replace(
                inferred[index],
                family_id=family_id,
                tier_index=tier_index,
                upgrade_from=inferred[index].upgrade_from or previous_id,
                upgrade_to=inferred[index].upgrade_to or next_id,
            )
            assigned.add(index)
    return [replace_by_index.get(index, item) for index, item in enumerate(inferred)]


def _infer_direct_family(item: AccessoryEntry) -> AccessoryEntry:
    if not _can_infer_family(item):
        return item
    family_id, tier_index = _derived_family(item.item_id)
    if family_id == item.item_id.lower():
        return item
    return replace(item, family_id=family_id, tier_index=tier_index)


def _can_infer_family(item: AccessoryEntry) -> bool:
    return item.auto_generated or item.family_id == item.item_id.lower()


def _token_family_candidates(accessories: list[AccessoryEntry]) -> list[tuple[str, list[int]]]:
    token_to_indexes: dict[str, list[int]] = {}
    for index, item in enumerate(accessories):
        if not _can_infer_family(item):
            continue
        if item.family_id != item.item_id.lower():
            continue
        for token in _family_candidate_tokens(item):
            token_to_indexes.setdefault(token, []).append(index)
    candidates = [
        (token, indexes)
        for token, indexes in token_to_indexes.items()
        if 2 <= len(indexes) <= 6
    ]
    return sorted(candidates, key=lambda row: (len(row[1]), len(row[0]), row[0]), reverse=True)


def _family_candidate_tokens(item: AccessoryEntry) -> set[str]:
    text = f"{item.item_id} {item.display_name}"
    tokens = {
        token
        for token in re.findall(r"[A-Z0-9]{4,}", normalize_item_id(text))
        if token not in FAMILY_TOKEN_STOPWORDS and not token.isdigit()
    }
    return tokens


def _numeric_tier_hint(item_id: str) -> int:
    parts = normalize_item_id(item_id).split("_")
    if parts and parts[-1].isdigit():
        return int(parts[-1])
    return 0

def _optional_item_id(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return normalize_item_id(str(value))


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
