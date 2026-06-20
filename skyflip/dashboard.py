from __future__ import annotations

import csv
import json
import time
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Iterable

from .ah_underpriced import load_watchlist, find_underpriced_ah
from .accessories import AccessoryAnalysis, analyze_accessories, filters_from_args
from .bazaar import BazaarClient
from .bazaar_compression import find_conversion_flips, load_conversions
from .bazaar_order import find_bazaar_order_flips
from .bazaar_spread import find_bazaar_spread_flips
from .cache import FileCache
from .cofl import CoflClient
from .datasets import runtime_dataset_warning
from .http import HttpClient
from .onboarding import ensure_profile_configuration
from .profile_fetcher import load_api_profile
from .pricing import PricingEngine
from .profile_parser import load_profile
from .recipes import check_eligibility, load_recipes, recipe_index
from .scoring import AnalyzerConfig, Opportunity, evaluate_opportunity
from .terminal import print_dashboard
from .user_config import budget_from_profile, load_user_config
from .warning_summary import compact_warnings


DEFAULT_SECTIONS = ["craft", "bazaar-spread", "bazaar-order", "bazaar-compression", "ah-underpriced", "talisman"]


@dataclass(frozen=True)
class DashboardData:
    profile: object
    budget: float
    craft: list
    bazaar_spreads: list
    bazaar_orders: list
    conversions: list
    ah_underpriced: list
    talisman_helper: AccessoryAnalysis | None
    rejected: list
    warnings: list[str]
    cache_ttl: int


def run_dashboard(args, *, resolve_uuid) -> int:
    refresh = max(60, int(args.refresh_interval or 0)) if not args.once else 0
    while True:
        _run_once(args, resolve_uuid=resolve_uuid)
        if refresh <= 0:
            return 0
        time.sleep(refresh)


def _run_once(args, *, resolve_uuid) -> None:
    data = collect_dashboard_data(args, resolve_uuid=resolve_uuid)
    print_dashboard(
        profile=data.profile,
        budget=data.budget,
        craft=data.craft,
        bazaar_spreads=data.bazaar_spreads,
        bazaar_orders=data.bazaar_orders,
        conversions=data.conversions,
        ah_underpriced=data.ah_underpriced,
        talisman_helper=data.talisman_helper,
        rejected=data.rejected,
        warnings=data.warnings,
        show_rejected=args.show_rejected,
        cache_ttl=data.cache_ttl,
    )
    if args.export_json:
        write_dashboard_json(Path(args.export_json), data.profile, data.craft, data.bazaar_spreads, data.bazaar_orders, data.conversions, data.ah_underpriced, data.talisman_helper, data.rejected, data.warnings)
    if args.export_csv:
        write_dashboard_csv(Path(args.export_csv), data.craft, data.bazaar_spreads, data.bazaar_orders, data.conversions, data.ah_underpriced)


