from __future__ import annotations

from dataclasses import dataclass, field

from .bazaar import BazaarClient
from .cofl import ActiveAuctions, CoflClient, MarketAnalysis, SoldSummary
from .recipes import Recipe


AH_FEE_RATE = 0.02


@dataclass(frozen=True)
class IngredientCost:
    name: str
    tag: str | None
    amount: float
    unit_cost: float
    total_cost: float
    source: str
    notes: list[str] = field(default_factory=list)
    children: list["IngredientCost"] = field(default_factory=list)


@dataclass(frozen=True)
class CraftCost:
    recipe_tag: str
    recipe_name: str
    total_cost: float
    per_output_cost: float
    ingredients: list[IngredientCost]
    unavailable: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MarketMetrics:
    safe_sell_price: float | None
    suggested_listing_price: float | None
    analysis: MarketAnalysis
    active: ActiveAuctions
    sold: SoldSummary
    median_sold_price: float | None
    volatility: float
    price_wall_score: float
    manipulation_risk_score: float
    confidence_score: float
    notes: list[str] = field(default_factory=list)
    status: str = "ok"


class PricingEngine:
    def __init__(
        self,
        recipes: dict[str, Recipe],
        bazaar: BazaarClient,
        cofl: CoflClient,
        *,
        use_buy_order_cost: bool = False,
        days: int = 7,
    ) -> None:
        self.recipes = recipes
        self.bazaar = bazaar
        self.cofl = cofl
        self.use_buy_order_cost = use_buy_order_cost
        self.days = days
        self._market_cache: dict[str, MarketMetrics] = {}
        self._craft_cache: dict[str, CraftCost] = {}

    def craft_cost(self, recipe: Recipe, stack: tuple[str, ...] = ()) -> CraftCost:
        if recipe.tag in self._craft_cache:
            return self._craft_cache[recipe.tag]
        if recipe.tag in stack:
            return CraftCost(recipe.tag, recipe.name, 0, 0, [], [f"recursive recipe chain: {' > '.join(stack)}"])

        ingredients: list[IngredientCost] = []
        unavailable: list[str] = []
        total = 0.0

        for ingredient in recipe.ingredients:
            cost = self._ingredient_cost(ingredient, stack + (recipe.tag,))
            ingredients.append(cost)
            total += cost.total_cost
            if cost.unit_cost <= 0:
                unavailable.append(f"{ingredient.name} has no usable price")
            if cost.source in {"unavailable", "nested-craft"}:
                unavailable.extend(cost.notes)

        result = CraftCost(
            recipe_tag=recipe.tag,
            recipe_name=recipe.name,
            total_cost=total,
            per_output_cost=total / max(1, recipe.quantity),
            ingredients=ingredients,
            unavailable=unavailable,
        )
        self._craft_cache[recipe.tag] = result
        return result

    def market_metrics(self, tag: str) -> MarketMetrics:
        if tag in self._market_cache:
            return self._market_cache[tag]

        notes: list[str] = []
        analysis = self.cofl.analysis(tag, self.days)
        sold = SoldSummary()
        if analysis is None or (analysis.median_price is None and analysis.total_sales == 0):
            sold = self.cofl.sold_summary(tag)
            analysis = MarketAnalysis(
                total_sales=sold.sale_count,
                sales_per_day=sold.sale_count / max(1, self.days),
                average_price=sold.mean_price,
                median_price=sold.median_price,
                source="sold-fallback",
            )
            notes.append("analysis unavailable; used sold-auction sample")
        else:
            sold = self.cofl.sold_summary(tag) if analysis.median_price is None else SoldSummary()

        active = self.cofl.active_bins(tag)
        median_sold = analysis.median_price or sold.median_price
        status = self._market_status(tag, analysis, active, sold)
        safe = self._safe_sell_price(median_sold, active, analysis, notes)
        if safe is None and status == "rate_limited":
            notes.append("market check skipped due to SkyCofl rate limit")
        confidence = self._confidence(analysis, active, sold, safe)
        price_wall = self._price_wall_score(active, analysis.sales_per_day)
        manipulation = self._manipulation_risk(analysis, active)
        suggested = self._suggest_listing_price(safe, active)
        result = MarketMetrics(
            safe_sell_price=safe,
            suggested_listing_price=suggested,
            analysis=analysis,
            active=active,
            sold=sold,
            median_sold_price=median_sold,
            volatility=max(0.0, float(analysis.coeff_variation or 0)),
            price_wall_score=price_wall,
            manipulation_risk_score=manipulation,
            confidence_score=confidence,
            notes=notes,
            status=status,
        )
        self._market_cache[tag] = result
        return result

    def _market_status(
        self,
        tag: str,
        analysis: MarketAnalysis,
        active: ActiveAuctions,
        sold: SoldSummary,
    ) -> str:
        failure_status = getattr(self.cofl, "failure_status", lambda _tag: None)(tag)
        sources = {analysis.source, active.source, sold.source, failure_status}
        if "unsupported" in sources:
            return "unsupported"
        if "rate_limited" in sources:
            return "rate_limited"
        if "unavailable" in sources:
            return "unavailable"
        return "ok"

    def _ingredient_cost(self, ingredient, stack: tuple[str, ...]) -> IngredientCost:
        if ingredient.source in {"npc", "fixed_cost"}:
            unit = float(ingredient.fixed_coin_cost or 0)
            return IngredientCost(ingredient.name, ingredient.tag, ingredient.amount, unit, unit * ingredient.amount, ingredient.source)

        if ingredient.source == "manual":
            return IngredientCost(
                ingredient.name,
                ingredient.tag,
                ingredient.amount,
                0,
                0,
                "unavailable",
                [f"{ingredient.name} is not priced automatically"],
            )

        if ingredient.source == "bazaar":
            price = self.bazaar.price_for(ingredient.tag or "", use_buy_order_cost=self.use_buy_order_cost)
            if price is not None:
                return IngredientCost(
                    ingredient.name,
                    ingredient.tag,
                    ingredient.amount,
                    price.unit_price,
                    price.unit_price * ingredient.amount,
                    f"bazaar:{price.source_field}",
                )
            fallback = self.cofl.bazaar_snapshot_price(ingredient.tag or "")
            if fallback is not None:
                return IngredientCost(ingredient.name, ingredient.tag, ingredient.amount, fallback, fallback * ingredient.amount, "cofl:bazaar-snapshot")
            return IngredientCost(ingredient.name, ingredient.tag, ingredient.amount, 0, 0, "unavailable", ["missing Bazaar product"])

        if ingredient.source == "ah":
            market = self.market_metrics(ingredient.tag or "")
            unit = market.active.lowest_bin or market.median_sold_price
            if unit is not None and unit > 0:
                return IngredientCost(
                    ingredient.name,
                    ingredient.tag,
                    ingredient.amount,
                    unit,
                    unit * ingredient.amount,
                    "ah",
                    ["AH-priced ingredient; verify the input manually"],
                )
            return IngredientCost(ingredient.name, ingredient.tag, ingredient.amount, 0, 0, "unavailable", ["missing AH input price"])

        if ingredient.source in {"craft", "previous_recipe"} and ingredient.tag in self.recipes:
            sub_recipe = self.recipes[ingredient.tag]
            crafted = self.craft_cost(sub_recipe, stack)
            crafted_unit = crafted.per_output_cost
            children = crafted.ingredients
            market = self.market_metrics(ingredient.tag)
            ah_unit = market.active.lowest_bin or market.median_sold_price
            if ah_unit is not None and ah_unit > 0 and ah_unit < crafted_unit:
                return IngredientCost(
                    ingredient.name,
                    ingredient.tag,
                    ingredient.amount,
                    ah_unit,
                    ah_unit * ingredient.amount,
                    "ah-buy-subitem",
                    ["cheaper than crafting sub-item"],
                )
            return IngredientCost(
                ingredient.name,
                ingredient.tag,
                ingredient.amount,
                crafted_unit,
                crafted_unit * ingredient.amount,
                "nested-craft",
                crafted.unavailable,
                children,
            )

        return IngredientCost(ingredient.name, ingredient.tag, ingredient.amount, 0, 0, "unavailable", [f"unsupported ingredient source {ingredient.source}"])

    def _safe_sell_price(
        self,
        median_sold: float | None,
        active: ActiveAuctions,
        analysis: MarketAnalysis,
        notes: list[str],
    ) -> float | None:
        candidates = [value for value in [median_sold, active.second_lowest_bin] if value is not None and value > 0]
        if active.lowest_bin is not None and active.active_count >= 3:
            candidates.append(active.lowest_bin * 0.99)
        if not candidates:
            notes.append("no reliable sold price or active BIN price")
            return None
        safe = min(candidates)
        cv = analysis.coeff_variation or 0
        if cv >= 1.0:
            safe *= 0.75
            notes.append("large volatility penalty applied")
        elif cv >= 0.5:
            safe *= 0.85
            notes.append("volatility penalty applied")
        return safe

    def _confidence(self, analysis: MarketAnalysis, active: ActiveAuctions, sold: SoldSummary, safe: float | None) -> float:
        if safe is None:
            return 0.0
        sales_score = min(1.0, max(analysis.total_sales, sold.sale_count) / 30)
        active_score = 0.8 if active.active_count else 0.35
        volatility_penalty = min(0.4, (analysis.coeff_variation or 0) * 0.2)
        return max(0.0, min(1.0, 0.25 + sales_score * 0.5 + active_score * 0.25 - volatility_penalty))

    def _price_wall_score(self, active: ActiveAuctions, sales_per_day: float) -> float:
        if not active.prices:
            return 0.4
        low = active.lowest_bin or active.prices[0]
        near_low = sum(1 for price in active.prices[:50] if price <= low * 1.03)
        saturation = active.active_count / max(1.0, sales_per_day * 2)
        return max(0.0, min(1.0, near_low / 20 + saturation / 5))

    def _manipulation_risk(self, analysis: MarketAnalysis, active: ActiveAuctions) -> float:
        risk = 0.0
        if analysis.total_sales < 15:
            risk += 0.35
        if (analysis.coeff_variation or 0) > 0.5:
            risk += 0.35
        if active.lowest_bin and active.second_lowest_bin and active.second_lowest_bin > active.lowest_bin * 1.5:
            risk += 0.25
        return max(0.0, min(1.0, risk))

    def _suggest_listing_price(self, safe: float | None, active: ActiveAuctions) -> float | None:
        if safe is None:
            return None
        if active.lowest_bin and active.lowest_bin > 0:
            return max(1.0, min(safe, active.lowest_bin - 1))
        return safe
