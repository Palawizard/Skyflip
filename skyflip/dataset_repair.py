from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode

from .cache import FileCache
from .dataset_validation import DATASET_FILES, DatasetValidationResult, validate_all_datasets
from .http import HttpClient


OFFICIAL_WIKI_API_URL = "https://wiki.hypixel.net/api.php"
TODAY = "2026-06-20"
KNOWN_WIKI_TITLES = {
    "CAMPFIRE_TALISMAN_1": "Campfire Badge",
    "VILLAGE_TALISMAN": "Village Affinity Talisman",
    "WISE_DRAGON_BOOTS": "Wise Dragon Armor",
    "WISE_DRAGON_CHESTPLATE": "Wise Dragon Armor",
    "WISE_DRAGON_HELMET": "Wise Dragon Armor",
    "WISE_DRAGON_LEGGINGS": "Wise Dragon Armor",
}


@dataclass(frozen=True)
class WikiConfirmation:
    title: str
    url: str


@dataclass(frozen=True)
class WikiRecipeIngredient:
    tag: str
    amount: float


@dataclass(frozen=True)
class WikiRecipe:
    tag: str
    ingredients: tuple[WikiRecipeIngredient, ...]
    url: str


class WikiLookup(Protocol):
    def confirm_item(self, name: str, tag: str | None = None) -> WikiConfirmation | None:
        ...


class OfficialWikiClient:
    def __init__(self, http: HttpClient | None = None) -> None:
        self.http = http or HttpClient(FileCache(ttl_seconds=86_400), retries=1, user_agent="skyflip/0.1 dataset-repair")

    def confirm_item(self, name: str, tag: str | None = None) -> WikiConfirmation | None:
        query = KNOWN_WIKI_TITLES.get(str(tag or "").upper(), str(name or tag or "").strip())
        if not query:
            return None
        params = urlencode({
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": 5,
            "format": "json",
        })
        result = self.http.get_json(f"{OFFICIAL_WIKI_API_URL}?{params}", cache_key=f"hypixel-wiki-search:{query}")
        payload = result.payload if isinstance(result.payload, dict) else {}
        rows = payload.get("query", {}).get("search", [])
        if not isinstance(rows, list):
            return None
        wanted = _normalized_title(query)
        tag_title = _normalized_title(str(tag or ""))
        for row in rows:
            title = str(row.get("title") or "")
            normalized = _normalized_title(title)
            if normalized in {wanted, tag_title} or wanted in normalized or normalized in wanted:
                page = title.replace(" ", "_")
                return WikiConfirmation(title=title, url=f"https://wiki.hypixel.net/{page}")
        return None

    def recipe(self, tag: str) -> WikiRecipe | None:
        normalized_tag = str(tag or "").strip().upper()
        if not normalized_tag:
            return None
        params = urlencode({
            "action": "query",
            "titles": f"Template:Recipe/{normalized_tag}",
            "prop": "revisions",
            "rvprop": "content",
            "rvslots": "main",
            "format": "json",
        })
        result = self.http.get_json(f"{OFFICIAL_WIKI_API_URL}?{params}", cache_key=f"hypixel-wiki-recipe:{normalized_tag}")
        payload = result.payload if isinstance(result.payload, dict) else {}
        pages = payload.get("query", {}).get("pages", {})
        if not isinstance(pages, dict):
            return None
        for page in pages.values():
            if not isinstance(page, dict) or "missing" in page:
                continue
            text = _revision_text(page)
            parsed = parse_wiki_recipe_template(text, normalized_tag)
            if parsed:
                return parsed
        return None


@dataclass
class DatasetAuditReport:
    issue_counts: dict[str, int] = field(default_factory=dict)
    examples: dict[str, list[str]] = field(default_factory=dict)

    def add(self, issue: str, item: str) -> None:
        self.issue_counts[issue] = self.issue_counts.get(issue, 0) + 1
        bucket = self.examples.setdefault(issue, [])
        if len(bucket) < 8:
            bucket.append(item)


