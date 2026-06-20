from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .cofl import ActiveAuctions, CoflClient, MarketAnalysis
from .market_speed import SpeedResult, normalize_ah_speed
from .models import RejectedItem
from .pricing import AH_FEE_RATE
from .profile_parser import PlayerProfile
from .scoring import AnalyzerConfig, score_generic_opportunity


@dataclass(frozen=True)
class WatchItem:
    tag: str
    name: str
    category: str
    max_budget_percent: float = 35.0
    min_catacombs_floor: int | None = None
    min_requirements: dict[str, dict[str, int]] | None = None
    risk_tags: tuple[str, ...] = ()
    notes: str = ""
    enabled: bool = True
    confidence: str = "medium"
    verified: bool = False
    requires_manual_verification: bool = False


@dataclass(frozen=True)
class AhUnderpricedOpportunity:
    item: str
    tag: str
    lowest_bin: float
    second_lowest_bin: float | None
    third_lowest_bin: float | None
    median_sold_price: float
    average_sold_price: float | None
    sales_per_day: float
    median_sell_time_hours: float | None
    active_bin_count: int
    underpriced_percent: float
    safe_resale_price: float
    expected_profit: float
    confidence: float
    risk: str
    reason: str
    score: float
    manual_action: str


def load_watchlist(path: Path | str = "data/ah_watchlist.json") -> list[WatchItem]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    items: list[WatchItem] = []
    for item in raw.get("items", []):
        if item.get("enabled") is False or item.get("disabled"):
            continue
        items.append(
            WatchItem(
                tag=item["tag"],
                name=item.get("name", item["tag"]),
                category=item.get("category", "general"),
                max_budget_percent=float(item.get("max_budget_percent", 35)),
                min_catacombs_floor=item.get("min_catacombs_floor"),
                min_requirements=item.get("min_requirements") or {},
                risk_tags=tuple(item.get("risk_tags", [])),
                notes=item.get("notes", ""),
                enabled=bool(item.get("enabled", True)),
                confidence=str(item.get("confidence", "medium")).lower(),
                verified=bool(item.get("verified", False)),
                requires_manual_verification=bool(item.get("requires_manual_verification", False)),
            )
        )
    return items


def find_underpriced_ah(
    cofl: CoflClient,
    profile: PlayerProfile,
    config: AnalyzerConfig,
    watchlist: list[WatchItem],
    *,
    days: int = 7,
) -> tuple[list[AhUnderpricedOpportunity], list[RejectedItem]]:
    accepted: list[AhUnderpricedOpportunity] = []
    rejected: list[RejectedItem] = []
    for item in watchlist:
        result = evaluate_watch_item(item, cofl, profile, config, days=days)
        if isinstance(result, AhUnderpricedOpportunity):
            accepted.append(result)
        else:
            rejected.append(result)
    accepted.sort(key=lambda row: (row.score, row.sales_per_day, row.expected_profit), reverse=True)
    return accepted[: config.limit], rejected


