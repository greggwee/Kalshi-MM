"""Estimating the Feil-Nendel model's parameters from Kalshi data.

The paper leaves calibration open: its Table 1 is illustrative, and it notes
that order intensities in prediction markets have not been studied empirically.
So every choice here is ours, not the paper's:

  sigma~  realized volatility of log-odds, sampled coarsely so a bid hopping
          between two ticks does not read as diffusion, with the first-order
          autocovariance added back to remove what noise remains.
  k       the distance an aggressive order reaches past the touch, measured from
          the half-spread and fitted as an exponential tail.
  A       contract flow through the touch over (queue ahead + our size): under
          FIFO, flow C through a queue of D takes (D + delta) / C to fill delta.

Two layers use these: fit_series_ramps() reads a recorder database and fits a
series' levels and time-to-close ramps, and the online estimators below rescale
those levels from what a market is doing right now.
"""
import bisect
import json
import logging
import math
import sqlite3
from collections import defaultdict, deque
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np

SECONDS_PER_HOUR = 3600.0
K_MIN = 20.0
K_MAX = 200.0
# hours to the model's terminal time (the bot's stand-down), bucket edges
TAU_EDGES = (0.0, 1.0, 2.0, 4.0, 6.0, 8.0, 12.0, 16.0, 24.0, 36.0, 48.0, 72.0, 96.0)


def logit(p: float) -> float:
    p = min(max(p, 0.01), 0.99)
    return math.log(p / (1.0 - p))


def noise_robust_variance(increments) -> Optional[float]:
    """Per-increment variance with microstructure noise removed: bounce makes
    successive increments negatively correlated, so var + 2 cov recovers the
    efficient price's variance. Sampling error can drive that to zero or below on a
    short window, so it is kept above a tenth of the raw figure."""
    d = np.asarray(increments, dtype=float)
    if d.size < 2:
        return None
    raw = float(np.mean(d * d))
    return max(raw + 2.0 * float(np.mean(d[:-1] * d[1:])), 0.1 * raw)


class LogitVol:
    """Latent volatility per sqrt hour from a stream of mids, shrunk toward a
    prior worth `prior_obs` increments."""

    def __init__(self, sample_s: float = 120.0, window: int = 120, prior_obs: float = 30.0):
        self.sample_s = sample_s
        self.prior_obs = prior_obs
        self.increments: deque = deque(maxlen=window)
        self._last_ts: Optional[float] = None
        self._last_x: Optional[float] = None

    def add(self, ts: float, mid: float):
        x = logit(mid)
        if self._last_ts is None:
            self._last_ts, self._last_x = ts, x
            return
        elapsed = ts - self._last_ts
        if elapsed < self.sample_s:
            return
        # a gap (a pause, a stale feed) is one increment over a longer time
        self.increments.append((x - self._last_x) / math.sqrt(elapsed / self.sample_s))
        self._last_ts, self._last_x = ts, x

    def sigma(self, prior: float) -> float:
        n = len(self.increments)
        var = noise_robust_variance(list(self.increments))
        hours = self.sample_s / SECONDS_PER_HOUR
        if var is None:
            return prior
        per_hour = var / hours
        return math.sqrt((n * per_hour + self.prior_obs * prior * prior) / (n + self.prior_obs))


