"""Unit tests for the market making math.

Covers the verification checks from kalshi_mm_fix_instructions.md (reservation
direction, spread reasonableness, symmetry, inventory response, near-resolution
behavior) plus the estimators, rounding, sizing, order-side mapping, P&L
tracking, and the local order book.

Run:  python -m unittest discover -s tests -v
"""
import logging
import math
import time
import unittest
from datetime import datetime, timezone

from helper import (AvellanedaMarketMaker, PerformanceTracker,
                    VolatilityEstimator, estimate_k_from_depth,
                    resolution_risk_premium, floor_to_tick, ceil_to_tick, TICK)
from src.orderbook import OrderBook

logger = logging.getLogger("test")
logger.addHandler(logging.NullHandler())


def make_mm(**kwargs) -> AvellanedaMarketMaker:
    defaults = dict(logger=logger, client=None, market_ticker="TEST",
                    perf_log_path="")  # empty path disables perf file writes
    defaults.update(kwargs)
    return AvellanedaMarketMaker(**defaults)


class TestQuoteMath(unittest.TestCase):
    def setUp(self):
        self.mm = make_mm()
        self.sigma = 0.04  # dollars / sqrt(hour)

    def test_check1_reservation_direction(self):
        flat = self.mm.compute_quotes(0.50, q=0, tth=24, sigma=self.sigma)
        long_ = self.mm.compute_quotes(0.50, q=5, tth=24, sigma=self.sigma)
        short = self.mm.compute_quotes(0.50, q=-5, tth=24, sigma=self.sigma)
        self.assertAlmostEqual(flat["reservation"], 0.50)
        self.assertLess(long_["reservation"], 0.50)
        self.assertGreater(short["reservation"], 0.50)

    def test_check2_spread_reasonableness(self):
        q = self.mm.compute_quotes(0.50, q=0, tth=24, sigma=self.sigma)
        self.assertGreaterEqual(q["spread"], 0.02)
        self.assertLessEqual(q["spread"], 0.12)

    def test_check3_spread_symmetric_around_reservation(self):
        q = self.mm.compute_quotes(0.50, q=3, tth=24, sigma=self.sigma)
        self.assertAlmostEqual(q["raw_ask"] - q["reservation"],
                               q["reservation"] - q["raw_bid"], places=10)

    def test_check4_inventory_shifts_both_quotes_same_direction(self):
        flat = self.mm.compute_quotes(0.50, q=0, tth=24, sigma=self.sigma)
        long_ = self.mm.compute_quotes(0.50, q=8, tth=24, sigma=self.sigma)
        self.assertLess(long_["raw_bid"], flat["raw_bid"])
        self.assertLess(long_["raw_ask"], flat["raw_ask"])
        # constant gamma: spread width is inventory-independent
        self.assertAlmostEqual(long_["spread"], flat["spread"], places=10)

    def test_check5_spread_increases_near_resolution(self):
        far = self.mm.compute_quotes(0.50, q=0, tth=10, sigma=self.sigma)
        near = self.mm.compute_quotes(0.50, q=0, tth=0.05, sigma=self.sigma)
        self.assertGreater(near["spread"], far["spread"])

    def test_premium_maximal_at_even_odds(self):
        self.assertGreater(resolution_risk_premium(0.5, 1.0, 1.0),
                           resolution_risk_premium(0.9, 1.0, 1.0))
        self.assertGreater(resolution_risk_premium(0.5, 0.1, 1.0),
                           resolution_risk_premium(0.5, 10.0, 1.0))

    def test_rounding_never_tightens(self):
        q = self.mm.compute_quotes(0.4567, q=1, tth=5, sigma=self.sigma)
        self.assertLessEqual(q["bid"], q["raw_bid"] + 1e-9)
        self.assertGreaterEqual(q["ask"], q["raw_ask"] - 1e-9)
        self.assertGreater(q["ask"], q["bid"])
        # quotes land on the cent grid
        self.assertAlmostEqual(q["bid"] * 100, round(q["bid"] * 100), places=6)
        self.assertAlmostEqual(q["ask"] * 100, round(q["ask"] * 100), places=6)

    def test_quotes_clamped_to_valid_range(self):
        q = self.mm.compute_quotes(0.02, q=0, tth=24, sigma=self.sigma)
        self.assertGreaterEqual(q["bid"], TICK)
        self.assertLessEqual(q["ask"], 1.0 - TICK)

    def test_quotes_clamped_under_extreme_inventory(self):
        # regression: reservation far outside [0,1] (heavy inventory on a penny
        # contract) once produced asks at -1.18 and bids at 1.58
        for q_pos, mid in ((20, 0.03), (-20, 0.03), (20, 0.97), (-20, 0.97)):
            q = self.mm.compute_quotes(mid, q=q_pos, tth=24, sigma=0.2)
            self.assertGreaterEqual(q["bid"], TICK, (q_pos, mid))
            self.assertLessEqual(q["ask"], 1.0 - TICK, (q_pos, mid))
            self.assertGreater(q["ask"], q["bid"], (q_pos, mid))

    def test_quadratic_gamma_rises_with_inventory(self):
        mm = make_mm(gamma_mode="quadratic", max_position=10)
        self.assertAlmostEqual(mm.effective_gamma(0), mm.base_gamma)
        self.assertAlmostEqual(mm.effective_gamma(10), 2 * mm.base_gamma)

    def test_horizon_cap_bounds_long_dated_spread(self):
        mm = make_mm(max_horizon_h=24.0)
        week_out = mm.compute_quotes(0.50, q=0, tth=137, sigma=self.sigma)
        day_out = mm.compute_quotes(0.50, q=0, tth=24, sigma=self.sigma)
        # inventory-risk term is capped, so a week-out market quotes like a
        # day-out one (up to the tiny resolution premium difference)
        self.assertAlmostEqual(week_out["spread"], day_out["spread"], places=2)
        self.assertLess(week_out["spread"], 0.15)

    def test_fair_value_adjustment_shifts_anchor_not_spread(self):
        mm = make_mm(fair_value_adjustment=0.03)
        base = make_mm().compute_quotes(0.50, q=0, tth=24, sigma=self.sigma)
        shifted = mm.compute_quotes(0.50, q=0, tth=24, sigma=self.sigma)
        self.assertAlmostEqual(shifted["reservation"] - base["reservation"], 0.03)
        self.assertAlmostEqual(shifted["spread"], base["spread"], places=10)


