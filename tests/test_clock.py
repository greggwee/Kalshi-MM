"""Observation-clock window: fitting, wrap-around, out-of-sample judging."""
import unittest

import numpy as np

from src.clock import (N_BINS, bin_pnl, evaluate, find_window, hour_bin, in_window,
                       split_dates, window_label, window_sums)


def fills(n_dates, toxic=(52 * 60, 54 * 60), per_hour=120, loss=-2.0, gain=0.3, seed=0):
    """One fill every 30s for 6 hours on each date; fills in the toxic part
    of the hour lose, the rest earn."""
    rng = np.random.default_rng(seed)
    ts, mo, dates = [], [], []
    for d in range(n_dates):
        t = d * 86400.0 + np.arange(6 * per_hour) * (3600.0 / per_hour)
        sec = t % 3600
        bad = (sec >= toxic[0]) & (sec < toxic[1])
        ts.append(t)
        mo.append(np.where(bad, loss, gain) + rng.normal(0, 0.5, len(t)))
        dates.append(np.full(len(t), f"26JAN{d + 1:02d}"))
    ts, mo, dates = map(np.concatenate, (ts, mo, dates))
    return ts, mo, np.ones(len(ts)), dates


class TestWindowFitting(unittest.TestCase):
    def test_bins(self):
        self.assertEqual(list(hour_bin(np.array([0.0, 29.9, 30.0, 3599.0, 3600.0]))),
                         [0, 0, 1, 119, 0])
        per_bin = bin_pnl(np.array([10.0, 20.0, 3605.0, 40.0]), np.array([1.0, 2.0, 4.0, np.nan]))
        self.assertEqual(per_bin[0], 7.0)
        self.assertEqual(len(per_bin), N_BINS)

    def test_window_sums_wrap_around_the_hour(self):
        per_bin = np.zeros(N_BINS)
        per_bin[N_BINS - 1], per_bin[0] = -3.0, -2.0
        sums = window_sums(per_bin, 2)
        self.assertEqual(sums[N_BINS - 1], -5.0)
        self.assertEqual(find_window(per_bin, 4), (N_BINS - 1, 2))

    def test_finds_the_losing_minutes(self):
        ts, mo, w, _ = fills(10)
        window = find_window(bin_pnl(ts, mo * w), max_bins=10)
        self.assertEqual(window, (104, 4))                 # :52:00 to :54:00
        self.assertEqual(window_label(window), ":52:00-:54:00")

    def test_window_never_extends_into_profitable_bins(self):
        per_bin = np.full(N_BINS, 1.0)
        per_bin[10] = -5.0
        self.assertEqual(find_window(per_bin, 10), (10, 1))

    def test_no_rule_when_nothing_loses(self):
        self.assertIsNone(find_window(np.full(N_BINS, 0.5), 10))

    def test_in_window_wraps(self):
        m = in_window(np.array([3590.0, 5.0, 31.0, 1800.0]), (N_BINS - 1, 2))
        self.assertEqual(list(m), [True, True, False, False])


class TestEvaluation(unittest.TestCase):
    def test_split_is_by_calendar_date(self):
        dates = np.array(["26FEB01", "26JAN31", "26JAN30", "26FEB02"])
        train, test = split_dates(dates)
        self.assertEqual(list(train), [False, True, True, False])
        self.assertEqual(list(test), [True, False, False, True])

    def test_real_window_holds_out_of_sample(self):
        ts, mo, w, dates = fills(20)
        train, test = split_dates(dates)
        window = find_window(bin_pnl(ts[train], (mo * w)[train]), max_bins=10)
        r = evaluate(ts[test], mo[test], w[test], dates[test], window, n_boot=300)
        self.assertEqual(r["n_dates"], 10)
        self.assertLess(r["inside"]["hi"], 0.0)
        self.assertGreater(r["outside"]["lo"], 0.0)
        self.assertGreater(r["gain_lo"], 0.0)
        # 4 losing bins of 720 fills per date, -2 each: skipping them saves 48 per date
        self.assertAlmostEqual(r["gain_per_date"], 48.0, delta=3.0)
        self.assertEqual(r["placebo_rank"], 1)
        self.assertAlmostEqual(r["share_of_hour"], 4 / 120)

    def test_window_fitted_on_noise_does_not_hold(self):
        ts, mo, w, dates = fills(40, loss=0.3)             # no toxic minutes at all
        mo = mo - 0.3                                       # zero-mean noise
        train, test = split_dates(dates)
        window = find_window(bin_pnl(ts[train], (mo * w)[train]), max_bins=10)
        self.assertIsNotNone(window)                        # in-sample there is always a "worst" run
        r = evaluate(ts[test], mo[test], w[test], dates[test], window, n_boot=300)
        self.assertLess(r["gain_lo"], 0.0)                  # ...but the gain interval spans zero
        self.assertGreater(r["placebo_rank"], 5)


if __name__ == "__main__":
    unittest.main()
