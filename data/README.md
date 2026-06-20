# SkyFlip Data Files

Local datasets are editable, but entries should stay conservative. Prefer `verified`, `confidence`, `source_notes`, and `last_verified` on every new entry.

- `accessories.json`: Accessory database for Accessories Helper. Families must share `family_id`, with higher upgrades using higher `tier_index`.
- `ah_watchlist.json`: AH BIN candidates for manual underpriced checks. Items are filtered by budget fraction and progression requirements.
- `bazaar_conversions.json`: Bazaar compression candidates. Input and output products must exist on live Hypixel Bazaar.
- `craft_recipes.json`: AH craft flip recipes. Non-auctionable, disabled, manual-only, or event-only outputs are skipped by normal craft recommendations. Some recipe shapes come from NotEnoughUpdates-REPO and are cross-checked against Hypixel item resources and live Bazaar product IDs; medium-confidence entries should still be manually verified before aggressive use.

Useful commands:

```powershell
python -m skyflip datasets validate
python -m skyflip datasets migrate
python -m skyflip datasets refresh-bazaar-conversions
python -m skyflip datasets summary
```
