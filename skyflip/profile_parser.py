from __future__ import annotations

import json
import base64
import gzip
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SKILL_XP_TOTALS = [
    0,
    50,
    175,
    375,
    675,
    1175,
    1925,
    2925,
    4425,
    6425,
    9925,
    14925,
    22425,
    32425,
    47425,
    67425,
    97425,
    147425,
    222425,
    322425,
    522425,
    822425,
    1222425,
    1722425,
    2322425,
    3022425,
    3822425,
    4722425,
    5722425,
    6822425,
    8022425,
    9322425,
    10722425,
    12222425,
    13822425,
    15522425,
    17322425,
    19222425,
    21222425,
    23322425,
    25522425,
    27822425,
    30222425,
    32722425,
    35322425,
    38072425,
    40972425,
    44072425,
    47472425,
    51172425,
    55172425,
    59472425,
    64072425,
    68972425,
    74172425,
    79672425,
    85472425,
    91572425,
    97972425,
    104672425,
    111672425,
]

SLAYER_XP_TOTALS = [0, 5, 15, 200, 1000, 5000, 20000, 100000, 400000, 1000000]

COLLECTION_TIER_THRESHOLDS: dict[str, list[int]] = {
    "ROTTEN_FLESH": [50, 100, 250, 500, 1000, 2500, 5000, 10000, 25000],
    "BONE": [50, 100, 250, 500, 1000, 2500, 5000, 10000, 25000],
    "IRON_INGOT": [50, 100, 250, 1000, 2500, 5000, 10000, 25000, 50000],
    "SUGAR_CANE": [100, 250, 500, 1000, 2000, 5000, 10000, 20000, 50000],
    "NETHER_STALK": [50, 100, 250, 500, 1000, 2500, 5000, 10000, 25000],
    "COBBLESTONE": [50, 100, 250, 1000, 2500, 5000, 10000, 40000, 70000],
    "LILY_PAD": [10, 25, 50, 100, 200, 500, 1000, 2500, 5000],
    "MAGMA_CREAM": [50, 100, 250, 500, 1000, 2500, 5000, 10000, 25000],
    "WHEAT": [50, 100, 250, 500, 1000, 2500, 5000, 10000, 15000],
    "OBSIDIAN": [50, 100, 250, 500, 1000, 2500, 5000, 10000, 25000],
    "MUSHROOM_COLLECTION": [50, 100, 250, 500, 1000, 2500, 5000, 10000, 25000],
    "POTATO_ITEM": [100, 200, 500, 1000, 2500, 5000, 10000, 25000, 50000],
    "MUTTON": [50, 100, 250, 500, 1000, 2500, 5000, 10000, 25000],
    "LAPIS_LAZULI": [50, 100, 250, 500, 1000, 2500, 5000, 10000, 25000],
    "BLAZE_ROD": [50, 100, 250, 500, 1000, 2500, 5000, 10000, 25000],
    "OAK_WOOD": [50, 100, 250, 500, 1000, 2500, 5000, 10000, 25000],
}


@dataclass(frozen=True)
class PlayerProfile:
    player_name: str
    member_id: str
    purse: float
    bank: float
    skills: dict[str, int] = field(default_factory=dict)
    skill_xp: dict[str, float] = field(default_factory=dict)
    slayer_levels: dict[str, int] = field(default_factory=dict)
    slayer_xp: dict[str, float] = field(default_factory=dict)
    catacombs_level: int | None = None
    catacombs_floor_completions: dict[int, int] = field(default_factory=dict)
    class_levels: dict[str, int] = field(default_factory=dict)
    collection_tiers: dict[str, int] = field(default_factory=dict)
    collection_amounts: dict[str, float] = field(default_factory=dict)
    crafted_minions: list[str] = field(default_factory=list)
    magical_power: int | None = None
    skyblock_level: int | None = None
    profile_mode: str | None = None
    profile_name: str | None = None
    profile_source: str = "local file"
    profile_fetched_at: float | None = None
    item_ids: list[str] = field(default_factory=list)
    accessory_bag_item_ids: list[str] = field(default_factory=list)
    inventory_api_enabled: bool = False
    accessory_bag_slots: int | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def available_coins(self) -> float:
        return self.purse + self.bank

    @property
    def is_restricted_mode(self) -> bool:
        mode = (self.profile_mode or "").lower()
        return mode in {"ironman", "bingo", "stranded", "island"}