class TestSizing(unittest.TestCase):
    def test_fill_cannot_breach_max_position(self):
        mm = make_mm(base_order_size=5, max_position=10)
        self.assertEqual(mm.desired_sizes(0), (5, 5))
        self.assertEqual(mm.desired_sizes(8), (2, 5))    # bid capped
        self.assertEqual(mm.desired_sizes(10), (0, 5))   # hard limit: no bid
        self.assertEqual(mm.desired_sizes(-10), (5, 0))  # hard limit: no ask


class TestOrderSideMapping(unittest.TestCase):
    def test_legacy_action_side_to_book_side(self):
        f = AvellanedaMarketMaker._order_book_side
        self.assertEqual(f({"action": "buy", "side": "yes"}), "bid")
        self.assertEqual(f({"action": "sell", "side": "no"}), "bid")
        self.assertEqual(f({"action": "buy", "side": "no"}), "ask")
        self.assertEqual(f({"action": "sell", "side": "yes"}), "ask")

    def test_yes_price_extraction(self):
        f = AvellanedaMarketMaker._order_yes_price
        self.assertAlmostEqual(f({"yes_price": 56}), 0.56)
        self.assertAlmostEqual(f({"yes_price_dollars": "0.5600"}), 0.56)
        self.assertAlmostEqual(f({"no_price": 40}), 0.60)


class TestVolatilityEstimator(unittest.TestCase):
    def test_cold_start_uses_bernoulli_scaling(self):
        v = VolatilityEstimator(calibration_constant=0.04)
        self.assertAlmostEqual(v.sigma(0.5), 0.04)          # scale = 1 at p=0.5
        self.assertLess(v.sigma(0.95), v.sigma(0.5))
        self.assertLess(v.sigma(0.05), v.sigma(0.5))

    def test_floor_applies_when_market_is_static(self):
        v = VolatilityEstimator(floor=0.01, min_obs=5)
        for i in range(20):
            v.add(float(i), 0.50)  # constant mid, 1s apart
        self.assertAlmostEqual(v.sigma(0.5), 0.01)

    def test_realized_vol_scales_with_movement(self):
        quiet, noisy = VolatilityEstimator(min_obs=5), VolatilityEstimator(min_obs=5)
        for i in range(50):
            quiet.add(float(i), 0.50 + 0.001 * (-1) ** i)
            noisy.add(float(i), 0.50 + 0.02 * (-1) ** i)
        self.assertGreater(noisy.sigma(0.5), quiet.sigma(0.5))


class TestKEstimation(unittest.TestCase):
    def test_recovers_synthetic_exponential_depth(self):
        k_true = 35.0
        profile = [(d, math.exp(k_true * d)) for d in (0.01, 0.02, 0.03, 0.05, 0.08)]
        self.assertAlmostEqual(estimate_k_from_depth(profile), k_true, delta=1.0)

    def test_degenerate_book_falls_back_to_default(self):
        self.assertEqual(estimate_k_from_depth([], default_k=40.0), 40.0)
        self.assertEqual(estimate_k_from_depth([(0.01, 5.0)], default_k=40.0), 40.0)
        # flat depth (slope 0) is not a valid exponential fit either
        flat = [(0.01, 10.0), (0.02, 10.0), (0.03, 10.0)]
        self.assertEqual(estimate_k_from_depth(flat, default_k=40.0), 40.0)


