from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .profile_parser import PlayerProfile


@dataclass(frozen=True)
class Ingredient:
    tag: str | None
    name: str
    amount: float
    source: str
    fixed_coin_cost: float | None = None


@dataclass(frozen=True)
class Requirements:
    skills: dict[str, int] = field(default_factory=dict)
    slayers: dict[str, int] = field(default_factory=dict)
    collections: dict[str, int] = field(default_factory=dict)
    catacombs_floor: int | None = None
    skyblock_level: int | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Recipe:
    tag: str
    name: str
    quantity: int
    ah_category: str | None
    ingredients: list[Ingredient]
    requirements: Requirements
    risk_tags: list[str]


@dataclass(frozen=True)
class Eligibility:
    eligible: bool
    confidence: float
    reasons: list[str]
    missing: list[str]


def load_recipes(path: Path | str = "data/craft_recipes.json") -> list[Recipe]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    recipes: list[Recipe] = []
    for item in raw.get("recipes", []):
        output = item["output"]
        requirements = item.get("requirements", {})
        recipes.append(
            Recipe(
                tag=output["tag"],
                name=output["display_name"],
                quantity=int(output.get("quantity", 1)),
                ah_category=output.get("ah_category"),
                ingredients=[
                    Ingredient(
                        tag=ingredient.get("item_tag"),
                        name=ingredient.get("display_name", ingredient.get("item_tag", "Coins")),
                        amount=float(ingredient.get("amount", 1)),
                        source=ingredient.get("source", "bazaar"),
                        fixed_coin_cost=ingredient.get("fixed_coin_cost"),
                    )
                    for ingredient in item.get("ingredients", [])
                ],
                requirements=Requirements(
                    skills={k.lower(): int(v) for k, v in requirements.get("min_skill_levels", {}).items()},
                    slayers={k.lower(): int(v) for k, v in requirements.get("min_slayer_levels", {}).items()},
                    collections={k.upper(): int(v) for k, v in requirements.get("min_collection_tiers", {}).items()},
                    catacombs_floor=requirements.get("min_catacombs_floor_completion"),
                    skyblock_level=requirements.get("min_skyblock_level"),
                    notes=list(requirements.get("notes", [])),
                ),
                risk_tags=list(item.get("risk_tags", [])),
            )
        )
    return recipes


def recipe_index(recipes: list[Recipe]) -> dict[str, Recipe]:
    return {recipe.tag: recipe for recipe in recipes}


def check_eligibility(recipe: Recipe, profile: PlayerProfile) -> Eligibility:
    reasons: list[str] = []
    missing: list[str] = []
    confidence = 1.0

    for skill, required in recipe.requirements.skills.items():
        actual = profile.skills.get(skill)
        if actual is None:
            confidence -= 0.15
            missing.append(f"unknown {skill} level, requires {required}")
        elif actual < required:
            missing.append(f"{skill} {actual} < {required}")
        else:
            reasons.append(f"{skill} {actual} >= {required}")

    for slayer, required in recipe.requirements.slayers.items():
        actual = profile.slayer_levels.get(slayer)
        if actual is None:
            confidence -= 0.2
            missing.append(f"unknown {slayer} slayer level, requires {required}")
        elif actual < required:
            missing.append(f"{slayer} slayer {actual} < {required}")
        else:
            reasons.append(f"{slayer} slayer {actual} >= {required}")

    for collection, required in recipe.requirements.collections.items():
        actual = profile.collection_tiers.get(collection)
        if actual is None:
            confidence -= 0.2
            missing.append(f"unknown {collection} collection tier, requires {required}")
        elif actual < required:
            missing.append(f"{collection} collection tier {actual} < {required}")
        else:
            reasons.append(f"{collection} collection tier {actual} >= {required}")

    if recipe.requirements.catacombs_floor is not None:
        required_floor = recipe.requirements.catacombs_floor
        if profile.catacombs_floor_completions.get(required_floor, 0) <= 0:
            missing.append(f"catacombs floor {required_floor} completion required")
        else:
            reasons.append(f"catacombs floor {required_floor} completed")

    if recipe.requirements.skyblock_level is not None:
        if profile.skyblock_level is None:
            confidence -= 0.15
            missing.append(f"unknown SkyBlock level, requires {recipe.requirements.skyblock_level}")
        elif profile.skyblock_level < recipe.requirements.skyblock_level:
            missing.append(f"SkyBlock level {profile.skyblock_level} < {recipe.requirements.skyblock_level}")
        else:
            reasons.append(f"SkyBlock level {profile.skyblock_level} >= {recipe.requirements.skyblock_level}")

    if recipe.requirements.notes:
        reasons.extend(recipe.requirements.notes)

    return Eligibility(
        eligible=not missing,
        confidence=max(0.0, min(1.0, confidence)),
        reasons=reasons or ["no explicit player requirement"],
        missing=missing,
    )
