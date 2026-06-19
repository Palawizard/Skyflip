from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import quote

from .bazaar import BazaarClient
from .cache import FileCache
from .cofl import CoflClient
from .dashboard import run_dashboard
from .dashboard_menu import run_dashboard_menu, should_open_dashboard_menu
from .http import ApiError, HttpClient
from .onboarding import ensure_profile_configuration, reset_profile_configuration_with_confirmation
from .pricing import PricingEngine
from .profile_parser import load_profile
from .recipes import check_eligibility, load_recipes, recipe_index
from .report import print_terminal_report, write_csv_report, write_json_report, write_txt_report
from .scoring import AnalyzerConfig, evaluate_opportunity


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    effective_argv = sys.argv[1:] if argv is None else argv
    if not effective_argv:
        effective_argv = ["dashboard"]
    args = parser.parse_args(effective_argv)
    if args.command == "analyze":
        return analyze(args)
    if args.command == "dashboard":
        if getattr(args, "talisman_helper", False):
            sections = {part.strip() for part in str(getattr(args, "sections", "")).split(",") if part.strip()}
            sections.add("talisman")
            args.sections = ",".join(sorted(sections))
        if getattr(args, "reset_profile_config", False):
            reset_profile_configuration_with_confirmation()
            return 0
        if getattr(args, "setup", False):
            cache = FileCache(ttl_seconds=getattr(args, "profile_cache_ttl", 600))
            ensure_profile_configuration(HttpClient(cache), force_setup=True)
        elif not getattr(args, "profile_file", None):
            cache = FileCache(ttl_seconds=getattr(args, "profile_cache_ttl", 600))
            ensure_profile_configuration(HttpClient(cache), force_setup=False)
        if should_open_dashboard_menu(args):
            return run_dashboard_menu(args, resolve_uuid=resolve_player_uuid)
        return run_dashboard(args, resolve_uuid=resolve_player_uuid)
    parser.print_help()
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="skyflip", description="Hypixel SkyBlock craft flip analyzer")
    subparsers = parser.add_subparsers(dest="command")
    analyze_parser = subparsers.add_parser("analyze", help="Analyze craft flipping opportunities")
    _add_common_args(analyze_parser, required=True)
    analyze_parser.add_argument("--max-craft-cost", type=float)
    analyze_parser.add_argument("--limit", type=int, default=30)
    analyze_parser.add_argument("--use-buy-order-cost", action="store_true")
    analyze_parser.add_argument("--export-json")
    analyze_parser.add_argument("--export-csv")
    analyze_parser.add_argument("--export-txt")
    analyze_parser.add_argument("--recipes-file", default="data/craft_recipes.json")
    analyze_parser.add_argument("--allow-restricted-profile", action="store_true")
    analyze_parser.add_argument("--show-rejected", action="store_true")

    dashboard_parser = subparsers.add_parser("dashboard", help="Print a terminal flipping dashboard")
    _add_common_args(dashboard_parser, required=False)
    dashboard_parser.add_argument("--menu", action="store_true", help="Open the interactive dashboard menu before running")
    dashboard_parser.add_argument("--dashboard", action="store_true", help="Accepted for compatibility; dashboard is already the default for this command")
    dashboard_parser.add_argument("--refresh-interval", type=int)
    dashboard_parser.add_argument("--once", action="store_true")
    dashboard_parser.add_argument("--sections", default="craft,bazaar-spread,bazaar-order,bazaar-compression,ah-underpriced,talisman")
    dashboard_parser.add_argument("--limit-per-section", type=int, default=10)
    dashboard_parser.add_argument("--spread-limit", type=int)
    dashboard_parser.add_argument("--min-spread-profit-per-unit", type=float, default=0.0)
    dashboard_parser.add_argument("--min-spread-volume-week", type=float, default=25_000.0)
    dashboard_parser.add_argument("--max-spread-depth-ratio", type=float, default=1.25)
    dashboard_parser.add_argument("--max-craft-cost", type=float)
    dashboard_parser.add_argument("--max-capital-percent-per-flip", type=float, default=35.0)
    dashboard_parser.add_argument("--use-buy-order-cost", action="store_true")
    dashboard_parser.add_argument("--recipes-file", default="data/craft_recipes.json")
    dashboard_parser.add_argument("--bazaar-conversions-file", default="data/bazaar_conversions.json")
    dashboard_parser.add_argument("--ah-watchlist-file", default="data/ah_watchlist.json")
    dashboard_parser.add_argument("--conversion-mode", choices=["conservative", "realistic"], default="realistic")
    dashboard_parser.add_argument("--export-json")
    dashboard_parser.add_argument("--export-csv")
    dashboard_parser.add_argument("--show-rejected", action="store_true")
    dashboard_parser.add_argument("--allow-restricted-profile", action="store_true")
    dashboard_parser.add_argument("--setup", action="store_true", help="Run Hypixel profile onboarding before loading the dashboard")
    dashboard_parser.add_argument("--reset-profile-config", action="store_true", help="Reset saved Hypixel user/profile/API-key configuration")
    dashboard_parser.add_argument("--profile-cache-ttl", type=int, default=600, help="Seconds before live Hypixel profile data is refreshed")
    dashboard_parser.add_argument("--max-estimated-buy-minutes", type=float)
    dashboard_parser.add_argument("--max-estimated-sell-minutes", type=float)
    dashboard_parser.add_argument("--max-estimated-bottleneck-minutes", type=float, default=240.0)
    dashboard_parser.add_argument("--min-speed-confidence", type=float, default=35.0)
    dashboard_parser.add_argument("--conservative-speed", action=argparse.BooleanOptionalAction, default=True)
    dashboard_parser.add_argument("--talisman-helper", action="store_true", help="Show the Talisman Helper section")
    dashboard_parser.add_argument("--accessories-file", default="data/accessories.json")
    dashboard_parser.add_argument("--max-accessory-price", type=float)
    dashboard_parser.add_argument("--max-accessory-recommendations", type=int, default=15)
    dashboard_parser.add_argument("--max-accessory-ah-checks", type=int, default=60)
    dashboard_parser.add_argument("--accessory-sort", default="score", choices=["score", "rarity", "price", "craft-cost", "coin-per-mp", "name", "craftable", "ah", "collection", "skill", "slayer"])
    dashboard_parser.add_argument("--accessory-rarity", default="", help="Comma-separated rarity filter")
    dashboard_parser.add_argument(
        "--accessory-view",
        default="recommended",
        choices=[
            "recommended",
            "craftable",
            "craftable-now",
            "buy-ah",
            "buy-from-ah",
            "available-on-ah",
            "upgrades",
            "locked",
            "all-missing",
            "owned",
            "owned-covered",
            "details",
        ],
    )
    dashboard_parser.add_argument("--accessory-search")
    dashboard_parser.add_argument("--accessory-ascending", action="store_true")
    dashboard_parser.add_argument("--show-owned", action="store_true")
    dashboard_parser.add_argument("--show-locked", action="store_true")
    dashboard_parser.add_argument("--only-craftable", action="store_true")
    dashboard_parser.add_argument("--only-ah", action="store_true")
    dashboard_parser.add_argument("--refresh-accessories", action="store_true", help="Accepted for compatibility; accessory database is reloaded each run")
    dashboard_parser.add_argument("--include-locked-accessories", action=argparse.BooleanOptionalAction, default=False)
    dashboard_parser.add_argument("--include-uncertain-accessories", action=argparse.BooleanOptionalAction, default=True)
    dashboard_parser.add_argument("--include-manual-unlocks", action=argparse.BooleanOptionalAction, default=True)
    dashboard_parser.add_argument("--include-ah-accessories", action=argparse.BooleanOptionalAction, default=True)
    dashboard_parser.add_argument("--include-craftable-accessories", action=argparse.BooleanOptionalAction, default=True)
    return parser


