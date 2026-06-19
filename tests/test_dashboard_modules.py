from skyflip.dashboard import DEFAULT_SECTIONS
from skyflip.dashboard_modules import DASHBOARD_MODULES, get_dashboard_module, module_keys_for_sections


def test_dashboard_modules_map_to_existing_sections():
    module_sections = [section for module in DASHBOARD_MODULES for section in module.sections]

    assert module_sections == [
        "bazaar-spread",
        "bazaar-order",
        "craft",
        "talisman",
        "bazaar-compression",
        "ah-underpriced",
    ]
    assert set(module_sections) == set(DEFAULT_SECTIONS)


def test_module_result_sections_include_module_and_support_pages():
    module = get_dashboard_module("bazaar")

    assert module.result_sections == (
        "summary",
        "bazaar-spread",
        "bazaar-order",
        "warnings",
        "rejected",
    )


def test_module_keys_follow_selected_sections():
    keys = module_keys_for_sections({"craft", "ah-underpriced"})

    assert keys == ["craft", "ah-bin"]
