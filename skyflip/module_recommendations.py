from __future__ import annotations

from argparse import Namespace
from dataclasses import dataclass

from .module_presets import ModulePreset, get_module_preset
from .profile_parser import PlayerProfile


@dataclass(frozen=True)
class ModuleRecommendation:
    module_key: str
    preset: ModulePreset
    reasons: tuple[str, ...]


def recommend_module_presets(profile: PlayerProfile, args: Namespace) -> dict[str, ModuleRecommendation]:
    budget = _budget(profile, args)
    return {
        "bazaar": _recommend_bazaar(profile, budget),
        "craft": _recommend_craft(profile, budget),
        "accessories": _recommend_accessories(profile, budget),
        "compression": _recommend_compression(profile, budget),
        "ah-bin": _recommend_ah_bin(profile, budget),
    }


def recommend_module_preset(profile: PlayerProfile, args: Namespace, module_key: str) -> ModuleRecommendation:
    return recommend_module_presets(profile, args)[module_key]


def _recommend_bazaar(profile: PlayerProfile, budget: float) -> ModuleRecommendation:
    reasons = [_budget_reason(profile, budget)]
    if profile.is_restricted_mode:
        return _recommendation("bazaar", "safe", [f"Profile mode is {profile.profile_mode}; keep market exposure low.", *reasons])
    if budget < 5_000_000:
        return _recommendation("bazaar", "safe", [*reasons, "Small budget fits lower capital per flip.", "Conservative speed filters reduce slow-fill orders."])
    if budget >= 100_000_000:
        return _recommendation("bazaar", "risky", [*reasons, "Large budget can test more candidates manually.", "Higher capital cap stays isolated to this module preset."])
    return _recommendation("bazaar", "recommended", [*reasons, "Default speed and depth filters fit the current budget."])


def _recommend_craft(profile: PlayerProfile, budget: float) -> ModuleRecommendation:
    unlocks = _unlock_signal(profile)
    reasons = [_budget_reason(profile, budget), _unlock_reason(profile)]
    if profile.is_restricted_mode:
        return _recommendation("craft", "safe", [f"Profile mode is {profile.profile_mode}; normal AH craft flipping should stay conservative.", *reasons])
    if budget < 5_000_000:
        return _recommendation("craft", "safe", [*reasons, "Small budget should avoid tying up too much capital."])
    if unlocks < 3:
        return _recommendation("craft", "safe", [*reasons, "Few known unlock gates are confirmed, so strict filters are safer."])
    if budget >= 50_000_000 and unlocks >= 6:
        return _recommendation("craft", "risky", [*reasons, "Several real unlock gates are confirmed.", "Higher budget can support larger manual batches."])
    return _recommendation("craft", "recommended", [*reasons, "Confirmed unlock data supports standard craft filters."])


def _recommend_accessories(profile: PlayerProfile, budget: float) -> ModuleRecommendation:
    reasons = [_budget_reason(profile, budget)]
    owned_count = _known_accessory_count(profile)
    if not profile.inventory_api_enabled:
        return _recommendation(
            "accessories",
            "budget",
            [*reasons, "Inventory API data is missing, so ownership may be incomplete.", "Budget sorting keeps manual review focused."],
        )
    if profile.magical_power is None:
        return _recommendation(
            "accessories",
            "budget",
            [*reasons, f"{owned_count} known accessory items were detected.", "Magical Power is unavailable, so cheap MP is the safest target."],
        )
    if profile.magical_power < 150:
        return _recommendation(
            "accessories",
            "budget",
            [*reasons, f"Magical Power is {profile.magical_power}; cheap upgrades should come first.", f"{owned_count} known accessory items were detected."],
        )
    if _unlock_signal(profile) >= 5 and budget < 15_000_000:
        return _recommendation(
            "accessories",
            "craft-now",
            [*reasons, "Several collection, skill, or slayer gates are confirmed.", "Craftable accessories limit coin spend."],
        )
    if budget >= 50_000_000 and profile.magical_power >= 350:
        return _recommendation(
            "accessories",
            "completion",
            [*reasons, f"Magical Power is {profile.magical_power}.", "Large budget can review broader missing-accessory lists."],
        )
    if budget >= 15_000_000:
        return _recommendation(
            "accessories",
            "buy-ah",
            [*reasons, f"Magical Power is {profile.magical_power}.", "Budget can support AH-available upgrades after manual price checks."],
        )
    return _recommendation(
        "accessories",
        "recommended",
        [*reasons, f"Magical Power is {profile.magical_power}.", "Standard filters balance craftable and AH options."],
    )


