from types import SimpleNamespace

from skyflip.dashboard_modules import get_dashboard_module
from skyflip.dashboard_results import (
    detail_lines,
    empty_state_hint,
    merge_module_data,
    module_summary_lines,
    module_warnings,
    normalize_risk,
)
from skyflip.dashboard_menu_ui import _section_count
from skyflip.models import RejectedItem


def test_module_summary_includes_best_risk_warnings_and_refresh():
    module = get_dashboard_module("bazaar")
    data = SimpleNamespace(
        bazaar_spreads=[
            SimpleNamespace(product_id="A", final_score=20, risk="Low", should_test_first=False),
            SimpleNamespace(product_id="B", final_score=90, risk="High", should_test_first=True),
        ],
        bazaar_orders=[],
        rejected=[RejectedItem("bazaar-spread", "C", "filtered"), RejectedItem("craft", "D", "filtered")],
        warnings=["Bazaar spread section failed: timeout", "Accessories Helper failed: data"],
    )

    lines = module_summary_lines(data, module, last_refresh="2026-06-20 01:00:00")

    assert "Last refresh: 2026-06-20 01:00:00" in lines
    assert "Candidates: 2 accepted / 1 filtered" in lines
    assert "Best candidate: B" in lines
    assert "Risk: Low 1, Test first 1" in lines
    assert "Warnings: 1" in lines


def test_module_warnings_are_filtered_to_relevant_module():
    data = SimpleNamespace(
        bazaar_spreads=[SimpleNamespace(product_id="A")],
        bazaar_orders=[],
        warnings=["Bazaar order section failed: timeout", "Accessories Helper failed: missing inventory"],
    )

    assert module_warnings(data, get_dashboard_module("bazaar")) == ["Bazaar order section failed: timeout"]
    assert module_warnings(data, get_dashboard_module("accessories")) == ["Accessories Helper failed: missing inventory"]


def test_accessory_module_counts_current_view_not_recommendation_limit():
    module = get_dashboard_module("accessories")
    analysis = SimpleNamespace(
        view="all-missing",
        recommendations=[SimpleNamespace(entry=SimpleNamespace(display_name=f"Limited {index}")) for index in range(30)],
        all_missing=[SimpleNamespace(entry=SimpleNamespace(display_name=f"Missing {index}")) for index in range(362)],
    )
    data = SimpleNamespace(talisman_helper=analysis, rejected=[], warnings=[])

    lines = module_summary_lines(data, module, last_refresh="now")

    assert "Candidates: 362 accepted / 0 filtered" in lines
    assert _section_count(data, "talisman") == 362


def test_module_warnings_do_not_fallback_to_unrelated_global_warnings():
    data = SimpleNamespace(
        bazaar_spreads=[],
        bazaar_orders=[],
        craft=[SimpleNamespace(recipe=SimpleNamespace(name="Craft"))],
        warnings=["Bazaar order section failed: timeout"],
    )

    assert module_warnings(data, get_dashboard_module("craft")) == []


def test_detail_lines_use_consistent_risk_and_manual_verification():
    item = SimpleNamespace(
        product_id="ENCHANTED_CARROT",
        risk="Medium",
        should_test_first=True,
        manual_action="Suggested manual action: place a small order.",
        reason="wide spread",
        capital_required=100_000,
        estimated_total_profit=25_000,
        profit_percent=12.5,
        confidence_score=72,
    )

    rows = dict(detail_lines(item, "bazaar-spread"))

    assert rows["Risk"] == "Test first"
    assert "place a small order" in rows["Action"]
    assert "top order walls" in rows["Verify"]


def test_empty_state_hints_are_module_specific():
    assert "Min profit" in empty_state_hint("craft", "craft")
    assert "speed strictness" in empty_state_hint("bazaar", "bazaar-spread")


def test_merge_module_data_replaces_only_refreshed_module():
    module = get_dashboard_module("bazaar")
    existing = SimpleNamespace(
        profile="old-profile",
        budget=1,
        cache_ttl=300,
        craft=[SimpleNamespace(recipe=SimpleNamespace(name="Craft"))],
        bazaar_spreads=[SimpleNamespace(product_id="OLD")],
        bazaar_orders=[],
        conversions=[SimpleNamespace(name="Keep conversion")],
        ah_underpriced=[],
        talisman_helper=None,
        rejected=[RejectedItem("craft", "Craft", "old"), RejectedItem("bazaar-spread", "OLD", "old")],
        warnings=["Craft section failed: old", "Bazaar spread section failed: old"],
    )
    updated = SimpleNamespace(
        profile="new-profile",
        budget=2,
        cache_ttl=300,
        craft=[],
        bazaar_spreads=[SimpleNamespace(product_id="NEW")],
        bazaar_orders=[SimpleNamespace(product_id="ORDER")],
        conversions=[],
        ah_underpriced=[],
        talisman_helper=None,
        rejected=[RejectedItem("bazaar-order", "ORDER", "new")],
        warnings=["Bazaar order section failed: new", "Bazaar order section failed: new"],
    )

    merged = merge_module_data(existing, updated, module)

    assert [item.product_id for item in merged.bazaar_spreads] == ["NEW"]
    assert [item.name for item in merged.conversions] == ["Keep conversion"]
    assert [item.section for item in merged.rejected] == ["craft", "bazaar-order"]
    assert merged.warnings == ["Craft section failed: old", "Bazaar order section failed: new"]
    assert merged.profile == "new-profile"


def test_normalize_risk_labels():
    assert normalize_risk(SimpleNamespace(risk="Very fast")) == "Low"
    assert normalize_risk(SimpleNamespace(risk="Slow")) == "Medium"
    assert normalize_risk(SimpleNamespace(risk="Too slow")) == "High"
    assert normalize_risk(SimpleNamespace(risk="Low", should_test_first=True)) == "Test first"
