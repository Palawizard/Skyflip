from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from .cache import FileCache
from .dataset_validation import (
    DATASET_FILES,
    DatasetValidationResult,
    compact_warning,
    validate_accessories,
    validate_ah_watchlist,
    validate_all_datasets,
    validate_bazaar_conversions,
    validate_craft_recipes,
)
from .http import HttpClient


BAZAAR_URL = "https://api.hypixel.net/v2/skyblock/bazaar"
TODAY = "2026-06-20"


def add_dataset_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("datasets", help="Validate and maintain local SkyFlip datasets")
    dataset_subparsers = parser.add_subparsers(dest="datasets_command")

    validate = dataset_subparsers.add_parser("validate", help="Validate local datasets")
    validate.add_argument("--offline", action="store_true", help="Skip live Bazaar product ID checks")

    migrate = dataset_subparsers.add_parser("migrate", help="Normalize local dataset files")
    migrate.add_argument("--offline", action="store_true", help="Skip live Bazaar product ID checks")

    dataset_subparsers.add_parser("summary", help="Print local dataset counts")

    refresh = dataset_subparsers.add_parser("refresh-bazaar-conversions", help="Refresh generated Bazaar conversion data")
    refresh.add_argument("--offline", action="store_true", help="Validate existing conversions without fetching live products")

    dataset_subparsers.add_parser("check-usage", help="Check that app modules load local datasets")


def run_dataset_command(args: argparse.Namespace) -> int:
    command = getattr(args, "datasets_command", None)
    if command == "validate":
        return validate_command(offline=bool(getattr(args, "offline", False)))
    if command == "migrate":
        return migrate_command(offline=bool(getattr(args, "offline", False)))
    if command == "summary":
        return summary_command()
    if command == "refresh-bazaar-conversions":
        return refresh_bazaar_conversions_command(offline=bool(getattr(args, "offline", False)))
    if command == "check-usage":
        return check_usage_command()
    print("Choose a datasets subcommand: validate, migrate, summary, refresh-bazaar-conversions, or check-usage.")
    return 2


def validate_command(*, offline: bool = False) -> int:
    product_ids = None if offline else fetch_bazaar_product_ids()
    result = validate_all_datasets(bazaar_product_ids=product_ids)
    print_validation_summary(result)
    return 1 if result.errors else 0


def migrate_command(*, offline: bool = False) -> int:
    backup_dir = backup_dataset_files()
    product_ids = None if offline else fetch_bazaar_product_ids()
    for name, path in DATASET_FILES.items():
        raw = _read_json(path)
        if name == "accessories":
            raw["accessories"] = [_normalize_accessory(row) for row in raw.get("accessories", []) if isinstance(row, dict)]
        elif name == "ah_watchlist":
            raw["items"] = [_normalize_watch_item(row) for row in raw.get("items", []) if isinstance(row, dict)]
        elif name == "bazaar_conversions":
            raw["conversions"] = [
                _normalize_conversion(row, product_ids=product_ids)
                for row in raw.get("conversions", [])
                if isinstance(row, dict)
            ]
        elif name == "craft_recipes":
            raw["recipes"] = [_normalize_recipe(row) for row in raw.get("recipes", []) if isinstance(row, dict)]
        _write_json(path, raw)
    result = validate_all_datasets(bazaar_product_ids=product_ids)
    print(f"Backed up datasets to {backup_dir}")
    print_validation_summary(result)
    return 1 if result.errors else 0


