"""Tests for the Feil-Nendel prediction market model and its live glue.

The solver is checked against numbers published in the paper (arXiv:2607.17991,
figures 1-2 and table 2), against the PDE it claims to solve, and against the
closed forms that exist at settlement. Expected values marked "paper" are read
from the paper, not produced by this code."""
import json
import logging
import math
import os
import shutil
import sqlite3
import tempfile
import unittest

import numpy as np

from src.diagnostics import write_report
from src.journal import Journal, read_journal
from src.pmm import ModelParams, optimal_ask, optimal_bid, simulate, solve
from src.pmm_calibrate import (FlowEstimator, LogitVol, K_MAX, K_MIN, fit_ramps, fit_series_ramps,
                               noise_robust_variance)
from src.pmm_quoter import PMMQuoter, maker_fee_rate
from src.session import TradingSession
from src.shadow import ShadowEngine, fetch_settle_price
from src.storage import SCHEMA
from tests.fake_exchange import FakeClock, FakeExchange

logger = logging.getLogger("test_pmm")
logger.addHandler(logging.NullHandler())
logger.propagate = False

PAPER = ModelParams()
KALSHI = ModelParams(T=40.0, sigma0=0.15, sigma1=0.35, sigma2=0.0, eta=6.0, A0=5.7, A1=500.0,
                     xi=8.0, nu=1.0, k0=200.0, k1=125.0, kappa=5.0, gamma=0.2, gamma_T=0.05,
                     Q=10.0, delta=2.0, min_offset=0.005)


class TestOptimalQuote(unittest.TestCase):
    """Prop. 3.2: the unique maximizer of Lambda * (edge - z)."""

    def brute(self, P, tau, p, z):
        grid = np.linspace(1e-6, 1 - 1e-6, 400001)
        return grid[np.argmax(P.intensity_bid(tau, p, grid) * (p - grid - P.fee(grid) - z))]

    def test_matches_a_brute_force_search(self):
        for P in (PAPER, ModelParams(nu=0.5, k0=120.0, k1=120.0),
                  ModelParams(nu=2.0, k0=20.0, k1=60.0, maker_fee=0.0175)):
            for p, z in ((0.5, 0.0), (0.1, 0.002), (0.9, -0.01), (0.03, 0.01), (0.5, 0.04),
                         (0.97, 0.02)):
                got = float(optimal_bid(P, P.k(0.5), np.array([p]), np.array([z]))[0])
                self.assertAlmostEqual(got, self.brute(P, 0.5, p, z), places=4, msg=(p, z))

    def test_no_edge_means_no_bid(self):
        # z >= p: every bid loses value, and Lambda^b(0) = 0 makes "no bid" the best
        self.assertEqual(float(optimal_bid(PAPER, 40.0, np.array([0.2]), np.array([0.2]))[0]), 0.0)
        self.assertEqual(float(optimal_bid(PAPER, 40.0, np.array([0.2]), np.array([0.5]))[0]), 0.0)
        p, z = np.array([0.2]), np.array([0.5])
        self.assertEqual(float(optimal_ask(PAPER, 40.0, 1 - p, z)[0]), 1.0)

    def test_ask_is_the_bid_seen_from_the_no_side(self):
        p, z = np.array([0.3, 0.8]), np.array([0.01, -0.02])
        ask = optimal_ask(PAPER, 40.0, p, z)
        bid = optimal_bid(PAPER, 40.0, 1 - p, z)
        np.testing.assert_allclose(ask, 1 - bid)
        np.testing.assert_allclose(PAPER.intensity_ask(0.5, p, ask),
                                   PAPER.intensity_bid(0.5, 1 - p, 1 - ask))

    def test_warm_start_from_a_corner_still_converges(self):
        p, z = np.array([0.5, 0.5]), np.array([0.0, 0.0])
        for start in (np.array([0.0, 1.0]), np.array([1e-12, 1 - 1e-12])):
            np.testing.assert_allclose(optimal_bid(PAPER, 40.0, p, z, start),
                                       optimal_bid(PAPER, 40.0, p, z), atol=1e-7)

    def test_min_offset_keeps_a_quote_off_the_mid(self):
        P = ModelParams(min_offset=0.005)
        # a large negative z pushes the bid through the mid without the bound
        free = float(optimal_bid(PAPER, 50.0, np.array([0.5]), np.array([-0.10]))[0])
        self.assertGreater(free, 0.5)
        bound = float(optimal_bid(P, 50.0, np.array([0.5]), np.array([-0.10]))[0])
        self.assertAlmostEqual(bound, 0.495)
        ask = float(optimal_ask(P, 50.0, np.array([0.5]), np.array([-0.10]))[0])
        self.assertAlmostEqual(ask, 0.505)


class TestModelParams(unittest.TestCase):
    def test_rejects_a_limit_the_grid_cannot_hold(self):
        with self.assertRaises(ValueError):
            ModelParams(Q=10.0, delta=3.0)
        with self.assertRaises(ValueError):
            ModelParams(Q=1.0, delta=2.0)

    def test_paper_shapes(self):
        # sec. 4.1 at t = T (tau = 0) and t = 0 (tau = T)
        self.assertAlmostEqual(float(PAPER.k(0.0)), 50.0)
        self.assertAlmostEqual(float(PAPER.k(1.0)), 35.0)
        self.assertAlmostEqual(float(PAPER.activity(0.0, 0.5)), 125.0)
        self.assertAlmostEqual(float(PAPER.activity(1.0, 0.5)), 50.0)
        self.assertAlmostEqual(float(PAPER.sigma_tilde(0.0, 0.5)), 1.0)
        self.assertAlmostEqual(float(PAPER.sigma_tilde(1.0, 0.5)), 0.7)
        self.assertAlmostEqual(float(PAPER.varsigma(1.0, 0.5)), 0.25 * 0.7)
        # the boundary factor vanishes at an uneconomic quote
        self.assertEqual(float(PAPER.intensity_bid(0.5, 0.4, 0.0)), 0.0)
        self.assertEqual(float(PAPER.intensity_ask(0.5, 0.4, 1.0)), 0.0)

    def test_phase_holds_still_further_out_than_T(self):
        P = ModelParams(T=48.0)
        self.assertEqual(float(P.k(48.0)), float(P.k(500.0)))
        self.assertEqual(float(P.activity(48.0, 0.5)), float(P.activity(500.0, 0.5)))