class FlowEstimator:
    """Taker flow and how far it reaches, over a trailing window. All estimates
    are conjugate posteriors around the caller's prior, so a silent tape stays
    near the prior instead of collapsing to zero."""

    def __init__(self, window_h: float = 2.0, prior_rate_weight: float = 2.0,
                 prior_k_weight: float = 20.0, clock_start: Optional[float] = None):
        self.window_s = window_h * SECONDS_PER_HOUR
        self.prior_rate_weight = prior_rate_weight
        self.prior_k_weight = prior_k_weight
        self.trades: deque = deque()         # (ts, qty, excess distance or None)
        self.depths: deque = deque(maxlen=720)
        self.mids: deque = deque(maxlen=2000)
        self.start = clock_start

    def observe_book(self, ts: float, mid: float, half_spread: float,
                     bid_depth: Optional[float], ask_depth: Optional[float]):
        self.mids.append((ts, mid, half_spread))
        if self.start is None:
            self.start = ts
        for depth in (bid_depth, ask_depth):
            if depth is not None:
                self.depths.append(depth)

    def _mid_at(self, ts: float, max_age_s: float = 15.0) -> Optional[Tuple[float, float]]:
        if not self.mids:
            return None
        i = bisect.bisect_right(self.mids, (ts, float("inf"), float("inf"))) - 1
        if i < 0 or ts - self.mids[i][0] > max_age_s:
            return None
        return self.mids[i][1], self.mids[i][2]

    def seed(self, trades: Iterable[dict], since: float):
        """Tape from before the session: counts toward flow, not toward k (no
        mid history to measure distance from)."""
        for t in trades:
            if t["ts"] >= since:
                self.trades.append((t["ts"], t["qty"], None))
        if self.start is None or since < self.start:
            self.start = since

    def add_trade(self, ts: float, price: float, qty: float, taker_side: Optional[str]):
        excess = None
        at = self._mid_at(ts)
        if at is not None and taker_side in ("yes", "no"):
            mid, half_spread = at
            reach = price - mid if taker_side == "yes" else mid - price
            excess = max(reach - half_spread, 0.0)
        self.trades.append((ts, qty, excess))

    def _trim(self, now: float):
        while self.trades and now - self.trades[0][0] > self.window_s:
            self.trades.popleft()

    def queue_depth(self, default: float = 0.0) -> float:
        return float(np.median(self.depths)) if self.depths else default

    def fill_rate(self, now: float, prior_rate: float, delta: float) -> float:
        """Fills of `delta` contracts per hour, per side, at the touch."""
        self._trim(now)
        hours = min((now - self.start) if self.start is not None else 0.0, self.window_s)
        hours /= SECONDS_PER_HOUR
        queue = self.queue_depth()
        # the tape holds both sides, the rate is per side
        fills = 0.5 * sum(t[1] for t in self.trades) / (queue + delta)
        weight = self.prior_rate_weight
        return (weight + fills) / (weight / max(prior_rate, 1e-6) + hours)

    def k(self, now: float, prior_k: float) -> float:
        """Exponential tail of distance past the touch, around a prior."""
        self._trim(now)
        excess = [t[2] for t in self.trades if t[2] is not None]
        weight = self.prior_k_weight
        posterior = (weight + len(excess)) / (weight / prior_k + sum(excess))
        return min(max(posterior, K_MIN), K_MAX)


def _nonneg_least_squares(basis: List[np.ndarray], y: np.ndarray, w: np.ndarray) -> Tuple[List[float], float]:
    """Weighted least squares over a 2-column basis with non-negative weights."""
    X = np.stack(basis, axis=1) * np.sqrt(w)[:, None]
    t = y * np.sqrt(w)
    best, best_err = None, float("inf")
    candidates = []
    try:
        candidates.append(np.linalg.lstsq(X, t, rcond=None)[0])
    except np.linalg.LinAlgError:
        pass
    for i in range(X.shape[1]):
        c = np.zeros(X.shape[1])
        denom = float(X[:, i] @ X[:, i])
        c[i] = float(X[:, i] @ t) / denom if denom > 0 else 0.0
        candidates.append(c)
    for c in candidates:
        if np.any(c < 0):
            continue
        err = float(np.sum((X @ c - t) ** 2))
        if err < best_err:
            best, best_err = c, err
    if best is None:
        best = np.zeros(X.shape[1])
    return [float(x) for x in best], best_err


def _ramp(phase: np.ndarray, rate: float) -> np.ndarray:
    return np.expm1(rate * phase) / math.expm1(rate)


