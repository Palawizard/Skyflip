from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .profile_parser import PlayerProfile
from .scoring import Opportunity
from .terminal_layout import format_table


def print_terminal_report(opportunities: list[Opportunity], rejected: list[Opportunity], warnings: list[str]) -> None:
    if warnings:
        print("Warnings:")
        for warning in warnings:
            print(f"- {warning}")
        print()

    rows = [opportunity for opportunity in opportunities if not opportunity.rejected]
    if not rows:
        print("No craft flips passed the configured filters.")
        if rejected:
            print(f"{len(rejected)} recipes were rejected; see JSON report for details.")
        return

    headers = ["#", "Item", "Cost", "Safe sell", "Profit/item", "Batch profit", "Profit %", "Sales/d", "Med sell", "BINs", "Risk", "Why", "Batch"]
    table_rows = []
    for rank, item in enumerate(rows, start=1):
        table_rows.append(
            [
                str(rank),
                item.recipe.name,
                coins(item.craft_cost.per_output_cost),
                coins(item.safe_sell_price),
                coins(item.estimated_profit),
                coins(item.estimated_profit * item.max_batch_size),
                f"{item.profit_percent:.1f}%",
                f"{item.market.analysis.sales_per_day:.1f}",
                hours(item.market.analysis.median_sell_time_hours),
                str(item.market.active.active_count),
                ",".join(item.risks[:2]) or "low",
                item.reasons[0] if item.reasons else "",
                str(item.max_batch_size),
            ]
        )
    print_table(headers, table_rows)
    print()
    for rank, item in enumerate(rows, start=1):
        print(f"{rank}. {item.recipe.name}")
        print(f"   Shopping: {shopping_list(item)}")
        print(f"   Craft chain: {craft_chain(item)}")
        print(f"   Suggested listing: {coins(item.suggested_listing_price)}; batch size: {item.max_batch_size}")
        print(f"   Unlocked: {'; '.join(item.eligibility.reasons)}")
        print(f"   Profit: {'; '.join(item.reasons)}")
        print(f"   Risk: {', '.join(item.risks) if item.risks else 'normal market risk'}")


def write_json_report(
    path: Path | str,
    *,
    profile: PlayerProfile,
    opportunities: list[Opportunity],
    rejected: list[Opportunity],
    warnings: list[str],
    raw_api_summary: dict[str, Any],
) -> None:
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "profile": {
            "player_name": profile.player_name,
            "member_id": profile.member_id,
            "purse": profile.purse,
            "bank": profile.bank,
            "profile_mode": profile.profile_mode,
            "skills": profile.skills,
            "slayer_levels": profile.slayer_levels,
            "collection_tiers": profile.collection_tiers,
        },
        "warnings": warnings,
        "api": raw_api_summary,
        "recommended": [opportunity_to_dict(item) for item in opportunities if not item.rejected],
        "rejected": [opportunity_to_dict(item) for item in rejected],
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")


def write_csv_report(path: Path | str, opportunities: list[Opportunity]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "rank",
                "item",
                "craft_cost",
                "safe_sell_price",
                "estimated_profit",
                "profit_percent",
                "sales_per_day",
                "median_sell_time_hours",
                "active_bin_count",
                "score",
                "max_batch_size",
                "risks",
            ],
        )
        writer.writeheader()
        rank = 1
        for item in opportunities:
            if item.rejected:
                continue
            writer.writerow(
                {
                    "rank": rank,
                    "item": item.recipe.name,
                    "craft_cost": round(item.craft_cost.per_output_cost, 2),
                    "safe_sell_price": round(item.safe_sell_price, 2),
                    "estimated_profit": round(item.estimated_profit, 2),
                    "profit_percent": round(item.profit_percent, 2),
                    "sales_per_day": round(item.market.analysis.sales_per_day, 2),
                    "median_sell_time_hours": item.market.analysis.median_sell_time_hours,
                    "active_bin_count": item.market.active.active_count,
                    "score": round(item.score, 2),
                    "max_batch_size": item.max_batch_size,
                    "risks": ",".join(item.risks),
                }
            )
            rank += 1


def write_txt_report(path: Path | str, opportunities: list[Opportunity]) -> None:
    rows = []
    rank = 1
    for item in opportunities:
        if item.rejected:
            continue
        rows.append(
            [
                str(rank),
                item.recipe.name,
                short_coins(item.estimated_profit),
                f"{item.market.analysis.sales_per_day:.1f}",
                hours(item.market.analysis.median_sell_time_hours),
                risk_label(item),
                str(item.max_batch_size),
            ]
        )
        rank += 1

    headers = ["#", "Item", "Profit", "Sales/day", "Sell time", "Risk", "Batch"]
    lines = format_plain_table(headers, rows)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def opportunity_to_dict(item: Opportunity) -> dict[str, Any]:
    data = asdict(item)
    data["shopping_list"] = shopping_list(item)
    data["craft_chain"] = craft_chain(item)
    return data


def shopping_list(item: Opportunity) -> str:
    parts: list[str] = []
    for ingredient in item.craft_cost.ingredients:
        if ingredient.children:
            parts.extend(_shopping_from_children(ingredient.children, ingredient.amount))
        else:
            parts.append(f"{ingredient.amount:g}x {ingredient.name} via {ingredient.source}")
    return "; ".join(parts)


def craft_chain(item: Opportunity) -> str:
    nested = [ingredient.name for ingredient in item.craft_cost.ingredients if ingredient.children]
    if nested:
        return f"craft {' + '.join(nested)} first, then craft {item.recipe.name}"
    return f"craft {item.recipe.name}"


def print_table(headers: list[str], rows: list[list[str]]) -> None:
    for line in format_table(headers, rows, essential_columns={0, 1}):
        print(line)


def coins(value: float | None) -> str:
    if value is None:
        return "?"
    return f"{value:,.0f}"


def hours(value: float | None) -> str:
    if value is None:
        return "?"
    return f"{value:.1f}h"


def short_coins(value: float | None) -> str:
    if value is None:
        return "?"
    sign = "-" if value < 0 else ""
    absolute = abs(value)
    if absolute >= 1_000_000:
        return f"{sign}{absolute / 1_000_000:.1f}m".replace(".0m", "m")
    if absolute >= 1_000:
        return f"{sign}{absolute / 1_000:.0f}k"
    return f"{sign}{absolute:.0f}"


def risk_label(item: Opportunity) -> str:
    risk_score = (
        item.market.volatility * 0.35
        + item.market.price_wall_score * 0.3
        + item.market.manipulation_risk_score * 0.25
        + (0.1 if item.market.confidence_score < 0.6 else 0)
    )
    if risk_score >= 0.45 or any(tag in item.risks for tag in {"volatile_price", "thin_market_data", "high_capital"}):
        return "High"
    if risk_score >= 0.2 or any(tag in item.risks for tag in {"price_wall", "saturated", "slow_sale"}):
        return "Med"
    return "Low"


def format_plain_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))
    lines = ["  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)).rstrip()]
    for row in rows:
        lines.append("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)).rstrip())
    return lines


def _shopping_from_children(children, multiplier: float) -> list[str]:
    parts: list[str] = []
    for child in children:
        if child.children:
            parts.extend(_shopping_from_children(child.children, multiplier * child.amount))
        else:
            parts.append(f"{child.amount * multiplier:g}x {child.name} via {child.source}")
    return parts


def _clip(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    return value[: max(0, width - 3)] + "..."