class TestPaperReproduction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.S = solve(PAPER, n_p=100, n_t=500)

    def spread(self, t, p, q=0.0):
        c = self.S.continuous_quotes(1.0 - t, p, q)
        return c["ask"] - c["bid"]

    def skew(self, t, p, q):
        c = self.S.continuous_quotes(1.0 - t, p, q)
        return 0.5 * (c["ask"] + c["bid"]) - p

    def test_figure_1_spreads_at_flat_inventory(self):
        for t, p, paper in ((0.0, 0.5, 0.0568), (0.0, 0.3, 0.0560), (0.0, 0.1, 0.0524),
                            (1.0, 0.5, 0.0442), (1.0, 0.1, 0.0394)):
            self.assertAlmostEqual(self.spread(t, p), paper, delta=3e-4, msg=(t, p))

    def test_figure_2_skews_at_the_start(self):
        for p, q, paper in ((0.4, -50, 0.0065), (0.4, 0, 0.0001), (0.4, 50, -0.0060),
                            (0.2, 0, 0.0008), (0.2, 50, -0.0038), (0.8, -50, 0.0038)):
            self.assertAlmostEqual(self.skew(0.0, p, q), paper, delta=2e-4, msg=(p, q))

    def test_skew_falls_with_inventory_and_has_the_right_sign(self):
        skews = [self.skew(0.5, 0.5, q) for q in (-60, -30, 0, 30, 60)]
        self.assertEqual(skews, sorted(skews, reverse=True))
        self.assertGreater(skews[0], 0)
        self.assertAlmostEqual(skews[2], 0.0, places=6)
        self.assertLess(skews[-1], 0)

    def test_terminal_closed_form(self):
        # at tau = 0, V = -gamma_T q^2 p(1-p), so z and the quotes are explicit
        p, q = 0.3, 20.0
        V = -PAPER.gamma_T * p * (1 - p)
        self.assertAlmostEqual(self.S.value(0.0, p, q), V * q * q, places=3)
        c = self.S.continuous_quotes(0.0, p, q)
        d = PAPER.delta
        self.assertAlmostEqual(c["z_b"], (V * q * q - V * (q + d) ** 2) / d, places=4)
        self.assertAlmostEqual(c["z_a"], (V * q * q - V * (q - d) ** 2) / d, places=4)

    def test_settlement_spread_formula_at_one_half(self):
        # bid = p - z - 1 / (k + nu / (2p)) to first order; the paper's 0.0442
        k = float(PAPER.k(0.0))
        z = PAPER.gamma_T * PAPER.delta * 0.25
        self.assertAlmostEqual(self.spread(1.0, 0.5), 2 * (z + 1.0 / (k + 1.0)), delta=1e-4)

    def test_symmetric_under_swapping_yes_and_no(self):
        for tau, p, q in ((0.4, 0.3, 20.0), (0.9, 0.2, -50.0), (0.1, 0.45, 70.0)):
            self.assertAlmostEqual(self.S.value(tau, p, q), self.S.value(tau, 1 - p, -q), places=4)
            a = self.S.continuous_quotes(tau, p, q)
            b = self.S.continuous_quotes(tau, 1 - p, -q)
            self.assertAlmostEqual(a["bid"], 1 - b["ask"], places=4)

    def test_constrained_side_is_withdrawn_at_the_limit(self):
        top = self.S.continuous_quotes(0.5, 0.5, PAPER.Q)
        self.assertIsNone(top["bid"])
        self.assertIsNotNone(top["ask"])
        bottom = self.S.continuous_quotes(0.5, 0.5, -PAPER.Q)
        self.assertIsNone(bottom["ask"])
        self.assertIsNotNone(bottom["bid"])

    def test_solution_satisfies_the_hjb(self):
        # residual of (HJB) at interior points, by finite differences on the surface
        P, S = PAPER, solve(PAPER, n_p=200, n_t=1000, max_slices=1001)
        d, dt, dp = P.delta, 1e-3, S.dp
        for tau, p, q in ((0.5, 0.5, 0.0), (0.3, 0.4, 30.0), (0.7, 0.2, -20.0), (0.5, 0.6, 50.0)):
            V = S.value
            V_tau = (V(tau + dt, p, q) - V(tau - dt, p, q)) / (2 * dt)
            V_pp = (V(tau, p + dp, q) - 2 * V(tau, p, q) + V(tau, p - dp, q)) / dp ** 2
            s2 = float(P.varsigma(tau, p)) ** 2
            H = 0.0
            for side, step in (("bid", d), ("ask", -d)):
                z = (V(tau, p, q) - V(tau, p, q + step)) / d
                if side == "bid":
                    pi = float(optimal_bid(P, P.k(tau), np.array([p]), np.array([z]))[0])
                    H += d * float(P.intensity_bid(tau, p, pi)) * (p - pi - z)
                else:
                    pi = float(optimal_ask(P, P.k(tau), np.array([p]), np.array([z]))[0])
                    H += d * float(P.intensity_ask(tau, p, pi)) * (pi - p - z)
            # t = T - tau, so -V_t in (HJB) is +dV/dtau
            residual = V_tau - 0.5 * s2 * V_pp + P.gamma * q * q * s2 - H
            scale = abs(H) + abs(P.gamma * q * q * s2) + abs(V_tau)
            self.assertLess(abs(residual), 0.02 * scale, msg=(tau, p, q))

    def test_table_2_risk_reduction(self):
        # paper: baseline 12.47 / 28.11 / 49.37 / 32.41 / 40.50
        #        optimal  12.39 / 10.34 / 15.23 /  4.20 /  9.68
        opt = simulate(self.S, PAPER, n_paths=3000, n_steps=500, seed=7)
        base = simulate(None, PAPER, n_paths=3000, n_steps=500, seed=7, bound_baseline=True)
        self.assertAlmostEqual(opt["mean_pnl"], 12.39, delta=1.0)
        self.assertAlmostEqual(opt["std_pnl"], 10.34, delta=1.0)
        self.assertAlmostEqual(opt["mean_abs_q"], 15.23, delta=1.5)
        self.assertAlmostEqual(opt["es5"], 9.68, delta=1.5)
        self.assertAlmostEqual(base["mean_pnl"], 12.47, delta=2.5)
        self.assertAlmostEqual(base["std_pnl"], 28.11, delta=2.5)
        self.assertAlmostEqual(base["mean_abs_q"], 49.37, delta=3.0)
        self.assertAlmostEqual(base["es5"], 40.50, delta=4.0)


