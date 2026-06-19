import argparse

from skyflip.module_presets import apply_module_preset, get_module_preset, list_module_presets


def test_module_presets_include_expected_sets():
    assert [preset.title for preset in list_module_presets("bazaar")] == ["Safe", "Recommended", "Risky"]
    assert [preset.title for preset in list_module_presets("craft")] == ["Safe", "Recommended", "Risky"]
    assert [preset.title for preset in list_module_presets("accessories")] == [
        "Budget",
        "Recommended",
        "Craft now",
        "Buy from AH",
        "Completion",
    ]
    assert [preset.title for preset in list_module_presets("compression")] == [
        "Conservative",
        "Balanced",
        "High throughput",
    ]
    assert [preset.title for preset in list_module_presets("ah-bin")] == [
        "Strict manual checks",
        "Balanced",
        "More candidates",
    ]


def test_apply_module_preset_updates_existing_settings_shape():
    args = argparse.Namespace(
        min_profit=1,
        min_profit_percent=1,
        min_sales_per_day=1,
        max_median_sell_time_hours=1,
        max_capital_percent_per_flip=1,
        use_buy_order_cost=False,
    )

    apply_module_preset(args, get_module_preset("craft", "risky"))

    assert args.min_profit == 1_000
    assert args.min_profit_percent == 2
    assert args.min_sales_per_day == 0.5
    assert args.max_median_sell_time_hours == 24
    assert args.max_capital_percent_per_flip == 60
    assert args.use_buy_order_cost is True


def test_accessory_preset_is_translation_to_filter_args():
    args = argparse.Namespace()

    apply_module_preset(args, get_module_preset("accessories", "craft-now"))

    assert args.accessory_view == "craftable-now"
    assert args.accessory_sort == "craft-cost"
    assert args.include_craftable_accessories is True
    assert args.include_ah_accessories is False
    assert args.only_craftable is True
