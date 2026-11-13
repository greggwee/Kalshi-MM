"""Tape markouts and features: signs, the tape mid, look-ahead, clustering."""
import sqlite3
import unittest

import numpy as np

from src.markouts import (FEATURES, TapeMid, aggregate_orders, build_dataset,
                          date_bootstrap, date_order, event_date, market_rows)
from src.storage import SCHEMA

CLOSE = 100_000.0


def rows(prints, close=CLOSE, settle=1.0, horizons=(10, 60)):
    """prints: (t_seconds, yes_price, count, taker_side)."""
    ts, p, c, s = zip(*prints)
    return market_rows([t * 1000 for t in ts], p, c, s, close, settle, horizons)


class TestAggregation(unittest.TestCase):
    def test_same_ms_same_side_prints_are_one_order(self):
        ts = np.array([1000, 1000, 1000, 2000.0])
        o = aggregate_orders(ts, np.array([0.50, 0.51, 0.52, 0.40]),
                             np.array([10.0, 10.0, 20.0, 5.0]), np.array([1, 1, 1, -1.0]))
        self.assertEqual(list(o["size"]), [40.0, 5.0])
        self.assertAlmostEqual(o["price"][0], (5.0 + 5.1 + 10.4) / 40.0)
        self.assertAlmostEqual(o["sweep"][0], 0.02)
        self.assertAlmostEqual(o["touch"][0], 0.50)     # a buy's best level is its lowest
        self.assertAlmostEqual(o["touch"][1], 0.40)
        self.assertEqual(list(o["n_prints"]), [3.0, 1.0])

    def test_same_ms_opposite_sides_stay_separate(self):
        o = aggregate_orders(np.array([1000, 1000.0]), np.array([0.5, 0.4]),
                             np.array([1.0, 1.0]), np.array([1, -1.0]))
        self.assertEqual(len(o["ts"]), 2)


class TestTapeMid(unittest.TestCase):
    def setUp(self):
        self.mid = TapeMid(np.array([0.0, 10.0, 20.0]), np.array([0.50, 0.46, 0.52]),
                           np.array([1.0, -1.0, 1.0]))

    def test_mid_is_average_of_last_buy_and_last_sell(self):
        m, spread, two = self.mid.at(np.array([15.0]))
        self.assertAlmostEqual(m[0], 0.48)
        self.assertAlmostEqual(spread[0], 0.04)
        self.assertTrue(two[0])

    def test_exclusive_lookup_ignores_the_print_at_t(self):
        m, _, _ = self.mid.at(np.array([20.0]), inclusive=False)
        self.assertAlmostEqual(m[0], 0.48)
        m, _, _ = self.mid.at(np.array([20.0]), inclusive=True)
        self.assertAlmostEqual(m[0], 0.49)

    def test_one_sided_tape_falls_back_to_last_print(self):
        m, spread, two = self.mid.at(np.array([5.0]))
        self.assertAlmostEqual(m[0], 0.50)
        self.assertTrue(np.isnan(spread[0]))
        self.assertFalse(two[0])

    def test_stale_side_and_crossed_proxies_fall_back(self):
        m, _, two = self.mid.at(np.array([2000.0]))       # both sides older than 900s
        self.assertFalse(two[0])
        self.assertAlmostEqual(m[0], 0.52)
        crossed = TapeMid(np.array([0.0, 10.0]), np.array([0.40, 0.45]), np.array([1.0, -1.0]))
        m, _, two = crossed.at(np.array([11.0]))            # last buy below last sell
        self.assertFalse(two[0])
        self.assertAlmostEqual(m[0], 0.45)

    def test_before_first_print_is_nan(self):
        m, _, _ = self.mid.at(np.array([-1.0]))
        self.assertTrue(np.isnan(m[0]))


