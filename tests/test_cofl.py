from skyflip.cofl import ActiveAuctions, CoflClient, SoldSummary, normalize_active, normalize_analysis, normalize_sold
from skyflip.http import ApiError


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


class FailingHttp:
    def __init__(self, exc):
        self.exc = exc
        self.calls = []

    def get_json(self, url):
        self.calls.append(url)
        raise self.exc


class SequenceHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get_json(self, url):
        self.calls.append(url)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return type("Result", (), {"payload": response, "source": "fake", "url": url})()


def test_cofl_bad_request_skips_remaining_tag_endpoints():
    http = FailingHttp(ApiError("400 Client Error: Bad Request for url"))
    cofl = CoflClient(http)

    assert cofl.active_bins("BAD_TAG") == ActiveAuctions(source="unsupported")
    assert cofl.analysis("BAD_TAG", 7) is None
    assert cofl.sold_summary("BAD_TAG") == SoldSummary()

    assert len(http.calls) == 1
    assert len(cofl.warnings) == 1


def test_cofl_rate_limit_does_not_skip_other_tags():
    http = SequenceHttp([
        ApiError("HTTP 429 for https://sky.coflnet.com/api/test"),
        {"medianPrice": 123_000, "totalSales": 20, "salesPerDay": 5},
    ])
    cofl = CoflClient(http)

    assert cofl.analysis("FIRST", 7) is None
    second = cofl.analysis("SECOND", 7)

    assert second is not None
    assert second.median_price == 123_000
    assert len(http.calls) == 2
    assert len(cofl.warnings) == 1
    assert cofl.failure_status("FIRST") == "rate_limited"