@dataclass
class DatasetRepairReport:
    changed_files: set[str] = field(default_factory=set)
    changes: dict[str, int] = field(default_factory=dict)
    validation: DatasetValidationResult | None = None

    def add(self, change: str, path: Path) -> None:
        self.changed_files.add(str(path))
        self.changes[change] = self.changes.get(change, 0) + 1


def audit_datasets(*, root: Path = Path("."), wiki: WikiLookup | None = None) -> DatasetAuditReport:
    report = DatasetAuditReport()
    accessories = _read_json(root / DATASET_FILES["accessories"]).get("accessories", [])
    watchlist = _read_json(root / DATASET_FILES["ah_watchlist"]).get("items", [])
    recipes = _read_json(root / DATASET_FILES["craft_recipes"]).get("recipes", [])

    for row in accessories:
        if not isinstance(row, dict) or row.get("disabled"):
            continue
        item_id = str(row.get("item_id") or "")
        if row.get("auto_generated") and row.get("confidence") == "low" and not row.get("recipe"):
            report.add("accessory_ownership_detection_only", item_id)
        if row.get("verified") is False:
            confirmation = wiki.confirm_item(str(row.get("display_name") or item_id), item_id) if wiki else None
            report.add("accessory_wiki_confirmed" if confirmation else "accessory_unverified", item_id)

    for row in watchlist:
        if not isinstance(row, dict) or row.get("enabled") is False or row.get("disabled"):
            continue
        tag = str(row.get("tag") or "")
        risk_tags = {str(value) for value in row.get("risk_tags", [])}
        if ";" in tag and (row.get("market_source") != "pet_ah" or row.get("cofl_auction_supported") is not False):
            report.add("watchlist_pet_variant_tag", tag)
        if "attribute_item" in risk_tags and (row.get("market_source") != "attribute_ah" or row.get("cofl_auction_supported") is not False):
            report.add("watchlist_attribute_item_tag", tag)
        if row.get("verified") is False:
            confirmation = wiki.confirm_item(str(row.get("name") or tag), tag) if wiki else None
            report.add("watchlist_wiki_confirmed" if confirmation else "watchlist_unverified", tag)

    for row in recipes:
        if not isinstance(row, dict) or row.get("disabled"):
            continue
        output = row.get("output") or {}
        tag = str(output.get("tag") or "")
        if output.get("auctionable") is False:
            report.add("craft_non_auctionable_output", tag)
        if row.get("verified") is False:
            confirmation = wiki.confirm_item(str(output.get("display_name") or tag), tag) if wiki else None
            report.add("craft_wiki_confirmed" if confirmation else "craft_unverified", tag)
        wiki_recipe = _wiki_recipe(wiki, tag)
        if wiki_recipe:
            if _recipe_signature(row.get("ingredients", [])) != _wiki_recipe_signature(wiki_recipe):
                report.add("craft_wiki_recipe_mismatch", tag)
        elif wiki and tag:
            report.add("craft_wiki_recipe_missing", tag)
    return report


def repair_datasets(
    *,
    root: Path = Path("."),
    wiki: WikiLookup | None = None,
    bazaar_product_ids: set[str] | None = None,
) -> DatasetRepairReport:
    report = DatasetRepairReport()
    _repair_accessories(root / DATASET_FILES["accessories"], wiki, report)
    _repair_watchlist(root / DATASET_FILES["ah_watchlist"], wiki, report)
    _repair_recipes(root / DATASET_FILES["craft_recipes"], wiki, report, bazaar_product_ids=bazaar_product_ids)
    report.validation = validate_all_datasets(root=root, bazaar_product_ids=bazaar_product_ids)
    return report


