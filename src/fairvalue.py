"""Observation-driven fair value for the daily-low markets.

The day's low L can only be at or below the lowest temperature seen so far,
so the whole question is how much further it falls:

    D = L - x        x = lowest temperature publicly reported so far today

The model is the empirical distribution of D on past days, conditional on
three things anyone can read off the station feed:

* the hour of the climate day (a 3 am reading says little, a 10 am one a lot),
* how far the current temperature sits above the running low,
* whether the running low comes from a 0.1 C report or a whole-degree one.

A market that pays when lo <= L <= hi is then worth P(lo - x <= D <= hi - x).
No forecast goes in: the model knows only what the station has reported.
"""
from typing import Dict, Optional, Tuple

import numpy as np

from src.obs import c_to_f

GAP_EDGES = np.array([0.5, 1.5, 3.0, 5.0, 8.0, 12.0])   # deg F above the running low
N_GAP = len(GAP_EDGES) + 1
N_HOUR = 24
MIN_CELL = 150
MIN6_SLACK_S = 600
MIN6_MAX_DIP_F = 3.6          # 2 C below the lowest reading so far

NEG_INF, POS_INF = -999, 999


def yes_interval(strike_type: str, floor: Optional[float], cap: Optional[float]) -> Tuple[int, int]:
    """Whole-degree range [lo, hi] of the low for which the market pays YES."""
    if strike_type == "greater":
        return int(floor) + 1, POS_INF
    if strike_type == "less":
        return NEG_INF, int(cap) - 1
    if strike_type == "between":
        return int(floor), int(cap)
    raise ValueError(f"unknown strike type {strike_type!r}")


class DayState:
    """What the station had made public by any moment of one climate day.

    A report becomes known `lat_precise` (hourly and special METARs) or
    `lat_coarse` (5-minute readings) seconds after its observation time.
    A 6-hour minimum group counts only when its six hours lie inside the day.
    """

    def __init__(self, ts, temp_c, precise, min6_c, day_start: int,
                 lat_precise: float = 180.0, lat_coarse: float = 900.0):
        ts = np.asarray(ts, float)
        temp_f = c_to_f(np.asarray(temp_c, float))
        precise = np.asarray(precise, bool)
        min6_f = c_to_f(np.asarray(min6_c, float))
        keep = (ts >= day_start) & (ts < day_start + 86400)
        by_time = np.argsort(ts[keep], kind="stable")
        ts, temp_f, precise, min6_f = (v[keep][by_time] for v in (ts, temp_f, precise, min6_f))
        nominal = np.round(ts / 3600.0) * 3600.0
        # A 6-hour minimum far below every reading so far is a sensor glitch
        # (seen in the archive: 55F reported on a day that never left 63F).
        seen = np.minimum.accumulate(np.where(np.isfinite(temp_f), temp_f, np.inf)) \
            if len(ts) else temp_f
        min6_ok = np.isfinite(min6_f) & (nominal - 6 * 3600 >= day_start - MIN6_SLACK_S) \
            & (min6_f >= seen - MIN6_MAX_DIP_F)
        avail = ts + np.where(precise, lat_precise, lat_coarse)
        order = np.argsort(avail, kind="stable")
        self.day_start = day_start
        self.avail = avail[order]
        temp_f, precise = temp_f[order], precise[order]
        min6_f = np.where(min6_ok, min6_f, np.inf)[order]
        fine = np.where(precise & np.isfinite(temp_f), temp_f, np.inf)
        fine = np.minimum(fine, min6_f)
        coarse = np.where(~precise & np.isfinite(temp_f), temp_f, np.inf)
        self.low_fine = np.minimum.accumulate(fine) if len(fine) else fine
        self.low_coarse = np.minimum.accumulate(coarse) if len(coarse) else coarse
        # latest temperature known at each step (carry the last finite reading)
        idx = np.where(np.isfinite(temp_f), np.arange(len(temp_f)), -1)
        idx = np.maximum.accumulate(idx) if len(idx) else idx
        self.cur = np.where(idx >= 0, temp_f[np.maximum(idx, 0)], np.nan) if len(idx) else temp_f

    def at(self, t) -> Dict[str, np.ndarray]:
        """State at times `t`: running low x, gap of current temp above it,
        whether x rests on a whole-degree reading, hour of the climate day.
        x is nan before the first report of the day is public."""
        t = np.asarray(t, float)
        k = np.searchsorted(self.avail, t, side="right") - 1
        ok = k >= 0
        kk = np.maximum(k, 0)
        if len(self.avail) == 0:
            nan = np.full(t.shape, np.nan)
            return {"x": nan, "gap": nan, "coarse": np.zeros(t.shape, bool),
                    "hour": (t - self.day_start) / 3600.0}
        fine, coarse = self.low_fine[kk], self.low_coarse[kk]
        x = np.minimum(fine, coarse)
        x = np.where(ok & np.isfinite(x), x, np.nan)
        return {"x": x, "gap": self.cur[kk] - x, "coarse": coarse < fine - 0.05,
                "hour": (t - self.day_start) / 3600.0}


