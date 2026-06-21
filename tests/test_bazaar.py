from skyflip.bazaar import BazaarClient
from skyflip.http import ApiResult


class FakeHttp:
    def __init__(self, products):
        self.products = products

    def get_json(self, url, *, headers=None, cache_key=None, use_cache=True):
        return ApiResult({"products": self.products}, source="fake", url=url)


def test_price_for_uses_instant_buy_side_when_requested():
    client = BazaarClient(
        FakeHttp(
            {
                "POISON_SAMPLE": {
                    "buy_summary": [{"pricePerUnit": 659_439}],
                    "sell_summary": [{"pricePerUnit": 613_693}],
                    "quick_status": {"buyPrice": 660_000, "sellPrice": 610_000},
                }
            }
        )
    )

    price = client.price_for("POISON_SAMPLE", use_buy_order_cost=True)

    assert price is not None
    assert price.unit_price == 659_439
    assert "buy_summary" in price.source_field


def test_price_for_can_still_use_sell_order_side():
    client = BazaarClient(
        FakeHttp(
            {
                "POISON_SAMPLE": {
                    "buy_summary": [{"pricePerUnit": 659_439}],
                    "sell_summary": [{"pricePerUnit": 613_693}],
                    "quick_status": {"buyPrice": 660_000, "sellPrice": 610_000},
                }
            }
        )
    )

    price = client.price_for("POISON_SAMPLE", use_buy_order_cost=False)

    assert price is not None
    assert price.unit_price == 613_693
    assert "sell_summary" in price.source_field
