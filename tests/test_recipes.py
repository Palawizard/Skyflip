import json
from pathlib import Path

from skyflip.profile_parser import PlayerProfile
from skyflip.recipes import Ingredient, Recipe, Requirements, check_eligibility, load_recipes


def test_eligibility_checks_skills_slayers_and_collections():
    recipe = Recipe(
        tag="TEST",
        name="Test Item",
        quantity=1,
        ah_category="accessory",
        auctionable=True,
        ingredients=[Ingredient("ROTTEN_FLESH", "Rotten Flesh", 1, "bazaar")],
        requirements=Requirements(skills={"combat": 12}, slayers={"zombie": 3}, collections={"ROTTEN_FLESH": 5}),
        risk_tags=[],
    )
    profile = PlayerProfile(
        player_name="PalaMC",
        member_id="uuid",
        purse=0,
        bank=0,
        skills={"combat": 14},
        slayer_levels={"zombie": 3},
        collection_tiers={"ROTTEN_FLESH": 6},
    )

    eligibility = check_eligibility(recipe, profile)

    assert eligibility.eligible
    assert eligibility.confidence == 1.0
    assert any("combat" in reason for reason in eligibility.reasons)


def test_eligibility_locks_missing_requirement():
    recipe = Recipe(
        tag="TEST",
        name="Test Item",
        quantity=1,
        ah_category=None,
        auctionable=True,
        ingredients=[],
        requirements=Requirements(slayers={"wolf": 6}),
        risk_tags=[],
    )
    profile = PlayerProfile(player_name="PalaMC", member_id="uuid", purse=0, bank=0, slayer_levels={"wolf": 3})

    eligibility = check_eligibility(recipe, profile)

    assert not eligibility.eligible
    assert eligibility.missing == ["wolf slayer 3 < 6"]


def test_event_limited_recipe_is_not_craft_flip_eligible():
    recipes = {recipe.tag: recipe for recipe in load_recipes("data/craft_recipes.json")}
    profile = PlayerProfile(
        player_name="PalaMC",
        member_id="uuid",
        purse=0,
        bank=0,
        collection_tiers={"BONE": 9},
        slayer_levels={"zombie": 3},
    )

    ring = check_eligibility(recipes["INTIMIDATION_RING"], profile)
    artifact = check_eligibility(recipes["INTIMIDATION_ARTIFACT"], profile)

    assert not ring.eligible
    assert "event-limited craft is not available for craft flips" in ring.missing
    assert not artifact.eligible
    assert "event-limited craft is not available for craft flips" in artifact.missing


def test_wand_of_mending_stays_eligible_for_zombie_slayer_three():
    recipes = {recipe.tag: recipe for recipe in load_recipes("data/craft_recipes.json")}
    profile = PlayerProfile(
        player_name="PalaMC",
        member_id="uuid",
        purse=0,
        bank=0,
        slayer_levels={"zombie": 3},
    )

    eligibility = check_eligibility(recipes["WAND_OF_MENDING"], profile)

    assert eligibility.eligible
    assert "zombie slayer 3 >= 3" in eligibility.reasons


def test_craft_recipe_data_marks_unmarketable_and_uses_priceable_tags():
    recipes = {recipe.tag: recipe for recipe in load_recipes("data/craft_recipes.json")}
    raw = json.loads(Path("data/craft_recipes.json").read_text(encoding="utf-8"))
    disabled = {
        row["output"]["tag"]: row
        for row in raw["recipes"]
        if row.get("disabled")
    }

    assert "RING_POTION_AFFINITY" not in recipes
    assert "WOOD_TALISMAN" not in recipes
    assert "WOLF_PAW" not in recipes
    assert not disabled["RING_POTION_AFFINITY"]["output"]["auctionable"]
    assert not disabled["WOOD_TALISMAN"]["output"]["auctionable"]
    assert not disabled["WOLF_PAW"]["output"]["auctionable"]
    assert any(ingredient.tag == "WATER_LILY" for ingredient in recipes["HEALING_TALISMAN"].ingredients)
    assert all(
        ingredient.source != "bazaar" or ingredient.tag not in {"LILY_PAD", "OLD_WOLF_TOOTH", "OAK_WOOD", "SPRUCE_WOOD", "BIRCH_WOOD", "DARK_OAK_WOOD"}
        for recipe in recipes.values()
        for ingredient in recipe.ingredients
    )