def summary_command() -> int:
    accessories = _read_json(DATASET_FILES["accessories"]).get("accessories", [])
    watchlist = _read_json(DATASET_FILES["ah_watchlist"]).get("items", [])
    conversions = _read_json(DATASET_FILES["bazaar_conversions"]).get("conversions", [])
    recipes = _read_json(DATASET_FILES["craft_recipes"]).get("recipes", [])
    families = {row.get("family_id") for row in accessories if isinstance(row, dict) and not row.get("disabled")}
    craftable_accessories = [
        row for row in accessories
        if isinstance(row, dict) and not row.get("disabled") and "craft" in [str(v).lower() for v in row.get("source_types", [])]
    ]
    auctionable_accessories = [row for row in accessories if isinstance(row, dict) and row.get("auctionable") and not row.get("disabled")]
    result = validate_all_datasets(bazaar_product_ids=None)

    print("Dataset summary")
    print(f"- Accessories: {len(accessories)}")
    print(f"- Accessory families: {len(families)}")
    print(f"- Craftable accessories: {len(craftable_accessories)}")
    print(f"- Auctionable accessories: {len(auctionable_accessories)}")
    print(f"- AH watchlist items: {len(watchlist)}")
    print(f"- Bazaar conversions: {len(conversions)}")
    print(f"- Craft recipes: {len(recipes)}")
    print(f"- Disabled entries: {result.disabled_entries}")
    print(f"- Uncertain entries: {result.uncertain_entries}")
    print(f"- Validation errors: {len(result.errors)}")
    print(f"- Validation warnings: {len(result.warnings)}")
    return 0


def refresh_bazaar_conversions_command(*, offline: bool = False) -> int:
    backup_dir = backup_dataset_files(files=[DATASET_FILES["bazaar_conversions"]])
    product_ids = None if offline else fetch_bazaar_product_ids()
    raw = _read_json(DATASET_FILES["bazaar_conversions"])
    existing = [
        _normalize_conversion(row, product_ids=product_ids)
        for row in raw.get("conversions", [])
        if isinstance(row, dict)
    ]
    generated = [] if product_ids is None else generate_obvious_compressions(product_ids)
    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in [*existing, *generated]:
        key = (row["input_product_id"], row["output_product_id"], row.get("conversion_type", "compression"))
        by_key.setdefault(key, row)
    raw["conversions"] = sorted(by_key.values(), key=lambda row: (row.get("disabled", False), row["input_product_id"], row["output_product_id"]))
    _write_json(DATASET_FILES["bazaar_conversions"], raw)
    result = validate_bazaar_conversions(DATASET_FILES["bazaar_conversions"], bazaar_product_ids=product_ids)
    print(f"Backed up Bazaar conversions to {backup_dir}")
    print(f"Generated {len(generated)} obvious live Bazaar compression candidates.")
    print_validation_summary(result)
    return 1 if result.errors else 0


def check_usage_command() -> int:
    checks = [
        ("Accessories dataset", Path("skyflip/accessory_database.py"), "data/accessories.json"),
        ("Craft recipes dataset", Path("skyflip/recipes.py"), "data/craft_recipes.json"),
        ("Bazaar conversions dataset", Path("skyflip/bazaar_compression.py"), "data/bazaar_conversions.json"),
        ("AH watchlist dataset", Path("skyflip/ah_underpriced.py"), "data/ah_watchlist.json"),
    ]
    errors = 0
    print("Dataset usage")
    for label, module_path, default_path in checks:
        text = module_path.read_text(encoding="utf-8")
        ok = default_path in text
        print(f"- {label}: {'ok' if ok else 'missing'}")
        errors += 0 if ok else 1
    dashboard_text = Path("skyflip/dashboard.py").read_text(encoding="utf-8")
    for loader in ("load_recipes", "load_conversions", "load_watchlist", "analyze_accessories"):
        ok = loader in dashboard_text
        print(f"- Dashboard calls {loader}: {'ok' if ok else 'missing'}")
        errors += 0 if ok else 1
    return 1 if errors else 0


def runtime_dataset_warning(*, paths: dict[str, Path | str] | None = None) -> str | None:
    paths = paths or {}
    result = DatasetValidationResult()
    selected = set(paths) if paths else set(DATASET_FILES)
    if "accessories" in selected:
        result.extend(validate_accessories(Path(paths.get("accessories", DATASET_FILES["accessories"]))))
    if "ah_watchlist" in selected:
        result.extend(validate_ah_watchlist(Path(paths.get("ah_watchlist", DATASET_FILES["ah_watchlist"]))))
    if "bazaar_conversions" in selected:
        result.extend(validate_bazaar_conversions(Path(paths.get("bazaar_conversions", DATASET_FILES["bazaar_conversions"]))))
    if "craft_recipes" in selected:
        result.extend(validate_craft_recipes(Path(paths.get("craft_recipes", DATASET_FILES["craft_recipes"]))))
    if not result.errors:
        return None
    count = len(result.errors)
    return f"{count} dataset error{'s' if count != 1 else ''}; run `python -m skyflip datasets validate` for details."