class TestSolverAtKalshiScale(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.S = solve(KALSHI, horizon=88.0, n_p=60, growth=1.1)

    def test_stays_fast_and_converged(self):
        self.assertLess(self.S.stats["steps"], 400)
        self.assertEqual(self.S.stats["unconverged_steps"], 0)
        self.assertEqual(self.S.stats["reached_tau"], 88.0)

    def test_coarse_grid_is_close_to_a_fine_one(self):
        fine = solve(KALSHI, horizon=88.0, n_p=120, step_factor=0.05, growth=1.02)
        for tau, p, q in ((80.0, 0.5, 0.0), (30.0, 0.25, 4.0), (5.0, 0.7, -6.0), (0.5, 0.5, 8.0)):
            a, b = self.S.continuous_quotes(tau, p, q), fine.continuous_quotes(tau, p, q)
            self.assertAlmostEqual(a["bid"], b["bid"], delta=2e-3, msg=(tau, p, q))
            self.assertAlmostEqual(a["ask"], b["ask"], delta=2e-3, msg=(tau, p, q))

    def test_joins_a_one_cent_touch(self):
        # A-S rounded outward and sat a tick behind 1c markets; these quotes do not
        c = self.S.tick_quotes(30.0, 0.505, 0.0)
        self.assertEqual((c["bid"], c["ask"]), (0.50, 0.51))

    def test_long_inventory_sells_cheaper_and_buys_cheaper(self):
        flat, long_ = self.S.tick_quotes(30.0, 0.505, 0.0), self.S.tick_quotes(30.0, 0.505, 6.0)
        self.assertLessEqual(long_["bid"], flat["bid"])
        self.assertLess(long_["z_a"], flat["z_a"])
        self.assertGreater(long_["z_b"], flat["z_b"])

    def test_at_the_limit_only_the_reducing_side_quotes(self):
        c = self.S.tick_quotes(30.0, 0.505, 10.0)
        self.assertIsNone(c["bid"])
        self.assertEqual(c["ask"], 0.51)
        c = self.S.tick_quotes(30.0, 0.505, -10.0)
        self.assertIsNone(c["ask"])
        self.assertEqual(c["bid"], 0.50)

    def test_a_maker_fee_makes_quotes_less_aggressive(self):
        P = ModelParams(**{**KALSHI.__dict__, "maker_fee": 0.0175, "k0": 40.0, "k1": 40.0})
        Q = ModelParams(**{**KALSHI.__dict__, "k0": 40.0, "k1": 40.0})
        with_fee = solve(P, horizon=40.0, n_p=40, growth=1.15).continuous_quotes(10.0, 0.5, 0.0)
        no_fee = solve(Q, horizon=40.0, n_p=40, growth=1.15).continuous_quotes(10.0, 0.5, 0.0)
        self.assertLess(with_fee["bid"], no_fee["bid"])
        self.assertGreater(with_fee["ask"], no_fee["ask"])

    def test_queue_aware_joins_a_one_cent_touch(self):
        c = self.S.queue_quotes(30.0, 0.505, 0.0, {0.50: 40.0, 0.49: 80.0}, {0.49: 40.0, 0.48: 80.0})
        self.assertEqual((c["bid"], c["ask"]), (0.50, 0.51))

    def test_queue_aware_steps_inside_a_wide_queued_market(self):
        # 200 contracts at each side of a 4c market: the paper's Lambda would join them
        book = ({0.48: 200.0}, {0.48: 200.0})
        c = self.S.queue_quotes(30.0, 0.50, 0.0, *book)
        self.assertEqual((c["bid"], c["ask"]), (0.49, 0.51))
        self.assertLess(c["model_bid"], c["bid"] + 1e-9)

    def test_queue_aware_never_crosses_or_goes_through_the_mid(self):
        c = self.S.queue_quotes(30.0, 0.505, 0.0, {0.50: 1.0}, {0.49: 1.0})
        self.assertLess(c["bid"], 0.51)
        self.assertGreater(c["ask"], 0.50)
        for mid, bids, nos in ((0.50, {0.45: 5.0}, {0.45: 5.0}), (0.30, {0.2: 5.0}, {0.6: 5.0})):
            c = self.S.queue_quotes(30.0, mid, 2.0, bids, nos)
            self.assertLessEqual(c["bid"], mid - 0.005 + 1e-9)
            self.assertGreaterEqual(c["ask"], mid + 0.005 - 1e-9)

    def test_queue_aware_respects_the_limit_and_an_empty_side(self):
        c = self.S.queue_quotes(30.0, 0.505, 10.0, {0.50: 40.0}, {0.49: 40.0})
        self.assertIsNone(c["bid"])
        self.assertEqual(c["ask"], 0.51)
        c = self.S.queue_quotes(30.0, 0.505, 0.0, {}, {0.49: 40.0})
        self.assertIsNotNone(c["bid"])

    def test_queue_aware_quotes_still_respond_to_inventory_in_a_wide_market(self):
        P = ModelParams(**{**KALSHI.__dict__, "gamma": 5.0, "gamma_T": 0.2, "k0": 40.0, "k1": 40.0})
        S = solve(P, horizon=88.0, n_p=40, growth=1.15)
        book = ({0.40: 5.0}, {0.40: 5.0})           # 0.40 / 0.60 around a 0.50 mid
        flat = S.queue_quotes(60.0, 0.50, 0.0, *book)
        long_ = S.queue_quotes(60.0, 0.50, 8.0, *book)
        short = S.queue_quotes(60.0, 0.50, -8.0, *book)
        self.assertLess(long_["ask"], flat["ask"])
        self.assertGreater(short["bid"], flat["bid"])
        self.assertLess(long_["bid"], flat["bid"])
        self.assertGreater(short["ask"], flat["ask"])

    def test_stops_once_stationary_and_reuses_the_last_slice(self):
        P = ModelParams(**{**KALSHI.__dict__, "T": 2.0, "A0": 40.0, "A1": 0.0, "sigma1": 0.0})
        S = solve(P, horizon=500.0, n_p=40, growth=1.15)
        self.assertTrue(S.stats["stopped_early"])
        self.assertLess(S.stats["reached_tau"], 500.0)
        at_end = S.continuous_quotes(S.stats["reached_tau"], 0.5, 4.0)
        beyond = S.continuous_quotes(490.0, 0.5, 4.0)
        self.assertEqual(at_end["bid"], beyond["bid"])

    def test_inventory_off_the_grid_interpolates(self):
        a, b, c = (self.S.value(30.0, 0.5, q) for q in (2.0, 3.0, 4.0))
        self.assertAlmostEqual(b, 0.5 * (a + c))
        z_b, z_a = self.S.shadow_costs(30.0, 0.5, 9.0)      # one contract of room left
        self.assertAlmostEqual(z_b, self.S.value(30.0, 0.5, 9.0) - self.S.value(30.0, 0.5, 10.0))
        self.assertIsNotNone(z_a)


class TestEstimators(unittest.TestCase):
    def test_bounce_is_not_volatility(self):
        rng = np.random.default_rng(1)
        efficient = np.cumsum(rng.normal(0, 0.01, 4000))
        noisy = efficient + rng.choice([-0.02, 0.02], 4000)
        d = np.diff(noisy)
        self.assertGreater(np.mean(d * d), 5 * 0.01 ** 2)
        self.assertAlmostEqual(noise_robust_variance(d) / 0.01 ** 2, 1.0, delta=0.3)

    def test_logit_vol_starts_at_the_prior_and_learns(self):
        v = LogitVol(sample_s=60.0, prior_obs=10.0)
        self.assertEqual(v.sigma(0.3), 0.3)
        for i in range(100):             # flat market
            v.add(i * 60.0, 0.5)
        self.assertLess(v.sigma(0.3), 0.15)
        rng = np.random.default_rng(2)
        v = LogitVol(sample_s=60.0, window=500, prior_obs=10.0)
        x = 0.0
        for i in range(500):
            x += rng.normal(0, 0.2 * math.sqrt(60.0 / 3600.0))
            v.add(i * 60.0, 1.0 / (1.0 + math.exp(-x)))
        self.assertAlmostEqual(v.sigma(0.05), 0.2, delta=0.06)

    def test_logit_vol_ignores_sub_sample_flicker(self):
        v = LogitVol(sample_s=120.0, prior_obs=1.0)
        for i in range(200):             # 0.50 / 0.51 flips every 2 seconds, sampled coarsely
            v.add(i * 2.0, 0.50 if i % 2 == 0 else 0.51)
        self.assertLess(len(v.increments), 4)

    def test_flow_rate_is_contracts_over_queue(self):
        f = FlowEstimator(window_h=2.0, prior_rate_weight=0.001)
        for i in range(200):
            f.observe_book(i * 36.0, 0.5, 0.005, 18.0, 18.0)
        for i in range(200):
            f.add_trade(i * 36.0, 0.505, 2.0, "yes")     # 200 contracts/h both sides
        # 100 contracts/h per side through a queue of 18 + our 2: 5 fills/h
        self.assertAlmostEqual(f.fill_rate(7200.0, 1.0, 2.0), 5.0, delta=0.3)

    def test_silent_tape_stays_near_the_prior(self):
        f = FlowEstimator(window_h=2.0)
        f.observe_book(0.0, 0.5, 0.005, 10.0, 10.0)
        self.assertAlmostEqual(f.fill_rate(0.0, 3.0, 2.0), 3.0)
        self.assertAlmostEqual(f.k(0.0, 90.0), 90.0)
        # two silent hours pull the rate down, but not to zero
        rate = f.fill_rate(7200.0, 3.0, 2.0)
        self.assertLess(rate, 3.0)
        self.assertGreater(rate, 0.0)

    def test_k_comes_from_reach_past_the_touch(self):
        f = FlowEstimator(window_h=2.0, prior_k_weight=1.0)
        rng = np.random.default_rng(3)
        f.observe_book(0.0, 0.5, 0.005, 10.0, 10.0)
        for excess in rng.exponential(1.0 / 50.0, 2000):
            f.add_trade(1.0, 0.505 + excess, 1.0, "yes")
        self.assertAlmostEqual(f.k(1.0, 150.0), 50.0, delta=5.0)

    def test_k_stays_in_bounds_and_stale_mids_do_not_count(self):
        f = FlowEstimator(window_h=2.0, prior_k_weight=1.0)
        f.observe_book(0.0, 0.5, 0.005, 10.0, 10.0)
        for _ in range(100):
            f.add_trade(1.0, 0.505, 1.0, "yes")         # never past the touch
        self.assertEqual(f.k(1.0, 150.0), K_MAX)
        for _ in range(100):
            f.add_trade(1.0, 0.95, 1.0, "no")           # wrong side: no reach at all
        self.assertEqual(f.k(1.0, 150.0), K_MAX)
        f = FlowEstimator(window_h=2.0, prior_k_weight=1.0)
        f.observe_book(0.0, 0.5, 0.005, 10.0, 10.0)
        f.add_trade(100.0, 0.95, 1.0, "yes")            # mid 100s old: unknown distance
        self.assertEqual(f.trades[-1][2], None)
        for _ in range(100):
            f.add_trade(1.0, 0.95, 1.0, "yes")
        self.assertEqual(f.k(1.0, 150.0), K_MIN)

    def test_window_forgets_old_flow(self):
        f = FlowEstimator(window_h=1.0)
        f.observe_book(0.0, 0.5, 0.005, 10.0, 10.0)
        f.add_trade(1.0, 0.505, 500.0, "yes")
        busy = f.fill_rate(60.0, 1.0, 2.0)
        self.assertLess(f.fill_rate(7300.0, 1.0, 2.0), busy / 10)

    def test_fit_recovers_known_ramps(self):
        T = 40.0
        taus = (1.0, 3.0, 6.0, 10.0, 16.0, 24.0, 32.0, 38.0)
        table = []
        for tau in taus:
            phase = 1.0 - tau / T
            table.append({
                "tau_mid": tau, "hours": 10.0, "p1p": 0.25, "half_spread": 0.005,
                "sigma": 0.2 + 1.5 * phase ** 3,
                "k": 150.0 + (60.0 - 150.0) * math.expm1(3.0 * phase) / math.expm1(3.0),
                "fill_rate": (4.0 + 300.0 * math.expm1(5.0 * phase) / math.expm1(5.0)) * 0.5
                * math.exp(-(150.0 + (60.0 - 150.0) * math.expm1(3.0 * phase)
                             / math.expm1(3.0)) * 0.005)})
        f = fit_ramps(table, T)
        self.assertEqual(f["eta"], 3.0)
        self.assertAlmostEqual(f["sigma0"], 0.2, delta=0.01)
        self.assertAlmostEqual(f["sigma1"], 1.5, delta=0.05)
        self.assertEqual(f["xi"], 5.0)
        self.assertAlmostEqual(f["A0"], 4.0, delta=0.3)
        self.assertAlmostEqual(f["A1"], 300.0, delta=15.0)
        self.assertAlmostEqual(f["k0"], 150.0, delta=10.0)
        self.assertAlmostEqual(f["k1"], 60.0, delta=10.0)


class TestSeriesCalibration(unittest.TestCase):
    def test_measures_and_fits_a_recorded_series(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        path = os.path.join(d, "rec.db")
        conn = sqlite3.connect(path)
        conn.executescript(SCHEMA)
        iso = "2027-01-15T08:00:00Z"
        close = 1_800_000_000       # the same instant
        conn.execute("INSERT INTO markets (ticker, series_ticker, close_time) VALUES (?,?,?)",
                     ("KXTEST-A", "KXTEST", iso))
        rng = np.random.default_rng(4)
        x, n = 0.0, 0
        start = close - 56 * 3600
        for ts in range(start, close, 60):
            x += rng.normal(0, 0.3 * math.sqrt(60 / 3600))
            if abs(x) > 1.2:        # reflect, to stay inside the quotable band
                x = math.copysign(2.4 - abs(x), x)
            mid = round(1.0 / (1.0 + math.exp(-x)), 2) + 0.005
            bid, ask = round(mid - 0.005, 2), round(mid + 0.005, 2)
            conn.execute("INSERT INTO book_snapshots (ts_ms, market_ticker, source, yes_levels,"
                         " no_levels) VALUES (?,?,?,?,?)",
                         (ts * 1000, "KXTEST-A", "rest", json.dumps([[bid, 30.0]]),
                          json.dumps([[round(1 - ask, 2), 30.0]])))
            for _ in range(int(rng.poisson(0.5))):
                n += 1
                conn.execute("INSERT INTO trades (trade_id, ts_ms, market_ticker, yes_price,"
                             " count, taker_side, source) VALUES (?,?,?,?,?,?,?)",
                             (f"t{n}", (ts + 2) * 1000, "KXTEST-A", ask, 4.0, "yes", "rest"))
        conn.commit()
        conn.close()
        out = fit_series_ramps(path, ["KXTEST", "KXNONE"], stand_down_h=8.0, delta=2.0, T=40.0)
        self.assertNotIn("KXNONE", out["series"])
        fitted = out["series"]["KXTEST"]
        measured = fitted["measured"]
        self.assertGreaterEqual(len(measured), 3)
        for row in measured:
            self.assertAlmostEqual(row["sigma"], 0.3, delta=0.15)
            self.assertEqual(row["k"], K_MAX)           # every order stopped at the touch
            # 30 prints/h of 4 lots on one side is 60 contracts/h/side through 30 + 2
            self.assertAlmostEqual(row["fill_rate"], 60.0 / 32.0, delta=0.5)
        ModelParams(**fitted["params"])                 # a usable parameter set


class TestQuoter(unittest.TestCase):
    def quoter(self, **cfg):
        self.solved = []
        base = {"T": 40.0, "A0": 10.0, "A1": 0.0, "sigma0": 0.2, "n_p": 30, "growth": 1.2}
        return PMMQuoter("KXFOO-1", delta=2, Q=10, cfg={**base, **cfg}, stand_down_h=1.0,
                         logger=logger, on_solve=self.solved.append)

    def test_rejects_sizes_the_inventory_grid_cannot_represent(self):
        with self.assertRaises(ValueError):
            PMMQuoter("X-1", delta=3, Q=10, logger=logger)

    def test_solves_once_then_only_when_aged_or_drifted(self):
        q = self.quoter(resolve_min_s=100.0, resolve_every_s=1000.0)
        q.observe_book(0.0, 0.5, 0.495, 0.505, 20.0, 20.0)
        first = q.quote(0.0, 0.5, 0.0, 30.0)
        self.assertTrue(first["solved"])
        self.assertFalse(q.quote(50.0, 0.5, 0.0, 30.0)["solved"])
        self.assertEqual(len(self.solved), 1)
        self.assertTrue(q.quote(1001.0, 0.5, 0.0, 30.0)["solved"])
        self.assertEqual(len(self.solved), 2)
        self.assertLess(self.solved[0]["tau"], 30.0)    # measured to the stand-down

    def test_drift_in_flow_triggers_a_solve_but_not_before_the_minimum_gap(self):
        q = self.quoter(resolve_min_s=100.0, resolve_every_s=1e6)
        q.observe_book(0.0, 0.5, 0.495, 0.505, 2.0, 2.0)
        q.quote(0.0, 0.5, 0.0, 30.0)
        for i in range(50):
            q.observe_trade(10.0, 0.505, 100.0, "yes")
        self.assertFalse(q.quote(50.0, 0.5, 0.0, 30.0)["solved"])
        self.assertTrue(q.quote(150.0, 0.5, 0.0, 30.0)["solved"])

    def test_allow_solve_defers_a_resolve_but_never_the_first(self):
        q = self.quoter(resolve_every_s=10.0, resolve_min_s=1.0)
        q.observe_book(0.0, 0.5, 0.495, 0.505, 20.0, 20.0)
        self.assertTrue(q.quote(0.0, 0.5, 0.0, 30.0, allow_solve=False)["solved"])
        self.assertFalse(q.quote(50.0, 0.5, 0.0, 30.0, allow_solve=False)["solved"])
        self.assertEqual(len(self.solved), 1)

    def test_survives_a_failed_resolve_with_the_old_surface(self):
        q = self.quoter(resolve_every_s=10.0, resolve_min_s=1.0)
        q.observe_book(0.0, 0.5, 0.495, 0.505, 20.0, 20.0)
        q.quote(0.0, 0.5, 0.0, 30.0)
        import src.pmm_quoter as module
        real, module.solve = module.solve, lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
        try:
            self.assertIsNotNone(q.quote(50.0, 0.5, 0.0, 30.0))
            fresh = self.quoter()
            fresh.observe_book(0.0, 0.5, 0.495, 0.505, 20.0, 20.0)
            self.assertIsNone(fresh.quote(0.0, 0.5, 0.0, 30.0))
        finally:
            module.solve = real

    def test_calibration_sets_the_shape_and_can_only_raise_the_stand_down(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        path = os.path.join(d, "cal.json")
        params = {"T": 40.0, "sigma0": 0.1, "sigma1": 0.5, "sigma2": 0.0, "eta": 3.0, "A0": 3.0,
                  "A1": 20.0, "xi": 2.0, "nu": 1.0, "k0": 150.0, "k1": 100.0, "kappa": 1.5}
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"stand_down_h": 8.0, "series": {"KXFOO": {"params": params}}}, f)
        q = self.quoter(calibration_file=path)
        self.assertTrue(q.calibrated)
        self.assertEqual(q.stand_down_h, 8.0)
        self.assertEqual(q.prior.k0, 150.0)
        other = PMMQuoter("KXBAR-1", 2, 10, {"calibration_file": path}, stand_down_h=1.0, logger=logger)
        self.assertFalse(other.calibrated)
        self.assertEqual(other.stand_down_h, 1.0)

    def test_queue_awareness_can_be_turned_off(self):
        book = ({0.48: 200.0}, {0.48: 200.0})
        q = self.quoter(k0=80.0, k1=80.0)
        q.observe_book(0.0, 0.50, 0.48, 0.52, 200.0, 200.0)
        on = q.quote(0.0, 0.50, 0.0, 30.0, ladders=book)
        self.assertEqual((on["bid"], on["ask"]), (0.49, 0.51))
        q = self.quoter(queue_aware=False)
        q.observe_book(0.0, 0.50, 0.48, 0.52, 200.0, 200.0)
        off = q.quote(0.0, 0.50, 0.0, 30.0, ladders=book)
        self.assertEqual((off["bid"], off["ask"]), (off["model_bid"], off["model_ask"]))

    def test_a_missing_calibration_file_stops_the_start(self):
        with self.assertRaises(ValueError):
            self.quoter(calibration_file=os.path.join(tempfile.gettempdir(), "no_such_file.json"))

    def test_uncalibrated_k_comes_from_the_quoted_spread(self):
        q = self.quoter()
        q.observe_book(0.0, 0.5, 0.45, 0.55, 20.0, 20.0)   # 10c wide: k = 1 / 0.05
        out = q.quote(0.0, 0.5, 0.0, 30.0)
        self.assertEqual(out["k"], K_MIN)
        self.assertLess(out["bid"], 0.47)                  # no reason to sit at the mid
        self.assertLess(out["model_bid"], 0.47)

    def test_maker_fee_rate(self):
        self.assertEqual(maker_fee_rate("quadratic", 1.0), 0.0)
        self.assertAlmostEqual(maker_fee_rate("quadratic_with_maker_fees", 1.0), 0.0175)
        self.assertAlmostEqual(maker_fee_rate("quadratic_with_maker_fees", 0.5), 0.00875)
        self.assertEqual(maker_fee_rate(None, None), 0.0)


STRATEGY = {"gamma": 1.0, "default_k": 40.0, "base_order_size": 2, "max_position": 10,
            "min_spread": 0.02, "min_time_to_resolution_h": 0.5, "max_horizon_h": 24.0,
            "calibration_constant": 0.04, "sigma_floor": 0.01, "k_refresh_every": 5,
            "model": "pmm",
            "pmm": {"gamma": 0.2, "gamma_T": 0.05, "T": 40.0, "A0": 10.0, "A1": 0.0,
                    "sigma0": 0.2, "n_p": 30, "growth": 1.2, "resolve_min_s": 30.0}}


class TestSessionWithModel(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.clock = FakeClock()
        self.ex = FakeExchange(self.clock)
        self.ex.add_market("TIGHT", {0.50: 40, 0.49: 80}, {0.49: 40, 0.48: 80}, close_in_h=48)
        self.ex.add_market("WIDE", {0.40: 5}, {0.50: 5}, close_in_h=48)
        self.path = os.path.join(self.dir, "events.jsonl")
        self.journal = Journal(self.path)
        self.addCleanup(self.journal.close)

    def session(self, specs=None, strategy=None, risk=None):
        cfg = {"environment": "demo", "strategy": strategy or STRATEGY, "risk": risk or {},
               "execution": {}, "session": {"cycle_s": 2.0, "trades_poll_s": 0,
                                            "summary_every_cycles": 1000}}
        specs = specs or [{"ticker": "TIGHT", "liquidity_class": "liquid"},
                          {"ticker": "WIDE", "liquidity_class": "illiquid"}]
        return TradingSession(self.ex, specs, cfg, self.journal, logger,
                              clock=self.clock, sleep=self.clock.sleep)

    def ticks(self, ticker):
        return [e for e in read_journal(self.path) if e["type"] == "tick" and e["ticker"] == ticker]

    def test_quotes_the_touch_in_a_tight_market(self):
        s = self.session()
        s.start()
        s.run_cycle()
        orders = {o["side"]: o for o in self.ex.our_orders("TIGHT")}
        self.assertEqual(orders["bid"]["price"], 0.50)
        self.assertEqual(orders["ask"]["price"], 0.51)
        self.assertEqual(orders["bid"]["remaining"], 2.0)

    def test_no_duplicates_across_cycles_and_clean_shutdown(self):
        s = self.session()
        s.start()
        for _ in range(5):
            s.run_cycle()
            self.clock.advance(2)
        self.assertEqual(len(self.ex.our_orders("TIGHT")), 2)
        self.assertEqual(len(self.ex.our_orders("WIDE")), 2)
        summary = s.shutdown()
        self.assertEqual(summary["orders_left_resting"], 0)
        self.assertEqual(self.ex.our_orders(), [])
        write_report(self.dir)
        with open(os.path.join(self.dir, "report.md"), encoding="utf-8") as f:
            self.assertIn("TIGHT", f.read())

    def test_inventory_moves_the_quotes_and_the_limit_withdraws_a_side(self):
        s = self.session(specs=[{"ticker": "WIDE", "liquidity_class": "illiquid"}])
        s.start()
        s.run_cycle()
        flat = self.ticks("WIDE")[-1]
        self.assertAlmostEqual(flat["reservation"], flat["mid"], places=4)
        before = {o["side"]: o["price"] for o in self.ex.our_orders("WIDE")}

        filled = 0
        while filled < 10:
            self.ex.taker_hits("WIDE", "bid", 2)
            filled += 2
            self.clock.advance(2)
            s.run_cycle()
        tick = self.ticks("WIDE")[-1]
        self.assertEqual(tick["q"], 10.0)
        self.assertLess(tick["reservation"], tick["mid"])
        self.assertEqual(tick["bid_size"], 0)
        after = {o["side"]: o["price"] for o in self.ex.our_orders("WIDE")}
        self.assertNotIn("bid", after)                  # paper fn. 3: no bid at q = Q
        self.assertLessEqual(after["ask"], before["ask"])
        self.assertEqual(s.workers["WIDE"].fill_count, 5)
        # one side quoted leaves the spread fields empty: the report must still build
        s.shutdown()
        write_report(self.dir)

    def test_stands_down_before_close(self):
        self.ex.add_market("LATE", {0.50: 40}, {0.49: 40}, close_in_h=0.25)
        s = self.session(specs=[{"ticker": "LATE", "liquidity_class": "liquid"}])
        s.start()
        s.run_cycle()
        tick = self.ticks("LATE")[-1]
        self.assertEqual(tick["reason"], "near_resolution")
        self.assertEqual(self.ex.our_orders("LATE"), [])

    def test_stands_down_when_only_a_last_trade_is_left_to_price_from(self):
        self.ex.add_market("EMPTY", {}, {}, close_in_h=48, last_price=0.40)
        s = self.session(specs=[{"ticker": "EMPTY", "liquidity_class": "illiquid"}])
        s.start()
        s.run_cycle()
        tick = self.ticks("EMPTY")[-1]
        self.assertEqual(tick["mid_source"], "last_price")
        self.assertEqual(tick["reason"], "no_two_sided_book")
        self.assertEqual(self.ex.our_orders("EMPTY"), [])

    def test_never_more_than_one_resolve_per_cycle(self):
        s = self.session()
        s.start()
        s.run_cycle()                                   # both first solves are unavoidable
        self.assertEqual(len([e for e in read_journal(self.path) if e["type"] == "pmm_solve"]), 2)
        self.clock.advance(4000)                        # both surfaces have now aged out
        s.run_cycle()
        self.assertEqual(len([e for e in read_journal(self.path) if e["type"] == "pmm_solve"]), 3)
        self.clock.advance(2)
        s.run_cycle()
        self.assertEqual(len([e for e in read_journal(self.path) if e["type"] == "pmm_solve"]), 4)

    def test_journals_what_the_report_and_replay_need(self):
        s = self.session()
        s.start()
        s.run_cycle()
        events = list(read_journal(self.path))
        self.assertTrue(any(e["type"] == "pmm_setup" for e in events))
        start = next(e for e in events if e["type"] == "session_start")
        self.assertEqual(start["strategy_effective"]["TIGHT"]["model"], "pmm")
        tick = self.ticks("TIGHT")[-1]
        for field in ("sigma", "sigma_raw", "k", "reservation", "spread", "spread_inv", "spread_liq",
                      "pmm_tau", "pmm_z_b", "pmm_z_a", "pmm_scales", "pmm_fill_rate"):
            self.assertIsNotNone(tick.get(field), field)

    def test_avellaneda_stays_the_default(self):
        strategy = {k: v for k, v in STRATEGY.items() if k not in ("model", "pmm")}
        s = self.session(strategy=strategy)
        s.start()
        s.run_cycle()
        self.assertIsNone(s.workers["TIGHT"].pmm)
        self.assertFalse(any(e["type"] == "pmm_solve" for e in read_journal(self.path)))
        self.assertGreater(self.ticks("TIGHT")[-1]["spread_liq"], 0)


class TestSettlementLookup(unittest.TestCase):
    def lookup(self, responses):
        import requests
        seen = []

        class Reply:
            def __init__(self, body):
                self.body = body

            def json(self):
                return self.body

        def fake_get(url, timeout=None):
            seen.append(url)
            return Reply(responses.get("hist" if "/historical/" in url else "live", {}))
        real, requests.get = requests.get, fake_get
        try:
            return fetch_settle_price("KXFOO-1"), seen
        finally:
            requests.get = real

    def test_a_live_settled_market(self):
        got, seen = self.lookup({"live": {"market": {"status": "settled", "result": "yes"}}})
        self.assertEqual(got, 1.0)
        self.assertEqual(len(seen), 1)

    def test_a_market_that_moved_to_the_historical_listing(self):
        got, seen = self.lookup({"live": {"error": {"code": "not_found"}},
                                 "hist": {"market": {"status": "finalized", "result": "no"}}})
        self.assertEqual(got, 0.0)
        self.assertEqual(len(seen), 2)

    def test_an_open_market_has_no_outcome(self):
        got, _ = self.lookup({"live": {"market": {"status": "active", "result": ""}}})
        self.assertIsNone(got)


class TestReplayWithModel(unittest.TestCase):
    def engine(self):
        # closes two days after the events below
        params = {"base_order_size": 2, "max_position": 10, "min_time_to_resolution_h": 0.5}
        return ShadowEngine("KXFOO-1", "2027-01-17T08:00:00Z", "quadratic", 1.0, params, logger,
                            pmm_config=dict(STRATEGY["pmm"]))

    def test_quotes_and_fills_from_recorded_events(self):
        e = self.engine()
        ts = 1_800_000_000_000
        snap = ([["0.50", "20"]], [["0.49", "20"]])
        e.on_event("snapshot", ts, snap)
        self.assertEqual(e.sim.orders["bid"].price, 0.50)
        self.assertEqual(e.sim.orders["ask"].price, 0.51)
        e.on_event("trade", ts + 1000, {"yes_price": 0.47, "count": 5, "taker_side": "no"})
        self.assertEqual(e.position, 2.0)
        self.assertEqual(e.pmm.solves, 1)

    def test_no_book_no_quotes_in_replay(self):
        e = self.engine()
        ts = 1_800_000_000_000
        e.on_event("trade", ts, {"yes_price": 0.40, "count": 1, "taker_side": "yes"})
        self.assertIsNone(e.sim.orders["bid"])
        self.assertIsNone(e.sim.orders["ask"])

    def test_limit_withdraws_a_side_in_replay_too(self):
        e = self.engine()
        ts = 1_800_000_000_000
        e.position = 10.0
        e.on_event("snapshot", ts, ([["0.50", "20"]], [["0.49", "20"]]))
        self.assertIsNone(e.sim.orders["bid"])
        self.assertIsNotNone(e.sim.orders["ask"])

    def test_a_fee_series_passes_its_fee_to_the_model(self):
        params = {"base_order_size": 2, "max_position": 10}
        e = ShadowEngine("KXFOO-1", None, "quadratic_with_maker_fees", 1.0, params, logger,
                         pmm_config=dict(STRATEGY["pmm"]))
        self.assertAlmostEqual(e.pmm.maker_fee, 0.0175)


if __name__ == "__main__":
    unittest.main()
