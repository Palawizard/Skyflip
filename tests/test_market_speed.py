from pathlib import Path

from skyflip.market_speed import combine_bazaar_side_speeds, estimate_bazaar_order_fill_speed


def test_high_weekly_volume_with_huge_depth_is_not_very_fast():
    speed = estimate_bazaar_order_fill_speed(
        side="buy",
        recommended_price=100,
        order_summary=(
            {"pricePerUnit": 100, "amount": 2_000_000},
            {"pricePerUnit": 99.9, "amount": 1_000},
        ),
        moving_week=20_000_000,
        live_volume=1_000_000,
    )

    assert speed.risk_label != "Very fast"
    assert speed.has_huge_wall
    assert "wall" in speed.reason


def test_one_fast_side_one_slow_side_makes_slow_bottleneck():
    fast = estimate_bazaar_order_fill_speed(
        side="buy",
        recommended_price=100,
        order_summary=({"pricePerUnit": 100, "amount": 100}, {"pricePerUnit": 99, "amount": 100}),
        moving_week=5_000_000,
        live_volume=500_000,
    )
    slow = estimate_bazaar_order_fill_speed(
        side="sell",
        recommended_price=120,
        order_summary=({"pricePerUnit": 120, "amount": 200_000}, {"pricePerUnit": 121, "amount": 10}),
        moving_week=50_000,
        live_volume=500,
    )

    bottleneck = combine_bazaar_side_speeds(fast, slow)

    assert bottleneck.risk_label in {"Slow", "Too slow"}
    assert bottleneck.estimated_minutes >= slow.estimated_minutes


def test_missing_order_book_has_low_confidence():
    speed = estimate_bazaar_order_fill_speed(
        side="sell",
        recommended_price=120,
        order_summary=(),
        moving_week=1_000_000,
        live_volume=100_000,
    )

    assert speed.risk_label == "Too slow"
    assert speed.confidence_score < 50
    assert "missing order book" in speed.reason


def test_outlier_top_order_reduces_confidence():
    normal = estimate_bazaar_order_fill_speed(
        side="buy",
        recommended_price=100,
        order_summary=({"pricePerUnit": 100, "amount": 100}, {"pricePerUnit": 99, "amount": 100}),
        moving_week=1_000_000,
        live_volume=100_000,
    )
    outlier = estimate_bazaar_order_fill_speed(
        side="buy",
        recommended_price=120,
        order_summary=({"pricePerUnit": 120, "amount": 100}, {"pricePerUnit": 99, "amount": 100}),
        moving_week=1_000_000,
        live_volume=100_000,
    )

    assert outlier.confidence_score < normal.confidence_score
    assert "outlier" in outlier.reason


def test_gitignore_excludes_local_sensitive_and_generated_files():
    text = Path(".gitignore").read_text(encoding="utf-8")

    for pattern in ("config.json", ".env", ".cache/", "out/", "*_selected_profile.json", "*.token"):
        assert pattern in text
