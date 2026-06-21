# SkyFlip

Local terminal dashboard for manual Hypixel SkyBlock flipping analysis.

SkyFlip checks craft flips, Bazaar spread/order flips, Bazaar compression/decompression, manual AH BIN candidates, and accessory recommendations. It does not automate any in-game action.

## Stack

- Python 3.11+
- requests
- pytest

## How To Run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m skyflip dashboard
```

Dataset maintenance:

```powershell
python -m skyflip datasets summary
python -m skyflip datasets validate
python -m skyflip datasets migrate
python -m skyflip datasets refresh-bazaar-conversions
python -m skyflip datasets check-usage
```

Run tests:

```powershell
pytest
```

## Local Datasets

SkyFlip uses editable local datasets in `data/` for content Hypixel does not expose as one complete stable resource:

- `accessories.json`: Accessories Helper accessory IDs, families, requirements, source types, and optional recipes.
- `craft_recipes.json`: AH craft flip recipes.
- `bazaar_conversions.json`: Bazaar compression candidates validated against live Bazaar product IDs.
- `ah_watchlist.json`: manual AH BIN candidates filtered by budget and progression.

Dataset entries can include `verified`, `confidence`, `source_notes`, `last_verified`, `requires_manual_verification`, `disabled`, and `disabled_reason`. High-confidence entries can be used normally. Medium-confidence entries are shown more cautiously. Low-confidence or manually verified entries are hidden from default accessory recommendations unless uncertain data is enabled. Disabled entries are skipped.

Accessory families use `family_id` and `tier_index`; owning a higher tier covers lower tiers, so downgrade accessories are not recommended. Craft flips use recipe requirements and skip non-auctionable or disabled outputs. The AH watchlist applies budget fractions and progression requirements before scoring. Bazaar conversions are generated only when both input and output IDs exist on the live Hypixel Bazaar product list, then scored with Bazaar movement and manual effort penalties.