def _cell(hour, gap, coarse):
    h = np.clip(np.floor(hour).astype(int), 0, N_HOUR - 1)
    g = np.searchsorted(GAP_EDGES, np.nan_to_num(gap, nan=0.0), side="right")
    return (h * N_GAP + g) * 2 + np.asarray(coarse, int), h


class DropModel:
    """Empirical distribution of D = L - x per (hour, gap, precision) cell,
    falling back to the hour alone when a cell has under MIN_CELL days-samples."""

    def __init__(self):
        self.cells: Dict[int, np.ndarray] = {}
        self.hours: Dict[int, np.ndarray] = {}

    def fit(self, hour, gap, coarse, d) -> "DropModel":
        ok = np.isfinite(d) & np.isfinite(gap)
        hour, gap, coarse, d = hour[ok], gap[ok], np.asarray(coarse)[ok], d[ok]
        cell, h = _cell(hour, gap, coarse)
        self.cells = {int(c): np.sort(d[cell == c]) for c in np.unique(cell)}
        self.hours = {int(c): np.sort(d[h == c]) for c in np.unique(h)}
        return self

    def prob(self, hour, gap, coarse, x, lo, hi) -> np.ndarray:
        """P(lo <= L <= hi) for each row. L is a whole number, so the event is
        lo - 0.5 < L < hi + 0.5, i.e. D in (lo - 0.5 - x, hi + 0.5 - x)."""
        hour, gap, x = (np.asarray(v, float) for v in (hour, gap, x))
        lo, hi = np.asarray(lo, float), np.asarray(hi, float)
        out = np.full(x.shape, np.nan)
        cell, h = _cell(hour, gap, coarse)
        valid = np.isfinite(x) & np.isfinite(gap)
        for c in np.unique(cell[valid]):
            m = valid & (cell == c)
            s = self.cells.get(int(c))
            if s is None or len(s) < MIN_CELL:
                for hh in np.unique(h[m]):
                    mm = m & (h == hh)
                    out[mm] = self._interval(self.hours.get(int(hh)), x[mm], lo[mm], hi[mm])
            else:
                out[m] = self._interval(s, x[m], lo[m], hi[m])
        return out

    @staticmethod
    def _interval(s, x, lo, hi):
        if s is None or len(s) == 0:
            return np.full(x.shape, np.nan)
        a = np.searchsorted(s, lo - 0.5 - x, side="right")
        b = np.searchsorted(s, hi + 0.5 - x, side="left")
        return (b - a + 0.5) / (len(s) + 1.0)     # never exactly 0 or 1


def taker_fee(price):
    """Kalshi taker fee per contract, in dollars (before rounding up to the cent)."""
    price = np.asarray(price, float)
    return 0.07 * price * (1.0 - price)


def _logit(p):
    p = np.clip(np.asarray(p, float), 0.003, 0.997)
    return np.log(p / (1.0 - p))


HOUR_GROUPS = (0, 4, 8, 12, 18, 24)


class Combiner:
    """Blend the traded price with the station model.

    One logistic regression per block of the climate day:

        logit P(yes) = a + b * logit(price) + c * logit(station model)

    With c = 0 the station model adds nothing to the price; the data sets c
    separately for each block. The side of the order is deliberately left
    out: a term for it would hand our own order the average edge of the
    informed takers, which we would not have.
    """

    def __init__(self):
        self.coef: Dict[int, np.ndarray] = {}

    @staticmethod
    def _group(hour):
        return np.clip(np.searchsorted(HOUR_GROUPS, hour, side="right") - 1, 0, len(HOUR_GROUPS) - 2)

    @staticmethod
    def _design(price, fv):
        """fv=None fits the price alone (the baseline the station model must beat)."""
        if fv is None:
            return _logit(price).reshape(-1, 1)
        return np.column_stack([_logit(price), _logit(fv)])

    def fit(self, price, fv, hour, y, weight) -> "Combiner":
        from sklearn.linear_model import LogisticRegression
        grp = self._group(np.asarray(hour, float))
        X = self._design(price, fv)
        for g in np.unique(grp):
            m = grp == g
            if m.sum() < 500 or len(np.unique(y[m])) < 2:
                continue
            lr = LogisticRegression(C=10.0, max_iter=500).fit(X[m], y[m], sample_weight=weight[m])
            self.coef[int(g)] = np.concatenate([lr.intercept_, lr.coef_[0]])
        return self

    def predict(self, price, fv, hour) -> np.ndarray:
        grp = self._group(np.asarray(hour, float))
        X = self._design(price, fv)
        out = np.full(len(X), np.nan)
        for g, c in self.coef.items():
            m = grp == g
            out[m] = 1.0 / (1.0 + np.exp(-(c[0] + X[m] @ c[1:])))
        return out