def collect_dashboard_data(args, *, resolve_uuid) -> DashboardData:
    cache = FileCache(ttl_seconds=args.cache_ttl)
    http = HttpClient(cache)
    profile = _load_dashboard_profile(args, http, resolve_uuid=resolve_uuid)
    if args.budget is None:
        config = None if getattr(args, "profile_file", None) else load_user_config()
        args.budget = budget_from_profile(profile, config)
    if not getattr(args, "player_name", None):
        args.player_name = profile.player_name
    warnings = list(profile.warnings)
    dataset_warning = runtime_dataset_warning(
        paths={
            "accessories": getattr(args, "accessories_file", "data/accessories.json"),
            "ah_watchlist": getattr(args, "ah_watchlist_file", "data/ah_watchlist.json"),
            "bazaar_conversions": getattr(args, "bazaar_conversions_file", "data/bazaar_conversions.json"),
            "craft_recipes": getattr(args, "recipes_file", "data/craft_recipes.json"),
        }
    )
    if dataset_warning:
        warnings.append(f"Dataset warning: {dataset_warning}")
    config = AnalyzerConfig(
        budget=args.budget,
        min_profit=args.min_profit,
        min_profit_percent=args.min_profit_percent,
        min_sales_per_day=args.min_sales_per_day,
        max_median_sell_time_hours=args.max_median_sell_time_hours,
        max_craft_cost=getattr(args, "max_craft_cost", None),
        max_capital_percent_per_flip=args.max_capital_percent_per_flip,
        limit=args.limit_per_section,
        min_spread_profit_per_unit=args.min_spread_profit_per_unit,
        min_spread_volume_week=args.min_spread_volume_week,
        max_spread_depth_ratio=args.max_spread_depth_ratio,
        spread_limit=args.spread_limit or args.limit_per_section,
        max_estimated_buy_minutes=getattr(args, "max_estimated_buy_minutes", None),
        max_estimated_sell_minutes=getattr(args, "max_estimated_sell_minutes", None),
        max_estimated_bottleneck_minutes=getattr(args, "max_estimated_bottleneck_minutes", 240.0),
        min_speed_confidence=getattr(args, "min_speed_confidence", 35.0),
        conservative_speed=getattr(args, "conservative_speed", True),
    )
    sections = _parse_sections(args.sections)
    bazaar = BazaarClient(http)
    cofl = CoflClient(http)
    craft: list[Opportunity] = []
    bazaar_spreads = []
    bazaar_orders = []
    conversions = []
    ah_underpriced = []
    talisman_helper = None
    rejected = []

    if profile.is_restricted_mode and not args.allow_restricted_profile:
        warnings.append(f"Profile mode {profile.profile_mode!r} is restricted; normal market flipping is not recommended.")
    else:
        if "craft" in sections:
            try:
                craft, craft_rejected = analyze_craft_section(args, bazaar, cofl, profile, config)
                rejected.extend(craft_rejected)
            except Exception as exc:  # noqa: BLE001 - dashboard should continue section-by-section
                warnings.append(f"Craft section failed: {exc}")
        if "bazaar-order" in sections:
            try:
                bazaar_orders, order_rejected = find_bazaar_order_flips(bazaar, config)
                rejected.extend(order_rejected)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"Bazaar order section failed: {exc}")
        if "bazaar-spread" in sections:
            try:
                bazaar_spreads, spread_rejected = find_bazaar_spread_flips(bazaar, config)
                rejected.extend(spread_rejected)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"Bazaar spread section failed: {exc}")
        if bazaar_spreads and bazaar_orders:
            bazaar_spreads, bazaar_orders, duplicate_rejected = hide_duplicate_bazaar_results(bazaar_spreads, bazaar_orders)
            rejected.extend(duplicate_rejected)
        if "bazaar-compression" in sections:
            try:
                conversion_data = load_conversions(args.bazaar_conversions_file)
                conversions, conversion_rejected = find_conversion_flips(bazaar, config, conversion_data, mode=args.conversion_mode)
                rejected.extend(conversion_rejected)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"Bazaar compression section failed: {exc}")
        if "ah-underpriced" in sections:
            try:
                watchlist = load_watchlist(args.ah_watchlist_file)
                ah_underpriced, ah_rejected = find_underpriced_ah(cofl, profile, config, watchlist, days=args.days)
                rejected.extend(ah_rejected)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"AH underpriced section failed: {exc}")

    if "talisman" in sections:
        try:
            talisman_helper = analyze_accessories(
                profile,
                bazaar,
                cofl,
                database_path=getattr(args, "accessories_file", "data/accessories.json"),
                filters=filters_from_args(args),
                days=getattr(args, "days", 7),
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Talisman Helper failed: {exc}")

    warnings.extend(bazaar.warnings)
    warnings.extend(cofl.warnings)
    warnings = compact_warnings(warnings)
    return DashboardData(
        profile=profile,
        budget=args.budget,
        craft=craft,
        bazaar_spreads=bazaar_spreads,
        bazaar_orders=bazaar_orders,
        conversions=conversions,
        ah_underpriced=ah_underpriced,
        talisman_helper=talisman_helper,
        rejected=rejected,
        warnings=warnings,
        cache_ttl=args.cache_ttl,
    )


def analyze_craft_section(args, bazaar: BazaarClient, cofl: CoflClient, profile, config: AnalyzerConfig) -> tuple[list[Opportunity], list]:
    from .models import RejectedItem

    recipes = load_recipes(args.recipes_file)
    pricing = PricingEngine(
        recipe_index(recipes),
        bazaar,
        cofl,
        use_buy_order_cost=args.use_buy_order_cost,
        days=args.days,
    )
    all_results: list[Opportunity] = []
    direct_rejected = []
    for recipe in recipes:
        if not recipe.auctionable:
            direct_rejected.append(RejectedItem("craft", recipe.name, "item is not auctionable or has no reliable AH market"))
            continue
        eligibility = check_eligibility(recipe, profile)
        static_rejection = _static_recipe_rejection(eligibility.missing)
        if static_rejection:
            direct_rejected.append(RejectedItem("craft", recipe.name, static_rejection))
            continue
        craft_cost = pricing.craft_cost(recipe)
        market = pricing.market_metrics(recipe.tag)
        all_results.append(evaluate_opportunity(recipe, eligibility, craft_cost, market, config))
    recommended = sorted(
        [item for item in all_results if not item.rejected],
        key=lambda item: (item.score, item.market.analysis.sales_per_day, item.estimated_profit),
        reverse=True,
    )[: config.limit]
    rejected = [
        _craft_rejected(item)
        for item in all_results
        if item.rejected
    ]
    return recommended, [*direct_rejected, *rejected]


def write_dashboard_json(path: Path, profile, craft, bazaar_spreads, bazaar_orders, conversions, ah_underpriced, talisman_helper, rejected, warnings) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "profile": {
            "player_name": profile.player_name,
            "purse": profile.purse,
            "bank": profile.bank,
            "profile_mode": profile.profile_mode,
        },
        "craft": [_serializable(item) for item in craft],
        "bazaar_spread": [_serializable(item) for item in bazaar_spreads],
        "bazaar_order": [_serializable(item) for item in bazaar_orders],
        "bazaar_compression": [_serializable(item) for item in conversions],
        "ah_underpriced": [_serializable(item) for item in ah_underpriced],
        "talisman_helper": _serializable(talisman_helper) if talisman_helper else None,
        "rejected": [_serializable(item) for item in rejected],
        "warnings": warnings,
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def write_dashboard_csv(path: Path, craft, bazaar_spreads, bazaar_orders, conversions, ah_underpriced) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for item in craft:
        rows.append({"section": "craft", "item": item.recipe.name, "profit": round(item.estimated_profit, 2), "score": round(item.score, 2), "risk": ",".join(item.risks)})
    for item in bazaar_spreads:
        rows.append({"section": "bazaar-spread", "item": item.product_id, "profit": round(item.estimated_total_profit, 2), "score": round(item.final_score, 2), "risk": item.risk})
    for item in bazaar_orders:
        rows.append({"section": "bazaar-order", "item": item.product_id, "profit": round(item.estimated_profit, 2), "score": round(item.score, 2), "risk": item.risk})
    for item in conversions:
        rows.append({"section": "bazaar-compression", "item": item.name, "profit": round(item.profit, 2), "score": round(item.score, 2), "risk": item.risk})
    for item in ah_underpriced:
        rows.append({"section": "ah-underpriced", "item": item.item, "profit": round(item.expected_profit, 2), "score": round(item.score, 2), "risk": item.risk})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["section", "item", "profit", "score", "risk"])
        writer.writeheader()
        writer.writerows(rows)


