"""Sign conventions in the toxicity analysis must be exactly right — a flipped
sign would invert every research conclusion."""
import unittest

from src.analysis import maker_pnl_per_contract, bucket_label, HOURS_TO_CLOSE_BUCKETS, PRICE_BANDS


class TestMakerPnl(unittest.TestCase):
    def test_taker_bought_yes_and_won_maker_loses(self):
        # taker lifted the ask at 0.60, market settled YES (1.0):
        # maker sold at 0.60 something worth 1.00 -> lost 0.40
        self.assertAlmostEqual(maker_pnl_per_contract("yes", 0.60, 1.0), -0.40)

    def test_taker_bought_yes_and_lost_maker_wins(self):
        self.assertAlmostEqual(maker_pnl_per_contract("yes", 0.60, 0.0), 0.60)

    def test_taker_bought_no_and_won_maker_loses(self):
        # taker bought NO at yes_price 0.60 (paid 0.40 for NO), settled NO:
        # maker was long YES at 0.60, worth 0 -> lost 0.60
        self.assertAlmostEqual(maker_pnl_per_contract("no", 0.60, 0.0), -0.60)

    def test_taker_bought_no_and_lost_maker_wins(self):
        self.assertAlmostEqual(maker_pnl_per_contract("no", 0.60, 1.0), 0.40)

    def test_unknown_aggressor_is_skipped(self):
        self.assertIsNone(maker_pnl_per_contract(None, 0.60, 1.0))
        self.assertIsNone(maker_pnl_per_contract("yes", None, 1.0))


class TestBuckets(unittest.TestCase):
    def test_hours_to_close_bucketing(self):
        self.assertEqual(bucket_label(0.5, HOURS_TO_CLOSE_BUCKETS), "0-1h")
        self.assertEqual(bucket_label(23.9, HOURS_TO_CLOSE_BUCKETS), "16-24h")
        self.assertEqual(bucket_label(500, HOURS_TO_CLOSE_BUCKETS), ">48h")

    def test_price_bands(self):
        self.assertEqual(bucket_label(0.10, PRICE_BANDS), "OTM (0.01-0.20)")
        self.assertEqual(bucket_label(0.50, PRICE_BANDS), "ATM (0.20-0.80)")
        self.assertEqual(bucket_label(0.90, PRICE_BANDS), "ITM (0.80-0.99)")


if __name__ == "__main__":
    unittest.main()