class TestMarkouts(unittest.TestCase):
    def test_maker_sign_convention(self):
        # taker buys YES at 0.52 while the tape mid is 0.48: the maker sold 4c
        # rich; the market then trades up to a 0.56 mid: the maker is 4c under.
        r = rows([(0, 0.50, 1, "yes"), (1, 0.46, 1, "no"), (100, 0.52, 1, "yes"),
                  (130, 0.54, 1, "no"), (131, 0.58, 1, "yes")])
        i = 2
        self.assertAlmostEqual(r["edge"][i], 0.04)
        self.assertAlmostEqual(r["mo_10"][i], 0.52 - 0.49)     # own print is the ask proxy
        self.assertAlmostEqual(r["mo_60"][i], 0.52 - 0.56)
        self.assertAlmostEqual(r["mo_settle"][i], 0.52 - 1.0)
        # and for a taker SELL the maker bought: profit when the mid ends higher
        j = 3
        self.assertAlmostEqual(r["mo_10"][j], -(0.54 - 0.56))
        self.assertAlmostEqual(r["mo_settle"][j], 1.0 - 0.54)

    def test_sweep_is_marked_at_the_touch_not_the_vwap(self):
        r = rows([(0, 0.50, 1, "yes"), (1, 0.46, 1, "no"),
                  (100, 0.50, 5, "yes"), (100, 0.55, 5, "yes")])
        self.assertAlmostEqual(r["price"][2], 0.525)
        self.assertAlmostEqual(r["edge"][2], 0.50 - 0.48)
        self.assertAlmostEqual(r["mo_settle"][2], 0.50 - 1.0)

    def test_horizon_past_close_is_nan(self):
        r = rows([(0, 0.5, 1, "yes"), (1, 0.4, 1, "no"), (CLOSE - 30, 0.5, 1, "yes")])
        self.assertTrue(np.isfinite(r["mo_10"][2]))
        self.assertTrue(np.isnan(r["mo_60"][2]))

    def test_features_do_not_look_ahead(self):
        base = [(0, 0.50, 5, "yes"), (20, 0.46, 3, "no"), (100, 0.52, 2, "yes")]
        a = rows(base)
        b = rows(base + [(101, 0.90, 500, "yes"), (150, 0.10, 500, "no")])
        for name in FEATURES:
            np.testing.assert_allclose(a[name][:3], b[name][:3], equal_nan=True,
                                       err_msg=name)

    def test_flow_features(self):
        r = rows([(0, 0.50, 6, "yes"), (10, 0.46, 2, "no"), (50, 0.50, 1, "yes"),
                  (1000, 0.50, 1, "no")])
        self.assertAlmostEqual(r["flow_align_300"][2], (6 - 2) / 8)   # buying into buying
        self.assertAlmostEqual(r["log_vol_300"][2], np.log1p(8))
        self.assertAlmostEqual(r["run_60"][2], np.log1p(6))
        self.assertAlmostEqual(r["flow_align_300"][3], 0.0)           # window is empty
        self.assertAlmostEqual(r["log_dt_prev"][3], np.log1p(950))

    def test_unknown_aggressor_and_null_prices_are_dropped(self):
        r = rows([(0, 0.5, 1, "yes"), (1, 0.5, 1, None), (2, float("nan"), 1, "no"),
                  (3, 0.4, 1, "no")])
        self.assertEqual(len(r["ts"]), 2)

    def test_empty_market(self):
        r = market_rows([], [], [], [], CLOSE, 1.0)
        self.assertEqual(len(r["ts"]), 0)
        self.assertIn("mo_settle", r)


class TestDataset(unittest.TestCase):
    def test_build_dataset_reads_settled_markets_only(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript(SCHEMA)
        for ticker, result in (("S-26NOV01-A", "yes"), ("S-26NOV02-A", ""), ("T-26NOV01-A", "no")):
            conn.execute("INSERT INTO markets (ticker, close_time, result) VALUES (?,?,?)",
                         (ticker, "2026-11-02T06:00:00Z", result))
            for k, side in enumerate(("yes", "no", "yes")):
                conn.execute(
                    "INSERT INTO trades (trade_id, ts_ms, market_ticker, yes_price, count,"
                    " taker_side, source) VALUES (?,?,?,?,?,?,?)",
                    (f"{ticker}{k}", 1_793_500_000_000 + k * 1000, ticker, 0.5, 1.0, side, "t"))
        d = build_dataset(conn)
        self.assertEqual(sorted(set(d["ticker"])), ["S-26NOV01-A", "T-26NOV01-A"])
        self.assertEqual(set(build_dataset(conn, series=["T"])["series"]), {"T"})
        self.assertEqual(set(d["date"]), {"26NOV01"})

    def test_event_date_and_ordering(self):
        self.assertEqual(event_date("KXHIGHNY-26NOV13-T57"), "26NOV13")
        self.assertEqual(date_order(["26NOV02", "26AUG30", "26NOV01"]),
                         ["26AUG30", "26NOV01", "26NOV02"])


class TestDateBootstrap(unittest.TestCase):
    def test_interval_reflects_dates_not_prints(self):
        # 2 dates x 5000 prints: a print-level interval would be tiny, the
        # date-level one must span the two date means.
        v = np.concatenate([np.full(5000, 1.0), np.full(5000, -1.0)])
        d = np.concatenate([np.full(5000, "a"), np.full(5000, "b")])
        r = date_bootstrap(v, np.ones(10000), d, n_boot=500)
        self.assertEqual(r["n_dates"], 2)
        self.assertAlmostEqual(r["mean"], 0.0)
        self.assertLessEqual(r["lo"], -0.99)
        self.assertGreaterEqual(r["hi"], 0.99)

    def test_weights_and_nans(self):
        v = np.array([1.0, 3.0, np.nan])
        r = date_bootstrap(v, np.array([1.0, 3.0, 9.0]), np.array(["a", "a", "b"]), n_boot=50)
        self.assertAlmostEqual(r["mean"], 2.5)
        self.assertEqual(r["n_dates"], 1)


if __name__ == "__main__":
    unittest.main()