def _parse_sections(value: str) -> set[str]:
    if not value:
        return set(DEFAULT_SECTIONS)
    aliases = {"spread": "bazaar-spread"}
    return {aliases.get(part.strip(), part.strip()) for part in value.split(",") if part.strip()}


def _load_dashboard_profile(args, http: HttpClient, *, resolve_uuid) -> object:
    if getattr(args, "profile_file", None):
        player_uuid = resolve_uuid(http, args.player_name) if args.player_name else None
        profile = load_profile(args.profile_file, player_name=args.player_name, player_uuid=player_uuid)
        if not args.player_name:
            args.player_name = profile.player_name
        return profile
    ensure_profile_configuration(http, force_setup=bool(getattr(args, "setup", False)))
    loaded = load_api_profile(
        http,
        force_refresh=bool(getattr(args, "refresh_profile", False)),
        ttl_seconds=int(getattr(args, "profile_cache_ttl", 600) or 600),
    )
    return loaded.profile


def hide_duplicate_bazaar_results(bazaar_spreads, bazaar_orders):
    from .models import RejectedItem

    spreads_by_product = {item.product_id: item for item in bazaar_spreads}
    orders_by_product = {item.product_id: item for item in bazaar_orders}
    duplicate_products = set(spreads_by_product).intersection(orders_by_product)
    hidden: list[RejectedItem] = []
    keep_spreads = []
    keep_orders = []

    for item in bazaar_spreads:
        order = orders_by_product.get(item.product_id)
        if order is None or item.final_score >= order.score:
            keep_spreads.append(item)
        else:
            hidden.append(RejectedItem("bazaar-spread", item.product_id, "hidden duplicate; Bazaar order section scored higher"))

    for item in bazaar_orders:
        spread = spreads_by_product.get(item.product_id)
        if spread is None or item.score > spread.final_score:
            keep_orders.append(item)
        else:
            hidden.append(RejectedItem("bazaar-order", item.product_id, "hidden duplicate; Bazaar spread section scored higher"))

    if not duplicate_products:
        return bazaar_spreads, bazaar_orders, hidden
    return keep_spreads, keep_orders, hidden


def _craft_rejected(item: Opportunity):
    from .models import RejectedItem

    return RejectedItem("craft", item.recipe.name, " and ".join(item.rejection_reasons[:3]))


def _static_recipe_rejection(missing: list[str]) -> str | None:
    static_reasons = [
        reason
        for reason in missing
        if "event-limited craft" in reason or "manual/source-only item" in reason
    ]
    return " and ".join(static_reasons) if static_reasons else None


def _serializable(value):
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [_serializable(item) for item in value]
    if isinstance(value, dict):
        return {key: _serializable(item) for key, item in value.items()}
    return value
