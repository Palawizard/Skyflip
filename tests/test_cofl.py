from skyflip.cofl import normalize_active, normalize_analysis, normalize_sold


def test_normalize_analysis_current_shape():
    analysis = normalize_analysis(
        {
            "totalSales": 42,
            "salesPerDay": 6,
            "avgPrice": 120_000,
            "medianPrice": 100_000,
            "avgSellTimeSeconds": 7200,
            "medianSellTimeSeconds": 3600,
            "priceStdDev": 10_000,
            "priceCoeffVariation": 0.1,
            "binPercentage": 98,
            "sellSpeedBuckets": [{"speedCategory": "FAST"}],
        }
    )

    assert analysis.total_sales == 42
    assert analysis.average_sell_time_hours == 2
    assert analysis.median_sell_time_hours == 1
    assert analysis.sell_speed_buckets[0]["speedCategory"] == "FAST"


def test_normalize_active_sorts_prices():
    active = normalize_active([{"price": 300}, {"price": 100}, {"price": 200}])

    assert active.lowest_bin == 100
    assert active.second_lowest_bin == 200
    assert active.third_lowest_bin == 300


def test_normalize_active_accepts_skycofl_starting_bid_shape():
    active = normalize_active([{"startingBid": 389_999}, {"startingBid": 380_000}, {"startingBid": 400_000}])

    assert active.lowest_bin == 380_000
    assert active.second_lowest_bin == 389_999
    assert active.third_lowest_bin == 400_000


def test_normalize_sold_uses_highest_bid_amount():
    sold = normalize_sold([{"highestBidAmount": 100}, {"highestBidAmount": 300}, {"highestBidAmount": 200}])

    assert sold.sale_count == 3
    assert sold.median_price == 200
    assert sold.mean_price == 200