def fit_ramps(table: List[dict], T: float) -> dict:
    """Fit the paper's time-to-close forms (sec. 4.1) to a bucket table. Levels
    span orders of magnitude from far-out to near-close, so errors are relative:
    otherwise the busiest bucket decides the fit and the quiet ones get nothing."""
    tau = np.array([r["tau_mid"] for r in table])
    hours = np.array([r["hours"] for r in table])
    phase = np.clip(1.0 - tau / T, 0.0, 1.0)
    out: Dict[str, float] = {"T": T, "sigma2": 0.0, "nu": 1.0}

    sig = np.array([r["sigma"] for r in table])
    w = hours / np.maximum(sig, 1e-3) ** 2
    best = None
    for eta in (1.0, 2.0, 3.0, 4.0, 6.0):
        c, err = _nonneg_least_squares([np.ones_like(phase), phase ** eta], sig, w)
        if best is None or err < best[0]:
            best = (err, eta, c)
    out.update(eta=best[1], sigma0=max(best[2][0], 0.02), sigma1=best[2][1])

    # A is measured at the touch, so undo the decay over the half-spread there
    activity = np.array([r["fill_rate"] / math.sqrt(max(r["p1p"], 1e-4))
                         * math.exp(r["k"] * r["half_spread"]) for r in table])
    w = hours / np.maximum(activity, 1e-3) ** 2
    best = None
    for xi in (0.5, 1.0, 2.0, 3.0, 5.0, 8.0):
        c, err = _nonneg_least_squares([np.ones_like(phase), _ramp(phase, xi)], activity, w)
        if best is None or err < best[0]:
            best = (err, xi, c)
    out.update(xi=best[1], A0=max(best[2][0], 0.05), A1=best[2][1])

    ks = np.array([r["k"] for r in table])
    best = None
    for kappa in (0.5, 1.5, 3.0, 5.0):
        for k0 in np.arange(K_MIN, K_MAX + 1, 5.0):
            for k1 in np.arange(K_MIN, K_MAX + 1, 5.0):
                err = float(np.sum(hours * (k0 + (k1 - k0) * _ramp(phase, kappa) - ks) ** 2))
                if best is None or err < best[0]:
                    best = (err, kappa, float(k0), float(k1))
    out.update(kappa=best[1], k0=best[2], k1=best[3])
    return out


def _bucket(tau: float) -> Optional[int]:
    if tau < 0.0 or tau >= TAU_EDGES[-1]:
        return None
    return bisect.bisect_right(TAU_EDGES, tau) - 1


