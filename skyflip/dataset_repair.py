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


@dataclass(frozen=True)
class WikiConfirmation:
    title: str
    url: str


class WikiLookup(Protocol):
    def confirm_item(self, name: str, tag: str | None = None) -> WikiConfirmation | None:
        ...


class OfficialWikiClient:
    def __init__(self, http: HttpClient | None = None) -> None:
        self.http = http or HttpClient(FileCache(ttl_seconds=86_400), retries=1, user_agent="skyflip/0.1 dataset-repair")

    def confirm_item(self, name: str, tag: str | None = None) -> WikiConfirmation | None:
        query = str(name or tag or "").strip()
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
        if ";" in tag:
            report.add("watchlist_pet_variant_tag", tag)
        if "attribute_item" in risk_tags:
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
    return report


def repair_datasets(*, root: Path = Path("."), wiki: WikiLookup | None = None) -> DatasetRepairReport:
    report = DatasetRepairReport()
    _repair_accessories(root / DATASET_FILES["accessories"], wiki, report)
    _repair_watchlist(root / DATASET_FILES["ah_watchlist"], wiki, report)
    _repair_recipes(root / DATASET_FILES["craft_recipes"], wiki, report)
    report.validation = validate_all_datasets(root=root, bazaar_product_ids=None)
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


def _repair_recipes(path: Path, wiki: WikiLookup | None, report: DatasetRepairReport) -> None:
    raw = _read_json(path)
    changed = False
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