def evaluate_watch_item(
    item: WatchItem,
    cofl: CoflClient,
    profile: PlayerProfile,
    config: AnalyzerConfig,
    *,
    days: int = 7,
) -> AhUnderpricedOpportunity | RejectedItem:
    rejection: list[str] = []
    if item.min_catacombs_floor is not None and profile.catacombs_floor_completions.get(item.min_catacombs_floor, 0) <= 0:
        return RejectedItem("ah-underpriced", item.name, f"locked: catacombs floor {item.min_catacombs_floor} completion required")
    requirement_rejection = _watch_requirement_rejection(item, profile)
    if requirement_rejection:
        return RejectedItem("ah-underpriced", item.name, requirement_rejection)

    active = cofl.active_bins(item.tag)
    analysis = cofl.analysis(item.tag, days) or MarketAnalysis(source="none")
    sold = cofl.sold_summary(item.tag) if analysis.median_price is None or analysis.average_price is None else None
    median = analysis.median_price or (sold.median_price if sold else None)
    average = analysis.average_price or (sold.mean_price if sold else None)
    sample_count = analysis.total_sales or (sold.sale_count if sold else 0)

    if active.lowest_bin is None or median is None or median <= 0:
        return RejectedItem("ah-underpriced", item.name, "missing lowest BIN or median sold price")

    safe_resale = _safe_resale_price(active, median)
    expected_profit = safe_resale * (1 - AH_FEE_RATE) - active.lowest_bin
    underpriced_percent = (median - active.lowest_bin) / median * 100
    speed = normalize_ah_speed(
        sales_per_day=analysis.sales_per_day,
        median_sell_time_hours=analysis.median_sell_time_hours,
        sold_sample_count=sample_count,
        active_bin_count=active.active_count,
    )
    safe_budget = config.budget * (min(config.max_capital_percent_per_flip, item.max_budget_percent) / 100)

    if active.lowest_bin >= median * 0.85:
        rejection.append("lowest BIN is not at least 15% under median")
    if expected_profit < config.min_profit:
        rejection.append(f"profit below {config.min_profit:,.0f}")
    if analysis.sales_per_day < config.min_sales_per_day:
        rejection.append(f"sales/day below {config.min_sales_per_day:g}")
    if analysis.median_sell_time_hours is not None and analysis.median_sell_time_hours > config.max_median_sell_time_hours:
        rejection.append(f"median sell time above {config.max_median_sell_time_hours:g}h")
    if active.lowest_bin > safe_budget:
        rejection.append("lowest BIN exceeds safe budget fraction")
    if active.active_count > 80 and active.active_count > analysis.sales_per_day * 7:
        rejection.append("active listing count extreme compared to sales/day")
    if sample_count < 10:
        rejection.append("sold sample too thin")
    if active.second_lowest_bin and active.second_lowest_bin > active.lowest_bin * 1.5 and sample_count < 30:
        rejection.append("lowest listing may be an outlier or bad attribute item")
    if active.second_lowest_bin and active.lowest_bin < median * 0.2 and active.second_lowest_bin > active.lowest_bin * 2:
        rejection.append("lowest listing likely has missing upgrades or mismatched attributes")
    if analysis.coeff_variation is not None and analysis.coeff_variation > 0.7:
        rejection.append("sold prices are too volatile")

    confidence = min(100.0, speed.confidence_score * 0.7 + min(30.0, sample_count))
    if item.confidence == "medium":
        confidence *= 0.9
    elif item.confidence == "low" or item.requires_manual_verification:
        confidence *= 0.65
    manipulation_penalty = 20.0 if any("outlier" in reason or "volatile" in reason for reason in rejection) else 0.0
    budget_fit = max(0.0, min(100.0, 100.0 * (1 - active.lowest_bin / max(1.0, safe_budget))))
    competition = max(0.0, min(100.0, 100.0 - active.active_count / max(1.0, analysis.sales_per_day * 4) * 100))
    score = score_generic_opportunity(
        profit=expected_profit,
        profit_percent=expected_profit / active.lowest_bin * 100 if active.lowest_bin else 0.0,
        speed_score=speed.speed_score,
        confidence_score=confidence,
        budget_fit_score=budget_fit,
        competition_score=competition,
        risk_penalty=manipulation_penalty,
    )

    if rejection:
        return RejectedItem("ah-underpriced", item.name, " and ".join(rejection[:3]))

    return AhUnderpricedOpportunity(
        item=item.name,
        tag=item.tag,
        lowest_bin=active.lowest_bin,
        second_lowest_bin=active.second_lowest_bin,
        third_lowest_bin=active.third_lowest_bin,
        median_sold_price=median,
        average_sold_price=average,
        sales_per_day=analysis.sales_per_day,
        median_sell_time_hours=analysis.median_sell_time_hours,
        active_bin_count=active.active_count,
        underpriced_percent=underpriced_percent,
        safe_resale_price=safe_resale,
        expected_profit=expected_profit,
        confidence=confidence,
        risk=speed.risk_label,
        reason=f"{speed.reason}; {underpriced_percent:.1f}% below median",
        score=score,
        manual_action=(
            "Check AH manually before buying. Do not buy if the listing has bad attributes, missing upgrades, "
            "soulbound status, wrong rarity, or unusual modifiers."
        ),
    )


def _safe_resale_price(active: ActiveAuctions, median: float) -> float:
    candidates = [median * 0.95]
    if active.second_lowest_bin:
        candidates.append(active.second_lowest_bin * 0.99)
    if active.third_lowest_bin:
        candidates.append(active.third_lowest_bin * 0.97)
    return max(1.0, min(candidates))


def _watch_requirement_rejection(item: WatchItem, profile: PlayerProfile) -> str | None:
    requirements = item.min_requirements or {}
    for skill, required in (requirements.get("skills") or {}).items():
        actual = profile.skills.get(str(skill).lower())
        if actual is None or actual < int(required):
            return f"locked: {skill} {actual or 0} < {required}"
    for slayer, required in (requirements.get("slayers") or {}).items():
        actual = profile.slayer_levels.get(str(slayer).lower())
        if actual is None or actual < int(required):
            return f"locked: {slayer} slayer {actual or 0} < {required}"
    for floor in requirements.get("catacombs_floor_completions") or []:
        required_floor = int(floor)
        if profile.catacombs_floor_completions.get(required_floor, 0) <= 0:
            return f"locked: catacombs floor {required_floor} completion required"
    return None