def _repair_accessories(path: Path, wiki: WikiLookup | None, report: DatasetRepairReport) -> None:
    raw = _read_json(path)
    changed = False
    for row in raw.get("accessories", []):
        if not isinstance(row, dict) or row.get("disabled"):
            continue
        item_id = str(row.get("item_id") or "")
        if row.get("auto_generated") and not row.get("recipe"):
            changed |= _set(row, "ownership_detection_only", True, "accessory_ownership_detection_only", path, report)
            changed |= _set(row, "recommendation_eligible", False, "accessory_recommendation_disabled", path, report)
            changed |= _set(row, "market_source", "ah" if row.get("auctionable") else "manual", "accessory_market_source_classified", path, report)
            changed |= _set(row, "cofl_auction_supported", bool(row.get("auctionable")), "accessory_market_capability_classified", path, report)
            changed |= _set(row, "cofl_price_supported", bool(row.get("auctionable")), "accessory_market_capability_classified", path, report)
            changed |= _set(row, "confidence", "medium", "accessory_confidence_classified", path, report)
            changed |= _set(row, "uncertain_requirements", False, "accessory_requirements_classified", path, report)
            changed |= _set(
                row,
                "source_notes",
                "Hypixel item resources confirm accessory metadata; kept for ownership detection until an obtain source is verified.",
                "accessory_source_notes_updated",
                path,
                report,
            )
        if row.get("verified") is False:
            confirmation = wiki.confirm_item(str(row.get("display_name") or item_id), item_id) if wiki else None
            if confirmation:
                changed |= _confirm_with_wiki(row, confirmation, "accessory", path, report)
    if changed:
        _write_json(path, raw)


def _repair_watchlist(path: Path, wiki: WikiLookup | None, report: DatasetRepairReport) -> None:
    raw = _read_json(path)
    changed = False
    for row in raw.get("items", []):
        if not isinstance(row, dict) or row.get("enabled") is False or row.get("disabled"):
            continue
        tag = str(row.get("tag") or "")
        risk_tags = {str(value) for value in row.get("risk_tags", [])}
        if ";" in tag:
            changed |= _set(row, "market_source", "pet_ah", "watchlist_pet_market_classified", path, report)
            changed |= _set(row, "cofl_auction_supported", False, "watchlist_cofl_capability_classified", path, report)
            changed |= _set(row, "cofl_price_supported", False, "watchlist_cofl_capability_classified", path, report)
            changed |= _append_note(row, "SkyCofl tag endpoints do not reliably support pet variant tags; skip automated Cofl checks.", "watchlist_notes_updated", path, report)
        elif "attribute_item" in risk_tags:
            changed |= _set(row, "market_source", "attribute_ah", "watchlist_attribute_market_classified", path, report)
            changed |= _set(row, "cofl_auction_supported", False, "watchlist_cofl_capability_classified", path, report)
            changed |= _set(row, "cofl_price_supported", False, "watchlist_cofl_capability_classified", path, report)
            changed |= _append_note(row, "Attribute listings need attribute-aware auction parsing; skip automated Cofl checks.", "watchlist_notes_updated", path, report)
        else:
            changed |= _setdefault(row, "market_source", "ah", "watchlist_market_source_defaulted", path, report)
            changed |= _setdefault(row, "cofl_auction_supported", True, "watchlist_cofl_capability_defaulted", path, report)
            changed |= _setdefault(row, "cofl_price_supported", True, "watchlist_cofl_capability_defaulted", path, report)
        if row.get("verified") is False:
            confirmation = wiki.confirm_item(str(row.get("name") or tag), tag) if wiki else None
            if confirmation:
                changed |= _confirm_with_wiki(row, confirmation, "watchlist", path, report)
    if changed:
        _write_json(path, raw)


