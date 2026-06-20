from __future__ import annotations

from datetime import datetime
from typing import Any

from .accessories import AccessoryAnalysis, AccessoryRecommendation
from .ah_underpriced import AhUnderpricedOpportunity
from .bazaar_compression import ConversionFlip
from .bazaar_order import BazaarOrderFlip
from .bazaar_spread import BazaarSpreadOpportunity
from .models import RejectedItem
from .profile_parser import PlayerProfile
from .report import hours, print_table
from .scoring import Opportunity
from .terminal_layout import compact_line, get_terminal_size, too_small_message, usable_width


def compact_number(value: float | int | None) -> str:
    if value is None:
        return "?"
    sign = "-" if value < 0 else ""
    absolute = abs(float(value))
    if absolute >= 1_000_000_000:
        return f"{sign}{absolute / 1_000_000_000:.1f}B"
    if absolute >= 1_000_000:
        return f"{sign}{absolute / 1_000_000:.1f}M"
    if absolute >= 1_000:
        return f"{sign}{absolute / 1_000:.1f}k"
    return f"{sign}{absolute:.0f}"


def print_dashboard(
    *,
    profile: PlayerProfile,
    budget: float,
    craft: list[Opportunity],
    bazaar_spreads: list[BazaarSpreadOpportunity],
    bazaar_orders: list[BazaarOrderFlip],
    conversions: list[ConversionFlip],
    ah_underpriced: list[AhUnderpricedOpportunity],
    rejected: list[RejectedItem],
    warnings: list[str],
    show_rejected: bool,
    cache_ttl: int,
    talisman_helper: AccessoryAnalysis | None = None,
) -> None:
    _print_resize_notice_if_needed()
    print(f"skyflip dashboard - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Cache TTL: {cache_ttl}s")
    print()
    _print_player_summary(profile, budget)
    _print_craft(craft)
    _print_bazaar_spreads(bazaar_spreads)
    _print_bazaar_orders(bazaar_orders)
    _print_conversions(conversions)
    _print_ah_underpriced(ah_underpriced)
    if talisman_helper is not None:
        _print_talisman_helper(talisman_helper)
    if show_rejected and rejected:
        _print_rejected(rejected)
    if warnings:
        print()
        print("API warnings")
        for warning in warnings[:12]:
            print(compact_line(f"- {warning}"))
        if len(warnings) > 12:
            print(f"- ... {len(warnings) - 12} more warnings hidden")
    print()
    print(compact_line("Manual-only tool: no in-game buying, selling, claiming, clicking, or listing is automated."))


