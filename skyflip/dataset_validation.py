from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .accessory_models import RARITY_ORDER


DATASET_FILES = {
    "accessories": Path("data/accessories.json"),
    "ah_watchlist": Path("data/ah_watchlist.json"),
    "bazaar_conversions": Path("data/bazaar_conversions.json"),
    "craft_recipes": Path("data/craft_recipes.json"),
}

CONFIDENCE_VALUES = {"high", "medium", "low"}
SOURCE_TYPES = {
    "ah",
    "bazaar",
    "chocolate_factory",
    "craft",
    "dungeon",
    "event",
    "fishing",
    "fixed",
    "fixed_cost",
    "foraging",
    "garden",
    "manual",
    "mining",
    "npc",
    "quest",
    "race",
    "rift",
    "slayer",
}
INGREDIENT_SOURCES = {"ah", "bazaar", "craft", "fixed", "fixed_cost", "manual", "npc", "previous_recipe", "previous_tier"}
CONVERSION_TYPES = {"compression", "decompression"}


@dataclass(frozen=True)
class ValidationIssue:
    dataset: str
    item: str
    severity: str
    message: str
    suggestion: str = ""


@dataclass
class DatasetValidationResult:
    valid_entries: int = 0
    disabled_entries: int = 0
    uncertain_entries: int = 0
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    @property
    def errors(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def suggestions(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "suggestion"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def extend(self, other: "DatasetValidationResult") -> None:
        self.valid_entries += other.valid_entries
        self.disabled_entries += other.disabled_entries
        self.uncertain_entries += other.uncertain_entries
        self.issues.extend(other.issues)


def validate_all_datasets(
    *,
    root: Path = Path("."),
    bazaar_product_ids: set[str] | None = None,
) -> DatasetValidationResult:
    result = DatasetValidationResult()
    result.extend(validate_accessories(root / DATASET_FILES["accessories"]))
    result.extend(validate_ah_watchlist(root / DATASET_FILES["ah_watchlist"]))
    result.extend(validate_bazaar_conversions(root / DATASET_FILES["bazaar_conversions"], bazaar_product_ids=bazaar_product_ids))
    result.extend(validate_craft_recipes(root / DATASET_FILES["craft_recipes"], bazaar_product_ids=bazaar_product_ids))
    return result


def validate_accessories(path: Path) -> DatasetValidationResult:
    result = DatasetValidationResult()
    raw = _read_json(path, "accessories", result)
    if raw is None:
        return result
    rows = raw.get("accessories")
    if not isinstance(rows, list):
        _error(result, "accessories", str(path), "missing accessories list")
        return result

    seen_ids: set[str] = set()
    family_tiers: dict[tuple[str, int], str] = {}
    ids: set[str] = set()
    for row in rows:
        if isinstance(row, dict) and row.get("item_id"):
            ids.add(str(row["item_id"]).upper())
    for index, row in enumerate(rows):
        item_id = _item_label(row, index)
        if not isinstance(row, dict):
            _error(result, "accessories", item_id, "entry must be an object")
            continue
        _count_status(result, row)
        if row.get("disabled"):
            continue
        required = [
            "item_id",
            "display_name",
            "rarity",
            "family_id",
            "tier_index",
            "is_accessory",
            "auctionable",
            "soulbound",
            "source_types",
            "requirements",
            "recipe",
        ]
        _require_fields(result, "accessories", item_id, row, required)
        if item_id in seen_ids:
            _error(result, "accessories", item_id, "duplicate item_id")
        seen_ids.add(item_id)
        if str(row.get("rarity", "")).lower() not in RARITY_ORDER:
            _error(result, "accessories", item_id, "invalid rarity")
        if not str(row.get("display_name", "")).strip():
            _error(result, "accessories", item_id, "missing display name")
        if str(row.get("confidence", "high")).lower() not in CONFIDENCE_VALUES:
            _warning(result, "accessories", item_id, "invalid or missing confidence", "Use high, medium, or low.")
        _validate_metadata(result, "accessories", item_id, row)
        family_id = str(row.get("family_id") or item_id)
        tier_index = _safe_int(row.get("tier_index"))
        if tier_index is None:
            _error(result, "accessories", item_id, "invalid tier_index")
        else:
            previous = family_tiers.get((family_id, tier_index))
            if previous:
                _error(result, "accessories", item_id, f"family tier conflict with {previous}")
            family_tiers[(family_id, tier_index)] = item_id
        for source in _as_list(row.get("source_types")):
            if str(source).lower() not in SOURCE_TYPES:
                _warning(result, "accessories", item_id, f"unknown source type {source!r}")
        _validate_requirement_object(result, "accessories", item_id, row.get("requirements"))
        _validate_accessory_recipe(result, item_id, row.get("recipe"), ids)
        for ref_field in ("upgrade_from", "upgrade_to"):
            ref = row.get(ref_field)
            if ref and str(ref).upper() not in ids:
                _warning(result, "accessories", item_id, f"{ref_field} references unknown accessory {ref}")

    for family_id in {family for family, _tier in family_tiers}:
        tiers = sorted(tier for family, tier in family_tiers if family == family_id)
        if tiers and tiers != list(range(min(tiers), max(tiers) + 1)):
            _warning(result, "accessories", family_id, "accessory family tier ordering has gaps")
    return result


def validate_ah_watchlist(path: Path) -> DatasetValidationResult:
    result = DatasetValidationResult()
    raw = _read_json(path, "ah_watchlist", result)
    if raw is None:
        return result
    rows = raw.get("items")
    if not isinstance(rows, list):
        _error(result, "ah_watchlist", str(path), "missing items list")
        return result
    seen: set[str] = set()
    for index, row in enumerate(rows):
        tag = _item_label(row, index, key="tag")
        if not isinstance(row, dict):
            _error(result, "ah_watchlist", tag, "entry must be an object")
            continue
        _count_status(result, row, enabled_field="enabled")
        if row.get("enabled") is False or row.get("disabled"):
            continue
        _require_fields(result, "ah_watchlist", tag, row, ["tag", "name", "category", "max_budget_percent"])
        if tag in seen:
            _error(result, "ah_watchlist", tag, "duplicate AH watch tag")
        seen.add(tag)
        if _safe_float(row.get("max_budget_percent")) is None:
            _error(result, "ah_watchlist", tag, "invalid max_budget_percent")
        if str(row.get("confidence", "high")).lower() not in CONFIDENCE_VALUES:
            _warning(result, "ah_watchlist", tag, "invalid or missing confidence", "Use high, medium, or low.")
        _validate_metadata(result, "ah_watchlist", tag, row)
        requirements = row.get("min_requirements")
        if requirements is not None:
            _validate_requirement_object(result, "ah_watchlist", tag, requirements)
        floor = row.get("min_catacombs_floor")
        if floor is not None and _safe_int(floor) is None:
            _error(result, "ah_watchlist", tag, "invalid min_catacombs_floor")
    return result


def validate_bazaar_conversions(path: Path, *, bazaar_product_ids: set[str] | None = None) -> DatasetValidationResult:
    result = DatasetValidationResult()
    raw = _read_json(path, "bazaar_conversions", result)
    if raw is None:
        return result
    rows = raw.get("conversions")
    if not isinstance(rows, list):
        _error(result, "bazaar_conversions", str(path), "missing conversions list")
        return result
    seen: set[tuple[str, str, str]] = set()
    for index, row in enumerate(rows):
        name = _item_label(row, index, key="name")
        if not isinstance(row, dict):
            _error(result, "bazaar_conversions", name, "entry must be an object")
            continue
        _count_status(result, row)
        if row.get("disabled"):
            continue
        _require_fields(
            result,
            "bazaar_conversions",
            name,
            row,
            ["input_product_id", "input_amount", "output_product_id", "output_amount", "conversion_type"],
        )
        input_id = str(row.get("input_product_id", ""))
        output_id = str(row.get("output_product_id", ""))
        key = (input_id, output_id, str(row.get("conversion_type", "compression")))
        if key in seen:
            _warning(result, "bazaar_conversions", name, "duplicate conversion")
        seen.add(key)
        if input_id and output_id and input_id == output_id:
            _error(result, "bazaar_conversions", name, "input and output products are identical")
        if _safe_float(row.get("input_amount")) is None or _safe_float(row.get("output_amount")) is None:
            _error(result, "bazaar_conversions", name, "invalid input/output amount")
        if str(row.get("conversion_type", "compression")).lower() not in CONVERSION_TYPES:
            _error(result, "bazaar_conversions", name, "invalid conversion_type")
        if bazaar_product_ids is not None:
            _validate_bazaar_id(result, "bazaar_conversions", name, input_id, bazaar_product_ids, "input_product_id")
            _validate_bazaar_id(result, "bazaar_conversions", name, output_id, bazaar_product_ids, "output_product_id")
        _validate_metadata(result, "bazaar_conversions", name, row)
    return result


def validate_craft_recipes(path: Path, *, bazaar_product_ids: set[str] | None = None) -> DatasetValidationResult:
    result = DatasetValidationResult()
    raw = _read_json(path, "craft_recipes", result)
    if raw is None:
        return result
    rows = raw.get("recipes")
    if not isinstance(rows, list):
        _error(result, "craft_recipes", str(path), "missing recipes list")
        return result
    recipe_tags: set[str] = set()
    for row in rows:
        if isinstance(row, dict):
            output = row.get("output") or {}
            if isinstance(output, dict) and output.get("tag"):
                recipe_tags.add(str(output["tag"]).upper())
    graph: dict[str, set[str]] = {tag: set() for tag in recipe_tags}
    for index, row in enumerate(rows):
        output = row.get("output") if isinstance(row, dict) else None
        tag = str((output or {}).get("tag") or f"entry-{index}").upper() if isinstance(output, dict) else f"entry-{index}"
        if not isinstance(row, dict):
            _error(result, "craft_recipes", tag, "entry must be an object")
            continue
        _count_status(result, row)
        if row.get("disabled"):
            continue
        if not isinstance(output, dict):
            _error(result, "craft_recipes", tag, "missing output object")
            continue
        _require_fields(result, "craft_recipes", tag, output, ["tag", "display_name", "quantity"])
        if not output.get("auctionable", True):
            _warning(result, "craft_recipes", tag, "recipe output is not auctionable and will be skipped")
        ingredients = row.get("ingredients")
        if not isinstance(ingredients, list) or not ingredients:
            _error(result, "craft_recipes", tag, "missing ingredients")
        else:
            for ingredient in ingredients:
                if not isinstance(ingredient, dict):
                    _error(result, "craft_recipes", tag, "ingredient must be an object")
                    continue
                source = str(ingredient.get("source", "bazaar")).lower()
                if source not in INGREDIENT_SOURCES:
                    _error(result, "craft_recipes", tag, f"invalid ingredient source {source!r}")
                item_tag = str(ingredient.get("item_tag") or "").upper()
                if not item_tag and source not in {"fixed", "fixed_cost", "npc"}:
                    _error(result, "craft_recipes", tag, "ingredient is missing item_tag")
                if _safe_float(ingredient.get("amount")) is None:
                    _error(result, "craft_recipes", tag, "ingredient has invalid amount")
                if source == "bazaar" and bazaar_product_ids is not None:
                    _validate_bazaar_id(result, "craft_recipes", tag, item_tag, bazaar_product_ids, "ingredient")
                if source in {"previous_recipe", "previous_tier"}:
                    if item_tag not in recipe_tags:
                        _warning(result, "craft_recipes", tag, f"previous_recipe reference {item_tag} has no matching recipe")
                    else:
                        graph.setdefault(tag, set()).add(item_tag)
        _validate_requirement_object(result, "craft_recipes", tag, row.get("requirements"))
        _validate_metadata(result, "craft_recipes", tag, row)
    for cycle in _find_cycles(graph):
        _error(result, "craft_recipes", " > ".join(cycle), "nested craft recipes create a cycle")
    return result


def compact_warning(result: DatasetValidationResult) -> str | None:
    skipped = result.disabled_entries + len(result.errors)
    if skipped <= 0 and result.uncertain_entries <= 0 and not result.warnings:
        return None
    parts = []
    if skipped:
        parts.append(f"{skipped} invalid/disabled dataset entr{'y' if skipped == 1 else 'ies'} skipped")
    if result.uncertain_entries:
        parts.append(f"{result.uncertain_entries} uncertain dataset entr{'y' if result.uncertain_entries == 1 else 'ies'}")
    if result.warnings:
        parts.append(f"{len(result.warnings)} dataset warning{'s' if len(result.warnings) != 1 else ''}")
    return "; ".join(parts) + "."


def _read_json(path: Path, dataset: str, result: DatasetValidationResult) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _error(result, dataset, str(path), "dataset file is missing")
    except json.JSONDecodeError as exc:
        _error(result, dataset, str(path), f"invalid JSON: {exc}")
    return None


def _count_status(result: DatasetValidationResult, row: dict[str, Any], *, enabled_field: str | None = None) -> None:
    disabled = bool(row.get("disabled")) or (enabled_field is not None and row.get(enabled_field) is False)
    if disabled:
        result.disabled_entries += 1
        return
    confidence = str(row.get("confidence", "high")).lower()
    if confidence == "low" or bool(row.get("requires_manual_verification")) or row.get("verified") is False:
        result.uncertain_entries += 1
    else:
        result.valid_entries += 1


def _item_label(row: Any, index: int, *, key: str = "item_id") -> str:
    if isinstance(row, dict):
        return str(row.get(key) or row.get("item_id") or row.get("tag") or f"entry-{index}").upper()
    return f"entry-{index}"


def _require_fields(result: DatasetValidationResult, dataset: str, item: str, row: dict[str, Any], fields: Iterable[str]) -> None:
    for field_name in fields:
        if field_name not in row:
            _error(result, dataset, item, f"missing required field {field_name}")


def _validate_metadata(result: DatasetValidationResult, dataset: str, item: str, row: dict[str, Any]) -> None:
    if "verified" not in row:
        _suggest(result, dataset, item, "missing verified field")
    if "confidence" not in row:
        _suggest(result, dataset, item, "missing confidence field")
    if not row.get("source_notes"):
        _suggest(result, dataset, item, "missing source_notes")
    if not row.get("last_verified"):
        _suggest(result, dataset, item, "missing last_verified")
    if row.get("disabled") and not row.get("disabled_reason"):
        _warning(result, dataset, item, "disabled entry should include disabled_reason")


def _validate_requirement_object(result: DatasetValidationResult, dataset: str, item: str, requirements: Any) -> None:
    if requirements is None:
        return
    if not isinstance(requirements, dict):
        _error(result, dataset, item, "requirements must be an object")
        return
    accepted = {
        "collections",
        "skills",
        "slayers",
        "catacombs_floor_completions",
        "skyblock_level",
        "quest_flags",
        "min_skill_levels",
        "min_slayer_levels",
        "min_collection_tiers",
        "min_catacombs_floor_completion",
        "min_skyblock_level",
        "event_only",
        "manual_source_only",
        "notes",
    }
    for key, value in requirements.items():
        if key not in accepted:
            _warning(result, dataset, item, f"unknown requirement key {key!r}")
        if isinstance(value, dict):
            for req_key, req_value in value.items():
                if _safe_int(req_value) is None:
                    _error(result, dataset, item, f"requirement {key}.{req_key} must be an integer")


def _validate_accessory_recipe(result: DatasetValidationResult, item_id: str, recipe: Any, ids: set[str]) -> None:
    if recipe is None:
        return
    if isinstance(recipe, dict):
        ingredients = recipe.get("ingredients")
    else:
        ingredients = recipe
    if ingredients in (None, []):
        return
    if not isinstance(ingredients, list):
        _error(result, "accessories", item_id, "recipe ingredients must be a list")
        return
    for ingredient in ingredients:
        if not isinstance(ingredient, dict):
            _error(result, "accessories", item_id, "recipe ingredient must be an object")
            continue
        source = str(ingredient.get("source", "bazaar")).lower()
        if source not in INGREDIENT_SOURCES:
            _warning(result, "accessories", item_id, f"unknown recipe ingredient source {source!r}")
        if source in {"previous_tier", "previous_recipe"}:
            ref = str(ingredient.get("item_id", "")).upper()
            if ref and ref not in ids:
                _warning(result, "accessories", item_id, f"recipe references unknown accessory {ref}")
        if _safe_float(ingredient.get("quantity", 1)) is None:
            _error(result, "accessories", item_id, "recipe ingredient has invalid quantity")


def _validate_bazaar_id(
    result: DatasetValidationResult,
    dataset: str,
    item: str,
    product_id: str,
    bazaar_product_ids: set[str],
    field_name: str,
) -> None:
    if product_id and product_id not in bazaar_product_ids:
        _error(result, dataset, item, f"{field_name} {product_id!r} is not a live Bazaar product")


def _find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    cycles: list[list[str]] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str, stack: list[str]) -> None:
        if node in visiting:
            cycles.append(stack[stack.index(node) :] + [node])
            return
        if node in visited:
            return
        visiting.add(node)
        for child in graph.get(node, set()):
            visit(child, stack + [child])
        visiting.remove(node)
        visited.add(node)

    for node in graph:
        visit(node, [node])
    return cycles


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _error(result: DatasetValidationResult, dataset: str, item: str, message: str, suggestion: str = "") -> None:
    result.issues.append(ValidationIssue(dataset, item, "error", message, suggestion))


def _warning(result: DatasetValidationResult, dataset: str, item: str, message: str, suggestion: str = "") -> None:
    result.issues.append(ValidationIssue(dataset, item, "warning", message, suggestion))


def _suggest(result: DatasetValidationResult, dataset: str, item: str, message: str, suggestion: str = "") -> None:
    result.issues.append(ValidationIssue(dataset, item, "suggestion", message, suggestion))