def _repair_recipes(
    path: Path,
    wiki: WikiLookup | None,
    report: DatasetRepairReport,
    *,
    bazaar_product_ids: set[str] | None,
) -> None:
    raw = _read_json(path)
    changed = False
    recipe_tags = {
        str((row.get("output") or {}).get("tag") or "").upper()
        for row in raw.get("recipes", [])
        if isinstance(row, dict)
    }
    for row in raw.get("recipes", []):
        if not isinstance(row, dict) or row.get("disabled"):
            continue
        output = row.get("output") or {}
        tag = str(output.get("tag") or "")
        if output.get("auctionable") is False:
            changed |= _set(row, "disabled", True, "craft_non_auctionable_disabled", path, report)
            changed |= _set(
                row,
                "disabled_reason",
                "Output is not auctionable, so it is not eligible for AH craft flips.",
                "craft_non_auctionable_disabled",
                path,
                report,
            )
            changed |= _set(row, "confidence", "medium", "craft_non_auctionable_classified", path, report)
            changed |= _set(row, "verified", True, "craft_non_auctionable_classified", path, report)
            changed |= _set(row, "requires_manual_verification", False, "craft_non_auctionable_classified", path, report)
            continue
        if row.get("verified") is False:
            confirmation = wiki.confirm_item(str(output.get("display_name") or tag), tag) if wiki else None
            if confirmation:
                changed |= _confirm_with_wiki(row, confirmation, "craft", path, report)
        wiki_recipe = _wiki_recipe(wiki, tag)
        if wiki_recipe:
            ingredients = _ingredients_from_wiki_recipe(
                row,
                wiki_recipe,
                recipe_tags=recipe_tags,
                bazaar_product_ids=bazaar_product_ids,
            )
            if row.get("ingredients") != ingredients:
                changed |= _set(row, "ingredients", ingredients, "craft_wiki_recipe_repaired", path, report)
            changed |= _set(row, "verified", True, "craft_wiki_recipe_confirmed", path, report)
            changed |= _set(row, "confidence", "high", "craft_wiki_recipe_confirmed", path, report)
            changed |= _set(row, "last_verified", TODAY, "craft_wiki_recipe_confirmed", path, report)
            changed |= _set(row, "requires_manual_verification", False, "craft_wiki_recipe_confirmed", path, report)
            changed |= _set(row, "source_notes", f"Official Hypixel SkyBlock Wiki recipe template confirmed: {wiki_recipe.url}", "craft_wiki_recipe_confirmed", path, report)
    if changed:
        _write_json(path, raw)


def _confirm_with_wiki(row: dict[str, Any], confirmation: WikiConfirmation, prefix: str, path: Path, report: DatasetRepairReport) -> bool:
    changed = False
    changed |= _set(row, "verified", True, f"{prefix}_wiki_confirmed", path, report)
    changed |= _set(row, "confidence", "medium", f"{prefix}_wiki_confirmed", path, report)
    changed |= _set(row, "last_verified", TODAY, f"{prefix}_wiki_confirmed", path, report)
    changed |= _set(row, "requires_manual_verification", False, f"{prefix}_wiki_confirmed", path, report)
    changed |= _set(row, "source_notes", f"Official Hypixel SkyBlock Wiki page confirmed: {confirmation.url}", f"{prefix}_wiki_confirmed", path, report)
    return changed