def _add_common_args(parser: argparse.ArgumentParser, *, required: bool) -> None:
    parser.add_argument("--profile-file", required=required)
    parser.add_argument("--player-name", required=required)
    parser.add_argument("--budget", required=required, type=float)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--min-profit", type=float, default=5_000)
    parser.add_argument("--min-profit-percent", type=float, default=4)
    parser.add_argument("--min-sales-per-day", type=float, default=2)
    parser.add_argument("--max-median-sell-time-hours", type=float, default=12)
    parser.add_argument("--cache-ttl", type=int, default=300)


def analyze(args: argparse.Namespace) -> int:
    cache = FileCache(ttl_seconds=args.cache_ttl)
    http = HttpClient(cache)
    player_uuid = resolve_player_uuid(http, args.player_name)
    profile = load_profile(args.profile_file, player_name=args.player_name, player_uuid=player_uuid)
    warnings = list(profile.warnings)
    if profile.is_restricted_mode and not args.allow_restricted_profile:
        warnings.append(
            f"Profile mode {profile.profile_mode!r} is restricted; normal AH craft flipping is not recommended."
        )
        print_terminal_report([], [], warnings)
        if args.export_json:
            write_json_report(
                Path(args.export_json),
                profile=profile,
                opportunities=[],
                rejected=[],
                warnings=warnings,
                raw_api_summary={"skipped": "restricted_profile", "cache_ttl": args.cache_ttl, "days": args.days},
            )
            print(f"Wrote JSON report: {args.export_json}")
        if args.export_csv:
            write_csv_report(Path(args.export_csv), [])
            print(f"Wrote CSV report: {args.export_csv}")
        if args.export_txt:
            write_txt_report(Path(args.export_txt), [])
            print(f"Wrote TXT report: {args.export_txt}")
        return 0

    bazaar = BazaarClient(http)
    cofl = CoflClient(http)
    recipes = load_recipes(args.recipes_file)
    recipes_by_tag = recipe_index(recipes)
    pricing = PricingEngine(
        recipes_by_tag,
        bazaar,
        cofl,
        use_buy_order_cost=args.use_buy_order_cost,
        days=args.days,
    )
    config = AnalyzerConfig(
        budget=args.budget,
        min_profit=args.min_profit,
        min_profit_percent=args.min_profit_percent,
        min_sales_per_day=args.min_sales_per_day,
        max_median_sell_time_hours=args.max_median_sell_time_hours,
        max_craft_cost=args.max_craft_cost,
        max_capital_percent_per_flip=getattr(args, "max_capital_percent_per_flip", 35.0),
        limit=args.limit,
    )

    all_results = []
    for recipe in recipes:
        eligibility = check_eligibility(recipe, profile)
        craft_cost = pricing.craft_cost(recipe)
        market = pricing.market_metrics(recipe.tag)
        all_results.append(evaluate_opportunity(recipe, eligibility, craft_cost, market, config))

    recommended = sorted(
        [item for item in all_results if not item.rejected],
        key=lambda item: (item.score, item.estimated_profit, item.market.analysis.sales_per_day),
        reverse=True,
    )[: args.limit]
    rejected = sorted(
        [item for item in all_results if item.rejected],
        key=lambda item: (item.score, item.estimated_profit),
        reverse=True,
    )
    warnings.extend(bazaar.warnings)
    warnings.extend(cofl.warnings)

    print_terminal_report(recommended, rejected if args.show_rejected else [], warnings)
    raw_summary = {
        "hypixel_bazaar_source": bazaar.last_source,
        "cache_ttl": args.cache_ttl,
        "days": args.days,
        "use_buy_order_cost": args.use_buy_order_cost,
    }
    if args.export_json:
        write_json_report(
            Path(args.export_json),
            profile=profile,
            opportunities=recommended,
            rejected=rejected,
            warnings=warnings,
            raw_api_summary=raw_summary,
        )
        print(f"Wrote JSON report: {args.export_json}")
    if args.export_csv:
        write_csv_report(Path(args.export_csv), recommended)
        print(f"Wrote CSV report: {args.export_csv}")
    if args.export_txt:
        write_txt_report(Path(args.export_txt), recommended)
        print(f"Wrote TXT report: {args.export_txt}")
    return 0


def resolve_player_uuid(http: HttpClient, player_name: str) -> str | None:
    url = f"https://api.mojang.com/users/profiles/minecraft/{quote(player_name)}"
    try:
        result = http.get_json(url)
    except ApiError:
        return None
    payload = result.payload if isinstance(result.payload, dict) else {}
    value = payload.get("id")
    return str(value) if value else None


if __name__ == "__main__":
    raise SystemExit(main())