def measure_series(conn: sqlite3.Connection, series: str, stand_down_h: float, delta: float,
                   min_mid: float = 0.10, max_mid: float = 0.90, max_age_s: float = 15.0,
                   vol_sample_s: float = 300.0, logger: Optional[logging.Logger] = None) -> List[dict]:
    """Per time-to-close bucket: volatility, order reach, touch flow and queue
    for one series, over every recorded market in it. Trades printed in the
    same millisecond on the same side are one aggressive order."""
    rows = conn.execute("SELECT ticker, close_time FROM markets WHERE series_ticker = ?",
                        (series,)).fetchall()
    acc = defaultdict(lambda: {"hours": 0.0, "qty": 0.0, "depth": [], "excess": [], "dx": [],
                               "p1p": [], "half": []})
    for ticker, close_time in rows:
        if not close_time:
            continue
        close = datetime.fromisoformat(close_time.replace("Z", "+00:00")).timestamp()
        snaps = []
        for ts, y, n in conn.execute("SELECT ts_ms, yes_levels, no_levels FROM book_snapshots"
                                     " WHERE market_ticker = ? ORDER BY ts_ms", (ticker,)):
            y, n = json.loads(y), json.loads(n)
            if not y or not n:
                continue
            by = max(y, key=lambda l: float(l[0]))
            bn = max(n, key=lambda l: float(l[0]))
            bid, ask = float(by[0]), 1.0 - float(bn[0])
            snaps.append((ts / 1000.0, 0.5 * (bid + ask), 0.5 * (ask - bid),
                          float(by[1]), float(bn[1])))
        if len(snaps) < 2:
            continue
        times = [s[0] for s in snaps]
        for s, nxt in zip(snaps, snaps[1:]):
            gap = nxt[0] - s[0]
            if gap > 180.0 or not (min_mid <= s[1] <= max_mid):
                continue
            b = _bucket((close - s[0]) / SECONDS_PER_HOUR - stand_down_h)
            if b is None:
                continue
            a = acc[b]
            a["hours"] += gap / SECONDS_PER_HOUR
            a["depth"].append(0.5 * (s[3] + s[4]))
            a["p1p"].append(s[1] * (1.0 - s[1]))
            a["half"].append(s[2])
        grid = np.arange(times[0], times[-1], vol_sample_s)
        idx = np.searchsorted(times, grid, side="right") - 1
        prev = None
        for g, i in zip(grid, idx):
            s = snaps[i]
            fresh = g - s[0] < 180.0 and min_mid <= s[1] <= max_mid
            if fresh and prev is not None:
                b = _bucket((close - g) / SECONDS_PER_HOUR - stand_down_h)
                if b is not None:
                    acc[b]["dx"].append(logit(s[1]) - prev)
            prev = logit(s[1]) if fresh else None

        orders = defaultdict(list)
        for ts, price, qty, side in conn.execute(
                "SELECT ts_ms, yes_price, count, taker_side FROM trades WHERE market_ticker = ?"
                " ORDER BY ts_ms", (ticker,)):
            if side in ("yes", "no") and price is not None:
                orders[(ts, side)].append((price, qty))
        for (ts, side), prints in orders.items():
            ts_s = ts / 1000.0
            i = bisect.bisect_right(times, ts_s) - 1
            if i < 0 or ts_s - times[i] > 180.0:
                continue
            s = snaps[i]
            if not (min_mid <= s[1] <= max_mid):
                continue
            b = _bucket((close - ts_s) / SECONDS_PER_HOUR - stand_down_h)
            if b is None:
                continue
            acc[b]["qty"] += sum(q for _, q in prints)
            if ts_s - times[i] <= max_age_s:
                prices = [p for p, _ in prints]
                reach = max(prices) - s[1] if side == "yes" else s[1] - min(prices)
                acc[b]["excess"].append(max(reach - s[2], 0.0))

    table = []
    for b in sorted(acc):
        a = acc[b]
        if a["hours"] < 1.0 or len(a["dx"]) < 10 or not a["excess"]:
            continue
        var = noise_robust_variance(a["dx"])
        excess = np.array(a["excess"])
        depth = float(np.median(a["depth"]))
        contracts = a["qty"] / a["hours"] / 2.0
        table.append({
            "tau_lo": TAU_EDGES[b], "tau_hi": TAU_EDGES[b + 1],
            "tau_mid": 0.5 * (TAU_EDGES[b] + TAU_EDGES[b + 1]),
            "hours": round(a["hours"], 2),
            "sigma": round(math.sqrt(var / (vol_sample_s / SECONDS_PER_HOUR)), 4),
            "orders": len(excess),
            "k": round(min(max(1.0 / max(float(excess.mean()), 1e-4), K_MIN), K_MAX), 1),
            "contracts_per_h_side": round(contracts, 1),
            "queue_depth": round(depth, 1),
            "fill_rate": round(contracts / (depth + delta), 3),
            "p1p": round(float(np.mean(a["p1p"])), 4),
            "half_spread": round(float(np.median(a["half"])), 4),
        })
    if logger:
        logger.info(f"{series}: {len(rows)} markets, {len(table)} usable buckets")
    return table


def fit_series_ramps(db_path: str, series_list: Iterable[str], stand_down_h: float = 2.0,
                     delta: float = 2.0, T: float = 48.0,
                     logger: Optional[logging.Logger] = None) -> dict:
    """Series name -> fitted parameters plus the measurements behind them."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    out = {"stand_down_h": stand_down_h, "delta": delta, "T": T, "series": {}}
    for series in series_list:
        table = measure_series(conn, series, stand_down_h, delta, logger=logger)
        if len(table) < 3:
            if logger:
                logger.warning(f"{series}: too little data to fit ({len(table)} buckets)")
            continue
        out["series"][series] = {"params": fit_ramps(table, T), "measured": table}
    conn.close()
    return out
