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

Run tests:

```powershell
pytest
```