def _normalized_title(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", " ", value).strip().lower()
    return re.sub(r"\s+", " ", text)


def _wiki_recipe(wiki: WikiLookup | None, tag: str) -> WikiRecipe | None:
    if wiki is None:
        return None
    recipe_method = getattr(wiki, "recipe", None)
    if recipe_method is None:
        return None
    return recipe_method(tag)


def parse_wiki_recipe_template(text: str, expected_tag: str) -> WikiRecipe | None:
    block = _first_craft_item_block(text)
    if not block:
        return None
    output_match = re.search(r"\|out\s*=\s*\{\{Item/([^|}]+)", block)
    if output_match is None:
        return None
    output_tag = _normalize_tag(output_match.group(1))
    expected = _normalize_tag(expected_tag)
    if output_tag != expected:
        return None

    totals: dict[str, float] = {}
    for match in re.finditer(r"\|in\d+\s*=\s*\{\{Item/([^|}]+)(.*?)\}\}\s*(?:,\s*([0-9]+(?:\.[0-9]+)?))?", block):
        tag = _normalize_tag(match.group(1))
        amount = _wiki_item_amount(match.group(2), match.group(3))
        totals[tag] = totals.get(tag, 0.0) + amount
    if not totals:
        return None
    ingredients = tuple(WikiRecipeIngredient(tag, amount) for tag, amount in sorted(totals.items()))
    return WikiRecipe(tag=output_tag, ingredients=ingredients, url=f"https://wiki.hypixel.net/Template:Recipe/{output_tag}")


def _first_craft_item_block(text: str) -> str | None:
    start = re.search(r"\|first\s*=\s*\{\{Craft Item", text)
    if start is None:
        return None
    block = text[start.start():]
    next_block = re.search(r"\n\|[A-Za-z0-9_ -]+\s*=\s*\{\{Craft Item", block[start.end() - start.start():])
    if next_block is not None:
        offset = start.end() - start.start() + next_block.start()
        block = block[:offset]
    return block


def _wiki_item_amount(args: str, trailing_amount: str | None) -> float:
    if trailing_amount:
        return float(trailing_amount)
    named = re.search(r"\|\s*(?:amount|count|qty|quantity)\s*=\s*([0-9]+(?:\.[0-9]+)?)", args)
    if named is not None:
        return float(named.group(1))
    for part in args.split("|"):
        token = part.strip()
        if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", token):
            return float(token)
    return 1.0


def _revision_text(page: dict[str, Any]) -> str:
    revisions = page.get("revisions")
    if not isinstance(revisions, list) or not revisions:
        return ""
    revision = revisions[0]
    if not isinstance(revision, dict):
        return ""
    slots = revision.get("slots")
    if isinstance(slots, dict):
        main = slots.get("main")
        if isinstance(main, dict):
            return str(main.get("*") or main.get("content") or "")
    return str(revision.get("*") or revision.get("content") or "")


def _recipe_signature(ingredients: Any) -> tuple[tuple[str, float], ...]:
    totals: dict[str, float] = {}
    if not isinstance(ingredients, list):
        return ()
    for ingredient in ingredients:
        if not isinstance(ingredient, dict):
            continue
        tag = _normalize_tag(ingredient.get("item_tag") or "")
        try:
            amount = float(ingredient.get("amount", 0) or 0)
        except (TypeError, ValueError):
            amount = 0.0
        if tag and amount > 0:
            totals[tag] = totals.get(tag, 0.0) + amount
    return tuple(sorted(totals.items()))


def _wiki_recipe_signature(recipe: WikiRecipe) -> tuple[tuple[str, float], ...]:
    return tuple(sorted((ingredient.tag, ingredient.amount) for ingredient in recipe.ingredients))


def _ingredients_from_wiki_recipe(
    row: dict[str, Any],
    recipe: WikiRecipe,
    *,
    recipe_tags: set[str],
    bazaar_product_ids: set[str] | None,
) -> list[dict[str, Any]]:
    existing = {
        _normalize_tag(ingredient.get("item_tag") or ""): ingredient
        for ingredient in row.get("ingredients", [])
        if isinstance(ingredient, dict) and ingredient.get("item_tag")
    }
    return [
        {
            "amount": _clean_amount(ingredient.amount),
            "display_name": str(existing.get(ingredient.tag, {}).get("display_name") or _display_name_from_tag(ingredient.tag)),
            "item_tag": ingredient.tag,
            "source": _ingredient_source(ingredient.tag, existing.get(ingredient.tag), recipe_tags, bazaar_product_ids),
        }
        for ingredient in recipe.ingredients
    ]


def _ingredient_source(
    tag: str,
    existing: dict[str, Any] | None,
    recipe_tags: set[str],
    bazaar_product_ids: set[str] | None,
) -> str:
    if tag in recipe_tags:
        return "previous_recipe"
    if bazaar_product_ids is not None:
        return "bazaar" if tag in bazaar_product_ids else "ah"
    if existing and existing.get("source"):
        return str(existing["source"])
    return "bazaar"


def _clean_amount(value: float) -> int | float:
    return int(value) if float(value).is_integer() else value


def _normalize_tag(value: Any) -> str:
    return str(value or "").strip().upper().replace(" ", "_")


def _display_name_from_tag(tag: str) -> str:
    return tag.replace("_", " ").replace(":", " ").title()


def _set(row: dict[str, Any], key: str, value: Any, change: str, path: Path, report: DatasetRepairReport) -> bool:
    if row.get(key) == value:
        return False
    row[key] = value
    report.add(change, path)
    return True


def _setdefault(row: dict[str, Any], key: str, value: Any, change: str, path: Path, report: DatasetRepairReport) -> bool:
    if key in row:
        return False
    row[key] = value
    report.add(change, path)
    return True


def _append_note(row: dict[str, Any], text: str, change: str, path: Path, report: DatasetRepairReport) -> bool:
    notes = str(row.get("notes") or "").strip()
    if text in notes:
        return False
    row["notes"] = f"{notes} {text}".strip()
    report.add(change, path)
    return True


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