def load_profile(path: Path | str, player_name: str | None = None, player_uuid: str | None = None) -> PlayerProfile:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return parse_profile(data, player_name=player_name, player_uuid=player_uuid)


def parse_profile(data: dict[str, Any], player_name: str | None = None, player_uuid: str | None = None) -> PlayerProfile:
    profile = data.get("profile", data)
    members = profile.get("members") or data.get("members") or {}
    warnings: list[str] = []
    member_id, member = _select_member(members, player_name, player_uuid)
    if member_id == "":
        raise ValueError("No profile members found in selected profile JSON")
    if player_name and not _member_matches_name(member, player_name, member_id, player_uuid):
        warnings.append(f"Could not confidently match player {player_name}; selected member {member_id}")

    skill_xp = _extract_skill_xp(member)
    skills = {skill: xp_to_level(xp) for skill, xp in skill_xp.items()}
    slayer_xp, slayer_levels = _extract_slayers(member)
    collection_amounts, collection_tiers = _extract_collections(member)
    catacombs_xp = _deep_get(member, ["dungeons", "dungeon_types", "catacombs", "experience"], 0) or 0
    catacombs_level = xp_to_level(float(catacombs_xp))
    floor_completions = _extract_floor_completions(member)

    purse = float(member.get("coin_purse") or _deep_get(member, ["currencies", "coin_purse"], 0) or 0)
    bank = float(_deep_get(profile, ["banking", "balance"], profile.get("bank_balance", 0)) or 0)
    mode = profile.get("game_mode") or profile.get("mode") or data.get("game_mode")
    profile_name = profile.get("cute_name") or data.get("cute_name")
    item_ids, inventory_api_enabled = _extract_item_ids(member)
    accessory_bag_item_ids, accessory_bag_slots = _extract_accessory_bag_items(member)

    return PlayerProfile(
        player_name=player_name or str(member.get("player_name") or member.get("display_name") or member_id),
        member_id=member_id,
        purse=purse,
        bank=bank,
        skills=skills,
        skill_xp=skill_xp,
        slayer_levels=slayer_levels,
        slayer_xp=slayer_xp,
        catacombs_level=catacombs_level,
        catacombs_floor_completions=floor_completions,
        class_levels=_extract_class_levels(member),
        collection_tiers=collection_tiers,
        collection_amounts=collection_amounts,
        crafted_minions=list(member.get("crafted_generators", [])),
        magical_power=_extract_int(member, ["accessory_bag_storage", "highest_magical_power"]),
        skyblock_level=_extract_skyblock_level(member),
        profile_mode=mode,
        profile_name=str(profile_name) if profile_name else None,
        profile_source=str(data.get("_skyflip_profile_source") or "local file"),
        profile_fetched_at=_float_or_none(data.get("_skyflip_profile_fetched_at")),
        item_ids=sorted(item_ids),
        accessory_bag_item_ids=sorted(accessory_bag_item_ids),
        inventory_api_enabled=inventory_api_enabled,
        accessory_bag_slots=accessory_bag_slots,
        warnings=warnings,
    )


def xp_to_level(xp: float, totals: list[int] = SKILL_XP_TOTALS) -> int:
    level = 0
    for required in totals:
        if xp >= required:
            level += 1
        else:
            break
    return max(0, level - 1)


def slayer_xp_to_level(xp: float) -> int:
    return xp_to_level(xp, SLAYER_XP_TOTALS)


def _select_member(members: dict[str, Any], player_name: str | None, player_uuid: str | None = None) -> tuple[str, dict[str, Any]]:
    if not members:
        return "", {}
    if player_name:
        for member_id, member in members.items():
            if _member_matches_name(member, player_name, member_id, player_uuid):
                return member_id, member
    return max(members.items(), key=lambda item: _member_completeness(item[1]))


