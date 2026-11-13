"""The observation clock: find the part of each hour in which resting quotes
lose money, on one set of event dates, and judge a "no quotes then" rule on
another.

A weather station reports on a fixed minute of every hour. On the day the
outcome is being decided, each report can settle a strike, and whoever reads
it first trades against quotes that have not moved yet. The rule fitted here
is the simplest possible: one contiguous window of the hour, the same every
hour, per series.

Fitting (train dates only)
    Bin fills by second of the hour. The window is the contiguous run of
    bins, at most `max_window_s` long and allowed to wrap past :00, whose
    fills had the most negative total maker P&L. If no run lost money there
    is no rule.

Judging (test dates only)
    * markout of fills inside the window against fills outside it;
    * what skipping the window is worth per event date, with an interval from
      resampling dates;
    * a placebo: the same-length window at every other position of the hour.
      If the fitted window is real, few or none of them lose as much.
"""
from typing import Dict, Optional, Tuple

import numpy as np

from src.markouts import date_bootstrap, date_order

BIN_S = 30
N_BINS = 3600 // BIN_S


def hour_bin(ts: np.ndarray, bin_s: int = BIN_S) -> np.ndarray:
    return ((np.asarray(ts) % 3600.0) // bin_s).astype(int)


def bin_pnl(ts: np.ndarray, pnl: np.ndarray, bin_s: int = BIN_S) -> np.ndarray:
    """Total P&L per second-of-hour bin; nan P&L is ignored."""
    ok = np.isfinite(pnl)
    return np.bincount(hour_bin(ts[ok], bin_s), weights=pnl[ok], minlength=3600 // bin_s)


def window_sums(per_bin: np.ndarray, length: int) -> np.ndarray:
    """Sum over the circular window of `length` bins starting at each bin."""
    n = len(per_bin)
    doubled = np.concatenate([per_bin, per_bin])
    c = np.concatenate([[0.0], np.cumsum(doubled)])
    return c[length:length + n] - c[:n]


def find_window(per_bin: np.ndarray, max_bins: int) -> Optional[Tuple[int, int]]:
    """(start_bin, n_bins) of the circular window with the most negative
    total, or None when nothing loses money."""
    best, best_sum = None, 0.0
    for length in range(1, max_bins + 1):
        sums = window_sums(per_bin, length)
        k = int(np.argmin(sums))
        if sums[k] < best_sum - 1e-12:
            best, best_sum = (k, length), float(sums[k])
    return best


def in_window(ts: np.ndarray, window: Tuple[int, int], bin_s: int = BIN_S) -> np.ndarray:
    start, length = window
    n = 3600 // bin_s
    return ((hour_bin(ts, bin_s) - start) % n) < length


def window_label(window: Tuple[int, int], bin_s: int = BIN_S) -> str:
    start, length = window
    a, b = start * bin_s, ((start + length) * bin_s) % 3600
    return f":{a // 60:02d}:{a % 60:02d}-:{b // 60:02d}:{b % 60:02d}"


def split_dates(dates: np.ndarray, train_share: float = 0.5):
    """Boolean masks (train, test): the earlier `train_share` of event dates
    and the rest, in calendar order."""
    order = date_order(dates)
    cut = max(1, int(round(len(order) * train_share)))
    early = set(order[:cut])
    train = np.array([x in early for x in dates])
    return train, ~train


def evaluate(ts: np.ndarray, mo: np.ndarray, w: np.ndarray, dates: np.ndarray,
             window: Tuple[int, int], n_boot: int = 2000, seed: int = 0) -> Dict:
    """How the window did on these (test) fills."""
    ok = np.isfinite(mo)
    ts, mo, w, dates = ts[ok], mo[ok], w[ok], dates[ok]
    inside = in_window(ts, window)
    pnl = mo * w
    uniq, inv = np.unique(dates, return_inverse=True)
    lost = np.bincount(inv, weights=np.where(inside, pnl, 0.0), minlength=len(uniq))
    total = np.bincount(inv, weights=pnl, minlength=len(uniq))
    rng = np.random.default_rng(seed)
    pick = rng.integers(0, len(uniq), size=(n_boot, len(uniq)))
    gain = -lost[pick].mean(axis=1)
    per_bin = bin_pnl(ts, pnl)
    placebo = window_sums(per_bin, window[1])
    return {
        "n_dates": int(len(uniq)),
        "share_of_fills": float(inside.mean()) if len(inside) else float("nan"),
        "share_of_hour": window[1] / N_BINS,
        "inside": date_bootstrap(mo[inside], w[inside], dates[inside], n_boot, seed),
        "outside": date_bootstrap(mo[~inside], w[~inside], dates[~inside], n_boot, seed),
        "all": date_bootstrap(mo, w, dates, n_boot, seed),
        # P&L per event date with the rule minus without it (same units as mo * w)
        "gain_per_date": float(-lost.mean()),
        "gain_lo": float(np.percentile(gain, 2.5)),
        "gain_hi": float(np.percentile(gain, 97.5)),
        "pnl_per_date": float(total.mean()),
        # how many of the 120 same-length windows lost at least as much
        "placebo_rank": int((placebo <= placebo[window[0]] + 1e-12).sum()),
        "placebo_n": int(len(placebo)),
    }
