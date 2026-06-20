from __future__ import annotations

from argparse import Namespace
from dataclasses import dataclass
from typing import Any

from .dashboard_modules import DASHBOARD_MODULES


@dataclass(frozen=True)
class ModulePreset:
    module_key: str
    key: str
    title: str
    risk_level: str
    settings_patch: dict[str, Any]


MODULE_PRESETS: tuple[ModulePreset, ...] = (
    ModulePreset(
        module_key="bazaar",
        key="safe",
        title="Safe",
        risk_level="Low",
        settings_patch={
            "spread_limit": 8,
            "min_spread_profit_per_unit": 1_000.0,
            "min_spread_volume_week": 100_000.0,
            "max_spread_depth_ratio": 0.8,
            "max_capital_percent_per_flip": 15.0,
            "max_estimated_bottleneck_minutes": 120.0,
            "min_speed_confidence": 60.0,
            "conservative_speed": True,
        },
    ),
    ModulePreset(
        module_key="bazaar",
        key="recommended",
        title="Recommended",
        risk_level="Medium",
        settings_patch={
            "spread_limit": None,
            "min_spread_profit_per_unit": 0.0,
            "min_spread_volume_week": 25_000.0,
            "max_spread_depth_ratio": 1.25,
            "max_capital_percent_per_flip": 35.0,
            "max_estimated_bottleneck_minutes": 240.0,
            "min_speed_confidence": 35.0,
            "conservative_speed": True,
        },
    ),
    ModulePreset(
        module_key="bazaar",
        key="risky",
        title="Risky",
        risk_level="High",
        settings_patch={
            "spread_limit": 15,
            "min_spread_profit_per_unit": 0.0,
            "min_spread_volume_week": 10_000.0,
            "max_spread_depth_ratio": 2.0,
            "max_capital_percent_per_flip": 60.0,
            "max_estimated_bottleneck_minutes": 480.0,
            "min_speed_confidence": 20.0,
            "conservative_speed": False,
        },
    ),
    ModulePreset(
        module_key="craft",
        key="safe",
        title="Safe",
        risk_level="Low",
        settings_patch={
            "min_profit": 20_000.0,
            "min_profit_percent": 8.0,
            "min_sales_per_day": 5.0,
            "max_median_sell_time_hours": 8.0,
            "max_capital_percent_per_flip": 15.0,
            "use_buy_order_cost": True,
        },
    ),
    ModulePreset(
        module_key="craft",
        key="recommended",
        title="Recommended",
        risk_level="Medium",
        settings_patch={
            "min_profit": 5_000.0,
            "min_profit_percent": 4.0,
            "min_sales_per_day": 2.0,
            "max_median_sell_time_hours": 12.0,
            "max_capital_percent_per_flip": 35.0,
            "use_buy_order_cost": True,
        },
    ),
    ModulePreset(
        module_key="craft",
        key="risky",
        title="Risky",
        risk_level="High",
        settings_patch={
            "min_profit": 1_000.0,
            "min_profit_percent": 2.0,
            "min_sales_per_day": 0.5,
            "max_median_sell_time_hours": 24.0,
            "max_capital_percent_per_flip": 60.0,
            "use_buy_order_cost": True,
        },
    ),
    ModulePreset(
        module_key="accessories",
        key="budget",
        title="Budget",
        risk_level="Low",
        settings_patch={
            "accessory_view": "recommended",
            "accessory_sort": "coin-per-mp",
            "max_accessory_price": 500_000.0,
            "max_accessory_recommendations": 10,
            "include_locked_accessories": False,
            "include_uncertain_accessories": True,
            "include_manual_unlocks": True,
            "include_ah_accessories": True,
            "include_craftable_accessories": True,
            "only_craftable": False,
            "only_ah": False,
            "show_locked": False,
        },
    ),
    ModulePreset(
        module_key="accessories",
        key="recommended",
        title="Recommended",
        risk_level="Medium",
        settings_patch={
            "accessory_view": "recommended",
            "accessory_sort": "score",
            "max_accessory_price": None,
            "max_accessory_recommendations": 15,
            "include_locked_accessories": False,
            "include_uncertain_accessories": True,
            "include_manual_unlocks": True,
            "include_ah_accessories": True,
            "include_craftable_accessories": True,
            "only_craftable": False,
            "only_ah": False,
            "show_locked": False,
        },
    ),
    ModulePreset(
        module_key="accessories",
        key="craft-now",
        title="Craft now",
        risk_level="Low",
        settings_patch={
            "accessory_view": "craftable-now",
            "accessory_sort": "craft-cost",
            "max_accessory_price": None,
            "max_accessory_recommendations": 15,
            "include_locked_accessories": False,
            "include_uncertain_accessories": True,
            "include_manual_unlocks": False,
            "include_ah_accessories": False,
            "include_craftable_accessories": True,
            "only_craftable": True,
            "only_ah": False,
            "show_locked": False,
        },
    ),
    ModulePreset(
        module_key="accessories",
        key="buy-ah",
        title="Buy from AH",
        risk_level="Medium",
        settings_patch={
            "accessory_view": "buy-from-ah",
            "accessory_sort": "price",
            "max_accessory_price": None,
            "max_accessory_recommendations": 15,
            "include_locked_accessories": False,
            "include_uncertain_accessories": True,
            "include_manual_unlocks": False,
            "include_ah_accessories": True,
            "include_craftable_accessories": False,
            "only_craftable": False,
            "only_ah": True,
            "show_locked": False,
        },
    ),
    ModulePreset(
        module_key="accessories",
        key="completion",
        title="Completion",
        risk_level="High",
        settings_patch={
            "accessory_view": "all-missing",
            "accessory_sort": "score",
            "max_accessory_price": None,
            "max_accessory_recommendations": 30,
            "include_locked_accessories": True,
            "include_uncertain_accessories": True,
            "include_manual_unlocks": True,
            "include_ah_accessories": True,
            "include_craftable_accessories": True,
            "only_craftable": False,
            "only_ah": False,
            "show_locked": True,
        },
    ),
    ModulePreset(
        module_key="compression",
        key="conservative",
        title="Conservative",
        risk_level="Low",
        settings_patch={
            "conversion_mode": "conservative",
            "limit_per_section": 8,
            "min_profit": 10_000.0,
            "min_profit_percent": 5.0,
            "max_capital_percent_per_flip": 15.0,
        },
    ),
    ModulePreset(
        module_key="compression",
        key="balanced",
        title="Balanced",
        risk_level="Medium",
        settings_patch={
            "conversion_mode": "realistic",
            "limit_per_section": 10,
            "min_profit": 5_000.0,
            "min_profit_percent": 4.0,
            "max_capital_percent_per_flip": 35.0,
        },
    ),
    ModulePreset(
        module_key="compression",
        key="high-throughput",
        title="High throughput",
        risk_level="High",
        settings_patch={
            "conversion_mode": "realistic",
            "limit_per_section": 15,
            "min_profit": 2_000.0,
            "min_profit_percent": 2.0,
            "max_capital_percent_per_flip": 60.0,
        },
    ),
    ModulePreset(
        module_key="ah-bin",
        key="strict",
        title="Strict manual checks",
        risk_level="Low",
        settings_patch={
            "limit_per_section": 5,
            "min_profit": 100_000.0,
            "min_profit_percent": 12.0,
            "max_median_sell_time_hours": 8.0,
        },
    ),
    ModulePreset(
        module_key="ah-bin",
        key="balanced",
        title="Balanced",
        risk_level="Medium",
        settings_patch={
            "limit_per_section": 10,
            "min_profit": 20_000.0,
            "min_profit_percent": 6.0,
            "max_median_sell_time_hours": 12.0,
        },
    ),
    ModulePreset(
        module_key="ah-bin",
        key="more-candidates",
        title="More candidates",
        risk_level="High",
        settings_patch={
            "limit_per_section": 20,
            "min_profit": 5_000.0,
            "min_profit_percent": 3.0,
            "max_median_sell_time_hours": 24.0,
        },
    ),
)

_PRESETS_BY_MODULE = {
    module.key: tuple(preset for preset in MODULE_PRESETS if preset.module_key == module.key)
    for module in DASHBOARD_MODULES
}

_PRESETS_BY_KEY = {(preset.module_key, preset.key): preset for preset in MODULE_PRESETS}


def list_module_presets(module_key: str) -> tuple[ModulePreset, ...]:
    return _PRESETS_BY_MODULE.get(module_key, ())


def get_module_preset(module_key: str, preset_key: str) -> ModulePreset:
    return _PRESETS_BY_KEY[(module_key, preset_key)]


def apply_module_preset(args: Namespace, preset: ModulePreset) -> None:
    for field, value in preset.settings_patch.items():
        setattr(args, field, value)