def fetch_bazaar_product_ids() -> set[str]:
    http = HttpClient(FileCache(ttl_seconds=300))
    result = http.get_json(BAZAAR_URL)
    payload = result.payload if isinstance(result.payload, dict) else {}
    products = payload.get("products") if isinstance(payload.get("products"), dict) else {}
    return {str(product_id) for product_id in products}


def generate_obvious_compressions(product_ids: set[str]) -> list[dict[str, Any]]:
    generated: list[dict[str, Any]] = []
    for product_id in sorted(product_ids):
        if product_id.startswith("ENCHANTED_") or ":" in product_id:
            continue
        output = f"ENCHANTED_{product_id}"
        if output in product_ids:
            generated.append(_generated_conversion(product_id, output, 160, 1))
    for product_id in sorted(product_ids):
        if not product_id.startswith("ENCHANTED_") or product_id.endswith("_BLOCK"):
            continue
        suffix = product_id.removeprefix("ENCHANTED_")
        for output in (f"ENCHANTED_{suffix}_BLOCK", f"{product_id}_BLOCK"):
            if output in product_ids:
                generated.append(_generated_conversion(product_id, output, 160, 1))
                break
    special_pairs = {
        "INK_SACK:4": "ENCHANTED_LAPIS_LAZULI",
        "INK_SACK:3": "ENCHANTED_COCOA",
        "LOG": "ENCHANTED_OAK_LOG",
        "LOG:1": "ENCHANTED_SPRUCE_LOG",
        "LOG:2": "ENCHANTED_BIRCH_LOG",
        "LOG_2": "ENCHANTED_ACACIA_LOG",
        "LOG_2:1": "ENCHANTED_DARK_OAK_LOG",
        "RAW_FISH": "ENCHANTED_RAW_FISH",
        "RAW_FISH:1": "ENCHANTED_RAW_SALMON",
        "RAW_FISH:2": "ENCHANTED_CLOWNFISH",
        "RAW_FISH:3": "ENCHANTED_PUFFERFISH",
    }
    for input_id, output_id in special_pairs.items():
        if input_id in product_ids and output_id in product_ids:
            generated.append(_generated_conversion(input_id, output_id, 160, 1))
    return generated


def backup_dataset_files(*, files: list[Path] | None = None) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = Path("data/backups") / timestamp
    target.mkdir(parents=True, exist_ok=True)
    for path in files or list(DATASET_FILES.values()):
        if path.exists():
            shutil.copy2(path, target / path.name)
    return target


def print_validation_summary(result: DatasetValidationResult) -> None:
    print("Dataset validation")
    print(f"- Valid entries: {result.valid_entries}")
    print(f"- Disabled entries: {result.disabled_entries}")
    print(f"- Uncertain entries: {result.uncertain_entries}")
    print(f"- Warnings: {len(result.warnings)}")
    print(f"- Errors: {len(result.errors)}")
    if result.errors:
        print("\nErrors")
        for issue in result.errors[:25]:
            print(f"- {issue.dataset} {issue.item}: {issue.message}")
        if len(result.errors) > 25:
            print(f"- ... {len(result.errors) - 25} more")
    if result.warnings:
        print("\nWarnings")
        for issue in result.warnings[:25]:
            print(f"- {issue.dataset} {issue.item}: {issue.message}")
        if len(result.warnings) > 25:
            print(f"- ... {len(result.warnings) - 25} more")
    suggestions = result.suggestions
    if suggestions:
        print("\nSuggestions")
        for issue in suggestions[:10]:
            print(f"- {issue.dataset} {issue.item}: {issue.message}")
        if len(suggestions) > 10:
            print(f"- ... {len(suggestions) - 10} more")