def _member_matches_name(member: dict[str, Any], player_name: str, member_id: str, player_uuid: str | None = None) -> bool:
    wanted = player_name.lower()
    wanted_uuid = _compact_uuid(player_uuid)
    fields = [
        member.get("player_name"),
        member.get("display_name"),
        member.get("cute_name"),
        member.get("name"),
        member_id,
    ]
    if any(str(value).lower() == wanted for value in fields if value):
        return True
    member_ids = [member_id, member.get("player_id"), member.get("uuid")]
    return bool(wanted_uuid) and any(_compact_uuid(value) == wanted_uuid for value in member_ids if value)


def _member_completeness(member: dict[str, Any]) -> int:
    return len(json.dumps(member, default=str))


def _extract_skill_xp(member: dict[str, Any]) -> dict[str, float]:
    experience = _deep_get(member, ["player_data", "experience"], {}) or {}
    values: dict[str, float] = {}
    for key, value in {**member, **experience}.items():
        key_lower = str(key).lower()
        numeric = _float_or_none(value)
        if numeric is None:
            continue
        if key_lower.startswith("experience_skill_"):
            values[key_lower.removeprefix("experience_skill_")] = numeric
        elif key_lower.startswith("skill_"):
            values[key_lower.removeprefix("skill_")] = numeric
    for key, value in experience.items():
        key_upper = str(key).upper()
        numeric = _float_or_none(value)
        if key_upper.startswith("SKILL_") and numeric is not None:
            values[key_upper.removeprefix("SKILL_").lower()] = numeric
    return values


def _extract_slayers(member: dict[str, Any]) -> tuple[dict[str, float], dict[str, int]]:
    bosses = _deep_get(member, ["slayer", "slayer_bosses"], {}) or {}
    if not bosses:
        bosses = member.get("slayer_bosses") or {}
    if not bosses:
        bosses = _deep_get(member, ["player_data", "slayer", "slayer_bosses"], {}) or {}
    xp_by_boss: dict[str, float] = {}
    levels: dict[str, int] = {}
    for boss, info in bosses.items():
        xp = float(info.get("xp", 0) or 0)
        claimed = info.get("claimed_levels", {}) or {}
        explicit = [int(str(k).split("_")[-1]) for k, v in claimed.items() if v and str(k).split("_")[-1].isdigit()]
        xp_by_boss[str(boss).lower()] = xp
        levels[str(boss).lower()] = max([slayer_xp_to_level(xp), *explicit], default=0)
    return xp_by_boss, levels


def _extract_collections(member: dict[str, Any]) -> tuple[dict[str, float], dict[str, int]]:
    raw_collection = member.get("collection") or {}
    amounts = {
        str(k).upper(): numeric
        for k, v in raw_collection.items()
        if (numeric := _float_or_none(v)) is not None
    }
    tiers: dict[str, int] = {}
    unlocked = _deep_get(member, ["player_data", "unlocked_coll_tiers"], member.get("unlocked_coll_tiers", [])) or []
    for entry in unlocked:
        if not isinstance(entry, str) or "_" not in entry:
            continue
        tag, tier_text = entry.rsplit("_", 1)
        if tier_text.isdigit():
            tiers[tag.upper()] = max(tiers.get(tag.upper(), 0), int(tier_text))
    for tag, amount in amounts.items():
        thresholds = COLLECTION_TIER_THRESHOLDS.get(tag)
        if thresholds:
            tiers[tag] = max(tiers.get(tag, 0), sum(1 for threshold in thresholds if amount >= threshold))
    return amounts, tiers


def _extract_floor_completions(member: dict[str, Any]) -> dict[int, int]:
    completions = _deep_get(member, ["dungeons", "dungeon_types", "catacombs", "tier_completions"], {}) or {}
    values: dict[int, int] = {}
    for floor, count in completions.items():
        text = str(floor).lower().replace("floor_", "")
        if text.isdigit():
            values[int(text)] = int(count or 0)
    return values