def _recommend_compression(profile: PlayerProfile, budget: float) -> ModuleRecommendation:
    reasons = [_budget_reason(profile, budget)]
    if profile.is_restricted_mode:
        return _recommendation("compression", "conservative", [f"Profile mode is {profile.profile_mode}; keep conversions conservative.", *reasons])
    if budget < 5_000_000:
        return _recommendation("compression", "conservative", [*reasons, "Conservative mode limits capital tied in conversion chains."])
    if budget >= 100_000_000:
        return _recommendation("compression", "high-throughput", [*reasons, "Large budget can review more manual conversion candidates."])
    return _recommendation("compression", "balanced", [*reasons, "Balanced mode fits the current available coins."])


def _recommend_ah_bin(profile: PlayerProfile, budget: float) -> ModuleRecommendation:
    reasons = [_budget_reason(profile, budget)]
    if profile.is_restricted_mode:
        return _recommendation("ah-bin", "strict", [f"Profile mode is {profile.profile_mode}; use strict manual checks only.", *reasons])
    if budget < 10_000_000:
        return _recommendation("ah-bin", "strict", [*reasons, "Lower budget should require higher profit and faster sales."])
    if budget >= 100_000_000:
        return _recommendation("ah-bin", "more-candidates", [*reasons, "Large budget can manually review more candidates."])
    return _recommendation("ah-bin", "balanced", [*reasons, "Balanced checks keep the list readable."])


def _recommendation(module_key: str, preset_key: str, reasons: list[str]) -> ModuleRecommendation:
    cleaned = tuple(reason for reason in reasons if reason)[:4]
    return ModuleRecommendation(
        module_key=module_key,
        preset=get_module_preset(module_key, preset_key),
        reasons=cleaned,
    )


def _budget(profile: PlayerProfile, args: Namespace) -> float:
    value = getattr(args, "budget", None)
    return float(value if value is not None else profile.available_coins)


def _budget_reason(profile: PlayerProfile, budget: float) -> str:
    source = "custom budget" if budget != profile.available_coins else "purse plus bank"
    return f"Budget is {_coins(budget)} from {source}."


def _unlock_signal(profile: PlayerProfile) -> int:
    skill_count = sum(1 for value in profile.skills.values() if value >= 10)
    slayer_count = sum(1 for value in profile.slayer_levels.values() if value >= 2)
    collection_count = sum(1 for value in profile.collection_tiers.values() if value >= 3)
    dungeon_count = 1 if (profile.catacombs_level or 0) >= 5 else 0
    return skill_count + slayer_count + collection_count + dungeon_count


def _unlock_reason(profile: PlayerProfile) -> str:
    parts: list[str] = []
    if profile.collection_tiers:
        parts.append(f"{len(profile.collection_tiers)} collection tiers")
    if profile.skills:
        parts.append(f"{len(profile.skills)} skills")
    if profile.slayer_levels:
        parts.append(f"{len(profile.slayer_levels)} slayers")
    if profile.catacombs_level:
        parts.append(f"catacombs {profile.catacombs_level}")
    if not parts:
        return "No concrete unlock data is available."
    return "Known unlock data: " + ", ".join(parts[:4]) + "."


def _known_accessory_count(profile: PlayerProfile) -> int:
    return len(set(profile.item_ids).union(profile.accessory_bag_item_ids))


def _coins(value: float) -> str:
    return f"{value:,.0f}"