def _normalize_accessory(row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    row.setdefault("verified", bool(row.get("recipe_verified", False)) or not row.get("uncertain_requirements", False))
    row.setdefault("confidence", "low" if row.get("uncertain_requirements") else "medium")
    row.setdefault("source_notes", "Existing local SkyFlip dataset; verify recipe/source before aggressive recommendations.")
    row.setdefault("last_verified", TODAY)
    row.setdefault("requires_manual_verification", bool(row.get("uncertain_requirements", False)) or row.get("confidence") == "low")
    row.setdefault("disabled", False)
    row.setdefault("disabled_reason", "")
    if row.get("recipe") in ({}, []):
        row["recipe"] = None
    if row.get("recipe") and isinstance(row["recipe"], list):
        row["recipe"] = {"ingredients": row["recipe"]}
    return row


def _normalize_watch_item(row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    row.setdefault("enabled", not bool(row.get("disabled", False)))
    row.setdefault("risk_tags", [])
    row.setdefault("min_requirements", {})
    row.setdefault("confidence", "medium")
    row.setdefault("verified", False)
    row.setdefault("source_notes", "Existing local SkyFlip AH watchlist; tag should be verified against SkyCofl before aggressive use.")
    row.setdefault("last_verified", TODAY)
    row.setdefault("requires_manual_verification", row.get("confidence") != "high")
    return row


def _normalize_conversion(row: dict[str, Any], *, product_ids: set[str] | None) -> dict[str, Any]:
    row = dict(row)
    row.setdefault("output_amount", 1)
    row.setdefault("conversion_type", "compression")
    row.setdefault("craftable_manually", True)
    row.setdefault("manual_craft_operations", 1)
    row.setdefault("manual_effort", "low" if float(row.get("input_amount", 0) or 0) <= 160 else "medium")
    row.setdefault("requires_collection", {})
    row.setdefault("verified", product_ids is not None)
    row.setdefault("confidence", "medium")
    row.setdefault("disabled", False)
    row.setdefault("disabled_reason", "")
    row.setdefault("source_notes", "Generated or maintained from live Hypixel Bazaar product IDs; verify in-game craft availability.")
    row.setdefault("last_verified", TODAY)
    row.setdefault("requires_manual_verification", row.get("confidence") != "high")
    input_id = str(row.get("input_product_id", ""))
    output_id = str(row.get("output_product_id", ""))
    if product_ids is not None and (input_id not in product_ids or output_id not in product_ids):
        row["disabled"] = True
        row["confidence"] = "low"
        row["disabled_reason"] = "Input or output product is not present in the live Hypixel Bazaar product list."
    if input_id and input_id == output_id:
        row["disabled"] = True
        row["confidence"] = "low"
        row["disabled_reason"] = "Input and output products are identical."
    return row


def _normalize_recipe(row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    row.setdefault("risk_tags", [])
    row.setdefault("verified", False)
    row.setdefault("confidence", "medium")
    row.setdefault("source_notes", "Existing local SkyFlip craft recipe; verify against official wiki before aggressive recommendations.")
    row.setdefault("last_verified", TODAY)
    row.setdefault("requires_manual_verification", row.get("confidence") != "high")
    row.setdefault("disabled", False)
    row.setdefault("disabled_reason", "")
    return row


def _generated_conversion(input_id: str, output_id: str, input_amount: int, output_amount: int) -> dict[str, Any]:
    return {
        "name": f"{_display_product(input_id)} -> {_display_product(output_id)}",
        "input_product_id": input_id,
        "input_amount": input_amount,
        "output_product_id": output_id,
        "output_amount": output_amount,
        "conversion_type": "compression",
        "craftable_manually": True,
        "manual_craft_operations": 1,
        "manual_effort": "low",
        "requires_collection": {},
        "notes": "Obvious 160x Bazaar compression candidate generated only because both products exist on live Bazaar.",
        "verified": True,
        "confidence": "medium",
        "disabled": False,
        "disabled_reason": "",
        "source_notes": "Live Hypixel Bazaar product IDs, craftability pattern still needs in-game/wiki verification.",
        "last_verified": TODAY,
        "requires_manual_verification": True,
    }


def _display_product(product_id: str) -> str:
    return product_id.replace(":", " ").replace("_", " ").title()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