class TestPerformanceTracker(unittest.TestCase):
    @staticmethod
    def fill(action, side, count, yes_price_cents, ts=None):
        created = datetime.fromtimestamp(ts or time.time(), tz=timezone.utc)
        return {"action": action, "side": side, "count": count,
                "yes_price": yes_price_cents,
                "created_time": created.isoformat().replace("+00:00", "Z")}

    def test_round_trip_realized_pnl(self):
        t = PerformanceTracker(logger, log_path=None)
        t.on_fill(self.fill("buy", "yes", 10, 40), mid_at_fill=0.41)
        self.assertEqual(t.position, 10)
        self.assertAlmostEqual(t.avg_entry, 0.40)
        t.on_fill(self.fill("sell", "yes", 10, 50), mid_at_fill=0.49)
        self.assertEqual(t.position, 0)
        self.assertAlmostEqual(t.realized_pnl, 1.00)  # 10 * $0.10

    def test_buy_no_is_short_yes_exposure(self):
        t = PerformanceTracker(logger, log_path=None)
        t.on_fill(self.fill("buy", "no", 5, 60), mid_at_fill=0.60)
        self.assertEqual(t.position, -5)
        self.assertAlmostEqual(t.avg_entry, 0.60)

    def test_markout_sign_convention(self):
        now = time.time()
        t = PerformanceTracker(logger, log_path=None, markout_horizons=(60,))
        t.on_fill(self.fill("buy", "yes", 10, 40, ts=now), mid_at_fill=0.40)
        t.check_markouts(now + 61, current_mid=0.45)  # price moved with us
        self.assertAlmostEqual(t.markout_sums[60], 0.05)
        t2 = PerformanceTracker(logger, log_path=None, markout_horizons=(60,))
        t2.on_fill(self.fill("sell", "yes", 10, 40, ts=now), mid_at_fill=0.40)
        t2.check_markouts(now + 61, current_mid=0.45)  # adverse for a sell
        self.assertAlmostEqual(t2.markout_sums[60], -0.05)

    def test_unrealized_pnl_marks_to_mid(self):
        t = PerformanceTracker(logger, log_path=None)
        t.on_fill(self.fill("buy", "yes", 10, 40), mid_at_fill=0.40)
        s = t.summary(current_mid=0.45)
        self.assertAlmostEqual(s["unrealized_pnl"], 0.50)
        self.assertAlmostEqual(s["total_pnl"], 0.50)


class TestOrderBook(unittest.TestCase):
    def test_rest_and_ws_formats_and_implied_ask(self):
        rest = OrderBook("T")
        rest.apply_snapshot({"yes_dollars": [["0.40", "10"]],
                             "no_dollars": [["0.55", "5"]]})
        ws = OrderBook("T")
        ws.apply_snapshot({"yes_dollars_fp": [["0.40", "10.00"]],
                           "no_dollars_fp": [["0.55", "5.00"]]})
        for book in (rest, ws):
            self.assertAlmostEqual(book.best_yes_bid, 0.40)
            self.assertAlmostEqual(book.best_yes_ask, 0.45)  # 1 - 0.55
            self.assertAlmostEqual(book.mid, 0.425)
            self.assertAlmostEqual(book.spread, 0.05)

    def test_delta_application_and_level_removal(self):
        book = OrderBook("T")
        book.apply_snapshot({"yes_dollars": [["0.40", "10"]], "no_dollars": []}, seq=1)
        self.assertTrue(book.apply_delta("yes", "0.40", "-10.00", seq=2))
        self.assertIsNone(book.best_yes_bid)
        self.assertTrue(book.apply_delta("yes", "0.38", "3.00", seq=3))
        self.assertAlmostEqual(book.best_yes_bid, 0.38)

    def test_sequence_gap_detected(self):
        book = OrderBook("T")
        book.apply_snapshot({"yes_dollars": [], "no_dollars": []}, seq=1)
        self.assertFalse(book.apply_delta("yes", "0.40", "1.00", seq=3))  # gap

    def test_remove_liquidity_nets_out_own_orders(self):
        book = OrderBook("T")
        book.apply_snapshot({"yes_dollars": [["0.40", "10"], ["0.38", "5"]],
                             "no_dollars": [["0.55", "5"]]})
        book.remove_liquidity("yes", 0.40, 10)   # our whole level disappears
        self.assertAlmostEqual(book.best_yes_bid, 0.38)
        book.remove_liquidity("no", 0.55, 2)     # partial: level shrinks, stays
        self.assertAlmostEqual(book.no[0.55], 3.0)

    def test_depth_profile_is_cumulative_and_sorted(self):
        book = OrderBook("T")
        book.apply_snapshot({"yes_dollars": [["0.40", "10"], ["0.38", "20"]],
                             "no_dollars": [["0.55", "5"]]})
        profile = book.depth_profile()
        self.assertEqual([d for d, _ in profile], sorted(d for d, _ in profile))
        self.assertEqual(profile[-1][1], 35.0)  # total contracts


class TestTickHelpers(unittest.TestCase):
    def test_floor_and_ceil(self):
        self.assertAlmostEqual(floor_to_tick(0.567), 0.56)
        self.assertAlmostEqual(ceil_to_tick(0.561), 0.57)
        self.assertAlmostEqual(floor_to_tick(0.56), 0.56)  # exact stays put
        self.assertAlmostEqual(ceil_to_tick(0.56), 0.56)


if __name__ == "__main__":
    unittest.main()
