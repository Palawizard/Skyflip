from __future__ import annotations

from dataclasses import dataclass, field

from .cofl import ActiveAuctions, SoldSummary


RARITY_ORDER = {
    "common": 1,
    "uncommon": 2,
    "rare": 3,
    "epic": 4,
    "legendary": 5,
    "mythic": 6,
    "special": 7,
    "very special": 8,
}

RARITY_MP = {
    "common": 3,
    "uncommon": 5,
    "rare": 8,
    "epic": 12,
    "legendary": 16,
    "mythic": 22,
    "special": 3,
    "very special": 5,
}

INCOMPLETE_ACCESSORY_DATA_WARNING = "Accessory data may be incomplete because inventory/accessory API data is missing or disabled."
FAMILY_SUFFIX_ORDER = {"talisman": 0, "ring": 1, "artifact": 2, "relic": 3}
FAMILY_TOKEN_STOPWORDS = {
    "ACCESSORY",
    "ARTIFACT",
    "BADGE",
    "BAR",
    "BELT",
    "BOWL",
    "BRACELET",
    "CHARM",
    "CHUNK",
    "CLOAK",
    "COMPASS",
    "FOOT",
    "GLOVE",
    "HAIR",
    "NECKLACE",
    "PAW",
    "REALM",
    "RELIC",
    "RING",
    "SLAB",
    "STICK",
    "TALISMAN",
    "THE",
}

@dataclass(frozen=True)
class AccessoryIngredient:
    item_id: str | None
    display_name: str
    quantity: float
    source: str = "bazaar"
    fixed_coin_cost: float | None = None


@dataclass(frozen=True)
class AccessoryRequirements:
    collections: dict[str, int] = field(default_factory=dict)
    skills: dict[str, int] = field(default_factory=dict)
    slayers: dict[str, int] = field(default_factory=dict)
    catacombs_floor_completions: list[int] = field(default_factory=list)
    skyblock_level: int | None = None
    quest_flags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AccessoryEntry:
    item_id: str
    aliases: list[str]
    display_name: str
    rarity: str
    family_id: str
    tier_index: int
    upgrade_from: str | None
    upgrade_to: str | None
    is_accessory: bool
    auctionable: bool
    soulbound: bool
    museum_required_if_any: str | None
    source_types: list[str]
    requirements: AccessoryRequirements
    recipe: list[AccessoryIngredient]
    recipe_verified: bool = False
    auto_generated: bool = False
    notes: str = ""
    recommended_for_stage: str = "any"
    manual_unlock_notes: str = ""
    uncertain_requirements: bool = False


@dataclass(frozen=True)
class AccessoryDatabase:
    accessories: list[AccessoryEntry]

    @property
    def by_id(self) -> dict[str, AccessoryEntry]:
        return {item.item_id: item for item in self.accessories}

    @property
    def by_family(self) -> dict[str, list[AccessoryEntry]]:
        families: dict[str, list[AccessoryEntry]] = {}
        for item in self.accessories:
            families.setdefault(item.family_id, []).append(item)
        for rows in families.values():
            rows.sort(key=lambda item: item.tier_index)
        return families


@dataclass(frozen=True)
class AccessoryOwnership:
    owned_exact: set[str]
    owned_family_best: dict[str, str]
    covered_by_higher_tier: set[str]
    warnings: list[str]
    confidence: float


@dataclass(frozen=True)
class BestOwnedFamilyTier:
    family_id: str
    item_id: str
    tier_index: int
    covered_lower_tiers: set[str]


@dataclass(frozen=True)
class AccessoryCraftCost:
    total_cost: float | None
    shopping_list: list[str]
    unavailable: list[str]
    used_previous_tier_purchase: bool = False


@dataclass(frozen=True)
class AccessoryAh:
    active: ActiveAuctions = field(default_factory=ActiveAuctions)
    sold: SoldSummary = field(default_factory=SoldSummary)
    safe_price: float | None = None
    confidence: float = 0.0
    manipulated: bool = False
    overpriced: bool = False
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AccessoryRecommendation:
    entry: AccessoryEntry
    owned_exact: bool
    owned_family_best: str | None
    covered_by_higher_tier: bool
    missing_useful: bool
    status: str
    best_method: str
    label: str
    estimated_cost: float | None
    craft_cost: float | None
    coin_per_mp: float | None
    mp_gain: int
    score: float
    reasons: list[str]
    missing_requirements: list[str]
    shopping_list: list[str]
    ah: AccessoryAh
    craftable_now: bool
    available_on_ah: bool
    confidence: float


@dataclass(frozen=True)
class AccessorySummary:
    magical_power: int | None
    accessory_bag_slots: int | None
    owned_count: int
    missing_count: int
    craftable_count: int
    ah_count: int
    cheapest_upgrades: list[AccessoryRecommendation]
    warnings: list[str]


@dataclass(frozen=True)
class AccessoryAnalysis:
    view: str
    summary: AccessorySummary
    recommendations: list[AccessoryRecommendation]
    craftable: list[AccessoryRecommendation]
    ah_available: list[AccessoryRecommendation]
    upgrades: list[AccessoryRecommendation]
    locked: list[AccessoryRecommendation]
    all_missing: list[AccessoryRecommendation]
    owned: list[AccessoryRecommendation]
    rows: list[AccessoryRecommendation]
    ownership: AccessoryOwnership


@dataclass(frozen=True)
class AccessoryFilters:
    view: str = "recommended"
    sort_key: str = "score"
    descending: bool = True
    rarities: set[str] = field(default_factory=set)
    max_price: float | None = None
    show_owned: bool = False
    show_locked: bool = False
    only_craftable: bool = False
    only_ah: bool = False
    hide_locked: bool = True
    search: str | None = None
    include_uncertain: bool = True
    include_manual: bool = True
    include_ah: bool = True
    include_craftable: bool = True
    max_recommendations: int = 15
    max_ah_checks: int = 60
