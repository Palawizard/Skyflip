from skyflip.profile_parser import parse_profile


def test_parse_profile_extracts_player_progression():
    data = {
        "profile": {
            "banking": {"balance": 1_000_000},
            "members": {
                "uuid1": {
                    "player_name": "PalaMC",
                    "coin_purse": 250_000,
                    "experience_skill_combat": 1000,
                    "slayer": {"slayer_bosses": {"zombie": {"xp": 250, "claimed_levels": {"level_3": True}}}},
                    "collection": {"ROTTEN_FLESH": 1200},
                    "dungeons": {"dungeon_types": {"catacombs": {"experience": 1000, "tier_completions": {"1": 1}}}},
                    "leveling": {"experience": 1234},
                }
            },
        }
    }

    profile = parse_profile(data, player_name="PalaMC")

    assert profile.player_name == "PalaMC"
    assert profile.purse == 250_000
    assert profile.bank == 1_000_000
    assert profile.skills["combat"] >= 4
    assert profile.slayer_levels["zombie"] == 3
    assert profile.collection_tiers["ROTTEN_FLESH"] >= 5
    assert profile.catacombs_floor_completions[1] == 1
    assert profile.skyblock_level == 12


def test_parse_profile_ignores_non_numeric_skill_prefixed_values():
    data = {
        "members": {
            "uuid1": {
                "skill_tree": {"nodes": []},
                "player_data": {"experience": {"SKILL_COMBAT": 1000}},
            }
        }
    }

    profile = parse_profile(data)

    assert profile.skill_xp == {"combat": 1000.0}


def test_parse_profile_matches_member_by_resolved_uuid():
    data = {
        "members": {
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa": {"player_data": {"experience": {"SKILL_COMBAT": 50}}},
            "c0176e6d6fb94bdba27fad7e402efe99": {"player_id": "c0176e6d-6fb9-4bdb-a27f-ad7e402efe99"},
        }
    }

    profile = parse_profile(data, player_name="PalaMC", player_uuid="c0176e6d6fb94bdba27fad7e402efe99")

    assert profile.member_id == "c0176e6d6fb94bdba27fad7e402efe99"
    assert not profile.warnings