def _extract_class_levels(member: dict[str, Any]) -> dict[str, int]:
    classes = _deep_get(member, ["dungeons", "player_classes"], {}) or {}
    return {
        str(name).lower(): xp_to_level(float(info.get("experience", 0) or 0))
        for name, info in classes.items()
        if isinstance(info, dict)
    }


def _extract_skyblock_level(member: dict[str, Any]) -> int | None:
    raw = _deep_get(member, ["leveling", "experience"], None)
    if raw is None:
        return None
    return int(float(raw) // 100)


def _extract_item_ids(member: dict[str, Any]) -> tuple[set[str], bool]:
    found: set[str] = set()
    inventory = member.get("inventory")
    inventory_api_enabled = isinstance(inventory, dict)
    _collect_item_id_tokens(member, found)
    return found, inventory_api_enabled


def _collect_item_id_tokens(value: Any, found: set[str]) -> None:
    if isinstance(value, dict):
        raw_data = value.get("data")
        if isinstance(raw_data, str) and raw_data:
            for token in _tokens_from_inventory_blob(raw_data):
                found.add(token)
        for key in ("id", "item_id", "itemId", "tag", "skyblock_id"):
            token = value.get(key)
            if isinstance(token, str):
                normalized = _normalize_item_id(token)
                if normalized:
                    found.add(normalized)
        for child in value.values():
            _collect_item_id_tokens(child, found)
    elif isinstance(value, list):
        for child in value:
            _collect_item_id_tokens(child, found)
    elif isinstance(value, str):
        normalized = _normalize_item_id(value)
        if normalized:
            found.add(normalized)


def _tokens_from_inventory_blob(value: str) -> set[str]:
    try:
        raw = gzip.decompress(base64.b64decode(value))
    except Exception:
        return set()
    return {
        token.decode("ascii", errors="ignore")
        for token in re.findall(rb"[A-Z][A-Z0-9_]{2,}", raw)
        if b" " not in token
    }


def _normalize_item_id(value: str) -> str | None:
    text = value.strip().upper()
    if not text or len(text) < 3:
        return None
    if not re.fullmatch(r"[A-Z0-9_:-]+", text):
        return None
    if ":" in text:
        text = text.rsplit(":", 1)[-1]
    return text


def _extract_accessory_bag_items(member: dict[str, Any]) -> tuple[set[str], int | None]:
    bag = _deep_get(member, ["inventory", "bag_contents", "talisman_bag"], None)
    if not isinstance(bag, dict):
        return set(), None
    data = bag.get("data")
    if not isinstance(data, str) or not data:
        return set(), None
    try:
        raw = gzip.decompress(base64.b64decode(data))
    except Exception:
        return set(), None
    ids = {
        token.decode("ascii", errors="ignore")
        for token in re.findall(rb"id..([A-Z][A-Z0-9_]{2,})", raw)
        if _looks_like_skyblock_item_id(token.decode("ascii", errors="ignore"))
    }
    occupied = raw.count(b"ExtraAttributes")
    return ids, occupied or None


def _looks_like_skyblock_item_id(value: str) -> bool:
    if not _normalize_item_id(value):
        return False
    if value.isdigit():
        return False
    return any(
        marker in value
        for marker in (
            "TALISMAN",
            "RING",
            "ARTIFACT",
            "CHARM",
            "BADGE",
            "HAIR",
            "NECKLACE",
            "BELT",
            "CLOAK",
            "GLOVE",
            "BRACELET",
            "BOWL",
            "COMPASS",
            "CHOCOLATE",
            "FOOT",
            "PAW",
            "CHICKEN",
        )
    )


def _extract_int(data: dict[str, Any], path: list[str]) -> int | None:
    value = _deep_get(data, path, None)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "."))
        except ValueError:
            return None
    return None


def _compact_uuid(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).replace("-", "").lower()
    return text if len(text) == 32 else None


def _deep_get(data: dict[str, Any], path: list[str], default: Any = None) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current