def print_dashboard_status(data, *, last_refresh: str | None = None, auto_refresh: bool = False) -> None:
    _print_resize_notice_if_needed()
    print(f"skyflip dashboard - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if last_refresh:
        print(f"Last refresh: {last_refresh}")
    print(f"Auto refresh: {'ON' if auto_refresh else 'OFF'}")
    print(f"Cache TTL: {data.cache_ttl}s")
    print()
    _print_player_summary(data.profile, data.budget)
    print(
        compact_line(
            "Results: "
            f"craft {len(data.craft)}, "
            f"spread {len(data.bazaar_spreads)}, "
            f"order {len(data.bazaar_orders)}, "
            f"compression {len(data.conversions)}, "
            f"AH {len(data.ah_underpriced)}, "
            f"talisman {len(data.talisman_helper.recommendations) if getattr(data, 'talisman_helper', None) else 0}, "
            f"warnings {len(data.warnings)}"
        )
    )
    print()


def print_dashboard_section(data, section: str, *, show_rejected: bool = False) -> None:
    if section == "summary":
        _print_player_summary(data.profile, data.budget)
    elif section == "craft":
        _print_craft(data.craft)
    elif section == "bazaar-spread":
        _print_bazaar_spreads(data.bazaar_spreads)
    elif section == "bazaar-order":
        _print_bazaar_orders(data.bazaar_orders)
    elif section == "bazaar-compression":
        _print_conversions(data.conversions)
    elif section == "ah-underpriced":
        _print_ah_underpriced(data.ah_underpriced)
    elif section == "talisman":
        if data.talisman_helper is None:
            print("Talisman Helper was not loaded. Enable the talisman section and refresh.")
        else:
            _print_talisman_helper(data.talisman_helper)
    elif section == "rejected":
        if show_rejected and data.rejected:
            _print_rejected(data.rejected)
        else:
            print("Rejected rows are hidden. Enable Show rejected in Settings.")
    elif section == "warnings":
        _print_warnings(data.warnings)
    else:
        print(f"Unknown section: {section}")


def _print_warnings(warnings: list[str]) -> None:
    if not warnings:
        print("No API warnings.")
        return
    print("API warnings")
    for warning in warnings[:25]:
        print(compact_line(f"- {warning}"))
    if len(warnings) > 25:
        print(f"- ... {len(warnings) - 25} more warnings hidden")


def _print_player_summary(profile: PlayerProfile, budget: float) -> None:
    stage = _stage(profile)
    unlocks = _unlocks(profile)
    risk = "small/manual batches"
    if budget >= 100_000_000:
        risk = "can tolerate medium batches, still cap capital"
    elif budget >= 20_000_000:
        risk = "early-mid budget; prefer many small fast flips"
    rows = [
        ["Player", profile.player_name],
        ["Profile", _profile_label(profile)],
        ["Profile source", _profile_source(profile)],
        ["Budget", compact_number(budget)],
        ["Purse", compact_number(profile.purse)],
        ["Bank", compact_number(profile.bank)],
        ["Stage", stage],
        ["Relevant unlocks", unlocks],
        ["Risk profile", risk],
    ]
    _print_section_title("A. Player summary")
    print_table(["Field", "Value"], rows)
    print()


def _profile_label(profile: PlayerProfile) -> str:
    return profile.profile_name or "unknown"


def _profile_source(profile: PlayerProfile) -> str:
    source = profile.profile_source or "unknown"
    if profile.profile_fetched_at:
        fetched = datetime.fromtimestamp(profile.profile_fetched_at).strftime("%Y-%m-%d %H:%M:%S")
        return f"{source}, fetched {fetched}"
    return source


def _print_craft(items: list[Opportunity]) -> None:
    _print_section_title("B. Best craft flips")
    _print_result_hint("Craft/list the shown batch size, then re-check AH before listing.")
    rows = []
    for rank, item in enumerate(items, 1):
        rows.append(
            [
                str(rank),
                _with_qty(item.recipe.name, item.max_batch_size),
                compact_number(item.estimated_profit),
                compact_number(item.estimated_profit * item.max_batch_size),
                f"{item.profit_percent:.1f}%",
                item.speed_label,
                f"{item.confidence:.0f}%",
            ]
        )
    _print_or_empty(
        ["#", "Item xQty", "Profit/item", "Batch profit", "Margin", "Speed", "Conf"],
        rows,
        "No craft flips passed the configured filters.",
    )
    print()


def _print_bazaar_orders(items: list[BazaarOrderFlip]) -> None:
    _print_section_title("D. Best Bazaar order flips")
    _print_result_hint("Amount is next to the product. Place buy order at Buy @, then sell at Sell @ after filled.")
    rows = []
    for rank, item in enumerate(items, 1):
        risk = _display_risk(item.risk)
        rows.append(
            [
                str(rank),
                _with_qty(item.product_id, item.suggested_order_size),
                compact_number(item.buy_order_price),
                compact_number(item.sell_order_price),
                compact_number(item.estimated_profit),
                f"{item.profit_percent:.2f}%",
                item.bottleneck_speed_label,
                risk,
            ]
        )
    _print_or_empty(
        ["#", "Product xQty", "Buy @", "Sell @", "Profit", "Margin", "Bottleneck", "Risk"],
        rows,
        "No Bazaar order flips passed the configured filters.",
    )
    print()


def _print_bazaar_spreads(items: list[BazaarSpreadOpportunity]) -> None:
    _print_section_title("C. Best Bazaar Spread Flips")
    _print_result_hint("Amount is next to the product. Coins/h and Profit/min are estimated from the bottleneck buy/sell speed.")
    rows = []
    for rank, item in enumerate(items, 1):
        risk = _display_risk(item.risk, test_first=item.should_test_first)
        rows.append(
            [
                str(rank),
                _with_qty(item.product_id, item.suggested_order_size),
                compact_number(item.realistic_buy_price),
                compact_number(item.realistic_sell_price),
                compact_number(item.estimated_total_profit),
                f"{item.profit_percent:.1f}%",
                compact_number(item.coins_per_hour),
                compact_number(item.profit_per_minute),
                compact_number(item.capital_required),
                risk,
            ]
        )
    _print_or_empty(
        ["#", "Product xQty", "Buy @", "Sell @", "Profit", "Profit %", "Coins/h", "Profit/min", "Capital", "Risk"],
        rows,
        "No Bazaar spread flips passed the configured filters.",
    )
    print()


def _print_conversions(items: list[ConversionFlip]) -> None:
    _print_section_title("E. Best Bazaar compression/decompression flips")
    _print_result_hint("Manual conversion only. Buy inputs, convert manually, then sell outputs.")
    rows = []
    for rank, item in enumerate(items, 1):
        risk = _display_risk(item.risk)
        rows.append(
            [
                str(rank),
                item.name,
                compact_number(item.profit),
                f"{item.profit_percent:.1f}%",
                item.bottleneck_speed_label,
                str(item.suggested_batch_size),
                risk,
            ]
        )
    _print_or_empty(
        ["#", "Conversion", "Profit", "Margin", "Bottleneck", "Batch", "Risk"],
        rows,
        "No Bazaar conversions passed the configured filters.",
    )
    print()


def _print_ah_underpriced(items: list[AhUnderpricedOpportunity]) -> None:
    _print_section_title("F. Manual AH BIN underpriced finder")
    _print_result_hint("Check attributes/upgrades manually before buying. This section is intentionally conservative.")
    rows = []
    for rank, item in enumerate(items, 1):
        risk = _display_risk(item.risk)
        rows.append(
            [
                str(rank),
                item.item,
                compact_number(item.lowest_bin),
                compact_number(item.expected_profit),
                f"{item.underpriced_percent:.1f}%",
                hours(item.median_sell_time_hours),
                risk,
            ]
        )
    _print_or_empty(
        ["#", "Item", "Lowest", "Profit", "Discount", "Median", "Risk"],
        rows,
        "No underpriced AH BIN candidates passed the configured filters.",
    )
    print()


def _print_talisman_helper(analysis: AccessoryAnalysis, *, view: str | None = None) -> None:
    _print_section_title("G. Talisman Helper")
    view = (view or analysis.view or "recommended").lower().replace("_", "-")
    summary = analysis.summary
    print(
        compact_line(
            f"MP: {compact_number(summary.magical_power)} | "
            f"Owned: {compact_number(summary.owned_count)} | "
            f"Missing useful: {compact_number(summary.missing_count)} | "
            f"Craftable: {compact_number(summary.craftable_count)} | "
            f"AH affordable: {compact_number(summary.ah_count)}"
        )
    )
    if summary.warnings:
        print(_warning_line(summary.warnings[0]))
    print()
    if view in {"craftable-now", "craftable"}:
        _print_accessory_craftable(analysis.craftable)
    elif view in {"available-on-ah", "buy-from-ah", "buy-ah", "ah"}:
        _print_accessory_ah(analysis.ah_available)
    elif view == "upgrades":
        _print_accessory_upgrades(analysis.upgrades)
    elif view == "locked":
        _print_accessory_locked(analysis.locked)
    elif view in {"all-missing", "missing"}:
        _print_accessory_recommended(analysis.all_missing)
    elif view in {"owned", "owned-covered", "owned--covered"}:
        _print_accessory_owned(analysis.owned)
    elif view == "details":
        _print_accessory_details(analysis.rows)
    else:
        _print_accessory_recommended(analysis.recommendations)


def _print_accessory_recommended(items: list[AccessoryRecommendation]) -> None:
    print("Recommended")
    rows = []
    for rank, item in enumerate(items, 1):
        rows.append(
            [
                str(rank),
                _truncate(item.entry.display_name, 30),
                item.entry.rarity,
                _accessory_action(item),
                _accessory_cost(item),
                _accessory_why(item),
            ]
        )
    _print_or_empty(
        ["#", "Accessory", "Rarity", "Action", "Cost", "Why"],
        rows,
        "No missing accessories matched the current filters.",
    )


def _print_accessory_craftable(items: list[AccessoryRecommendation]) -> None:
    print("Craftable now")
    rows = [
        [
            _truncate(item.entry.display_name, 34),
            item.entry.rarity,
            _accessory_cost(item),
            _accessory_why(item),
        ]
        for item in items
    ]
    _print_or_empty(["Accessory", "Rarity", "Cost", "Why"], rows, "No craftable accessories found.")


def _print_accessory_ah(items: list[AccessoryRecommendation]) -> None:
    print("Buy from AH")
    rows = [
        [
            _truncate(item.entry.display_name, 34),
            item.entry.rarity,
            compact_number(item.ah.active.lowest_bin),
            _accessory_why(item),
        ]
        for item in items
    ]
    _print_or_empty(["Accessory", "Rarity", "Lowest BIN", "Why"], rows, "No AH accessories found.")


def _print_accessory_upgrades(items: list[AccessoryRecommendation]) -> None:
    print("Upgrades")
    rows = []
    for item in items:
        current = item.owned_family_best or item.entry.upgrade_from or "owned tier"
        rows.append(
            [
                _truncate(current.replace("_", " ").title(), 26),
                _truncate(item.entry.display_name, 30),
                _accessory_cost(item),
                _accessory_action(item),
                _accessory_why(item),
            ]
        )
    _print_or_empty(["Current", "Upgrade", "Cost", "Action", "Why"], rows, "No upgrade accessories found.")


def _print_accessory_locked(items: list[AccessoryRecommendation]) -> None:
    print("Locked")
    rows = [
        [
            _truncate(item.entry.display_name, 34),
            _truncate(item.missing_requirements[0] if item.missing_requirements else item.status, 42),
            ",".join(item.entry.source_types[:3]),
        ]
        for item in items
    ]
    _print_or_empty(["Accessory", "Missing requirement", "Source"], rows, "No locked accessories found.")


def _print_accessory_owned(items: list[AccessoryRecommendation]) -> None:
    print("Owned / Covered")
    best_by_family = {item.entry.family_id: item for item in items if item.owned_exact}
    covered_by_family: dict[str, list[str]] = {}
    for item in items:
        if item.covered_by_higher_tier:
            covered_by_family.setdefault(item.entry.family_id, []).append(item.entry.display_name)
    rows = []
    for family_id, best in sorted(best_by_family.items()):
        covers = ", ".join(sorted(covered_by_family.get(family_id, []))) or "-"
        rows.append([_truncate(best.entry.display_name, 30), _truncate(covers, 36), _truncate(family_id, 24)])
    _print_or_empty(["Owned best", "Covers", "Family"], rows, "No owned accessories detected.")


def _print_accessory_details(items: list[AccessoryRecommendation]) -> None:
    print("Details")
    if not items:
        print("No accessory selected.")
        return
    item = items[0]
    rows = [
        ["Accessory", item.entry.display_name],
        ["Rarity", item.entry.rarity],
        ["Status", "covered by higher tier" if item.covered_by_higher_tier else item.status],
        ["Best action", _accessory_action(item)],
        ["Estimated cost", _accessory_cost(item)],
        ["Requirements", "; ".join(item.missing_requirements[:3]) or "met/none"],
        ["Recipe", "; ".join(item.shopping_list[:6]) or item.entry.notes or "not listed"],
        ["AH availability", compact_number(item.ah.active.lowest_bin) if item.ah.active.lowest_bin else "none/unknown"],
        ["Family", _family_status(item)],
    ]
    print_table(["Field", "Value"], rows)


def _accessory_action(item: AccessoryRecommendation) -> str:
    if item.covered_by_higher_tier or item.owned_exact:
        return "Skip"
    if item.owned_family_best and item.entry.tier_index > 0:
        return "Upgrade"
    if item.craftable_now:
        return "Craft"
    if item.available_on_ah:
        return "Buy AH"
    if item.status == "Soulbound / manual unlock" or "manual" in item.entry.source_types:
        return "Manual"
    if item.status in {"Locked", "Unknown requirements", "Unknown recipe"}:
        return "Locked"
    if item.ah.overpriced or item.ah.manipulated:
        return "Skip"
    return "Manual"


def _accessory_cost(item: AccessoryRecommendation) -> str:
    if item.status == "Soulbound / manual unlock" or "manual" in item.entry.source_types and item.estimated_cost is None:
        return "free/manual"
    return compact_number(item.estimated_cost) if item.estimated_cost is not None else "unknown"


def _accessory_why(item: AccessoryRecommendation) -> str:
    if item.covered_by_higher_tier:
        return "covered"
    if item.owned_exact:
        return "owned"
    if item.ah.overpriced or item.ah.manipulated:
        return "overpriced, skip"
    if item.owned_family_best and item.entry.tier_index > 0:
        return "upgrade owned tier"
    if item.craftable_now:
        return "craftable now"
    if item.available_on_ah:
        return "AH available"
    if item.status == "Soulbound / manual unlock" or "manual" in item.entry.source_types:
        return "manual reward"
    if item.missing_requirements:
        return "unlock collection first"
    if item.coin_per_mp is not None and item.coin_per_mp <= 100_000:
        return "cheap MP"
    return _truncate(item.reasons[0] if item.reasons else item.status.lower(), 24)


def _family_status(item: AccessoryRecommendation) -> str:
    if item.owned_exact:
        return "owned"
    if item.covered_by_higher_tier:
        return "covered by higher tier"
    if item.owned_family_best:
        return "upgrade available"
    return "missing"


def _truncate(value: str, width: int) -> str:
    text = str(value)
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return text[: width - 3] + "..."


def _print_rejected(items: list[RejectedItem]) -> None:
    print("Rejected")
    rows = [[item.section, item.item, item.reason] for item in items[:100]]
    print_table(["Section", "Item/Product", "Reason"], rows)
    if len(items) > 100:
        print(f"... {len(items) - 100} more rejected rows hidden")


def _print_or_empty(headers: list[str], rows: list[list[str]], empty: str) -> None:
    if rows:
        print_table(headers, rows)
    else:
        print(empty)


def _print_result_hint(text: str) -> None:
    print(compact_line(f"   {text}"))
    print()


def _print_section_title(title: str) -> None:
    width = usable_width()
    print(compact_line(title, width=width))
    print("-" * min(width, max(24, len(title))))


def _print_resize_notice_if_needed() -> None:
    size = get_terminal_size()
    if size.too_small:
        print(too_small_message(size))
        print()


def _with_qty(name: str, amount: int | float) -> str:
    return f"{name} x{compact_number(amount)}"


def _display_risk(value: str, *, test_first: bool = False) -> str:
    if test_first:
        return "Test first"
    text = str(value or "").lower()
    if "test" in text:
        return "Test first"
    if "high" in text or "too slow" in text:
        return "High"
    if "medium" in text or "med" in text or "slow" in text:
        return "Medium"
    return "Low"


def _warning_line(warning: str) -> str:
    prefixed = f"Warning: {warning}"
    width = usable_width()
    if len(prefixed) <= width:
        return prefixed
    if len(warning) <= width:
        return warning
    return compact_line(prefixed, width=width)


def _stage(profile: PlayerProfile) -> str:
    combat = profile.skills.get("combat", 0)
    cata = profile.catacombs_level or 0
    level = profile.skyblock_level or 0
    if level >= 120 or cata >= 30 or combat >= 40:
        return f"late game lean (SB {level}, combat {combat}, cata {cata})"
    if level >= 50 or cata >= 14 or combat >= 25:
        return f"midgame lean (SB {level}, combat {combat}, cata {cata})"
    return f"early game lean (SB {level}, combat {combat}, cata {cata})"


def _unlocks(profile: PlayerProfile) -> str:
    parts: list[str] = []
    if profile.catacombs_level is not None:
        parts.append(f"cata {profile.catacombs_level}")
    for boss in ("zombie", "wolf", "spider", "enderman"):
        level = profile.slayer_levels.get(boss)
        if level:
            parts.append(f"{boss} {level}")
    if profile.magical_power:
        parts.append(f"{profile.magical_power} MP")
    return ", ".join(parts[:6]) or "profile data has limited unlock detail"


def _short_reason(reasons: list[str], fallback: str) -> str:
    if reasons:
        return reasons[0]
    return fallback
