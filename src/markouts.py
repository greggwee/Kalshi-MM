"""Per-print maker markouts and toxicity features from the public trade tape.

Every aggressive order on the tape has a maker on the other side. This module
turns a recorder-schema database (trades + markets, see src/storage.py) into
one row per aggressive order with

  * what the maker earned on it: the edge at the fill, the markout at fixed
    horizons, and the markout to settlement;
  * what was knowable just before it: time to close, hour, size, price,
    recent flow, recent price movement, and the tape's own estimate of the
    spread.

Features use only prints strictly before the order, so a model fitted on them
can be used live.

The tape mid
------------
Most of the history has no order books, so the reference price is built from
the tape: the last taker-buy print is an ask proxy, the last taker-sell print
a bid proxy, and the mid is their average. When one side is stale
(`max_side_age_s`) or the two are crossed (the market moved between them) the
mid falls back to the most recent print, which is biased toward that print's
side by about half a spread; `two_sided` marks rows whose pre-trade AND
horizon mids both come from fresh prints on both sides. In thin books prefer
`two_sided` rows or the settlement markout, which needs no mid at all.

Sign convention: `side` is +1 when the taker bought YES (the maker sold),
-1 when the taker sold YES. Maker P&L per contract at a reference price m is
`side * (fill - m)`, where the fill is the TOUCH price: the best level the
aggressive order consumed, i.e. what a small maker resting at the touch got.
An order that walks the book pays deeper makers more; `price` keeps its VWAP.
"""
import sqlite3
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np

HORIZONS_S = (10, 60, 300, 1800)
FEATURE_WINDOW_S = 300.0
MAX_SIDE_AGE_S = 900.0

FEATURES = (
    "htc_h",            # hours from the order to market close
    "utc_hour",         # hour of day, UTC
    "log_size",         # log1p(contracts in the aggressive order)
    "n_prints",         # prints the order was filled in (levels / makers hit)
    "sweep",            # price walked by the order, dollars
    "dist_half",        # |price - 0.5|
    "spread",           # tape spread proxy before the order (nan if one-sided)
    "edge",             # side * (price - pre-trade mid): what the maker was paid
    "absmove_300",      # |pre-trade mid now - pre-trade mid 300s ago|
    "flow_align_300",   # signed-volume imbalance of the last 300s, in this order's direction
    "log_vol_300",      # log1p(contracts traded in the last 300s)
    "log_dt_prev",      # log1p(seconds since the previous order)
    "run_60",           # log1p(same-direction contracts in the last 60s)
)


# What the incoming order looked like is only known once it has filled you.
# A quoting decision can use everything else: these are per (market, side)
# and computable before the order arrives.
ORDER_FEATURES = ("log_size", "n_prints", "sweep")
QUOTE_FEATURES = tuple(f for f in FEATURES if f not in ORDER_FEATURES)


def _iso_to_s(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def aggregate_orders(ts_ms: np.ndarray, price: np.ndarray, count: np.ndarray,
                     side: np.ndarray) -> Dict[str, np.ndarray]:
    """Collapse prints into aggressive orders: consecutive prints that share a
    millisecond and a side are one order walking the book (the same rule the
    replay uses). Input must be sorted by ts_ms."""
    n = len(ts_ms)
    if n == 0:
        z = np.zeros(0)
        return {"ts": z, "side": z, "size": z, "price": z, "touch": z,
                "n_prints": z, "sweep": z}
    new = np.ones(n, dtype=bool)
    new[1:] = (ts_ms[1:] != ts_ms[:-1]) | (side[1:] != side[:-1])
    starts = np.flatnonzero(new)
    size = np.add.reduceat(count, starts)
    notional = np.add.reduceat(count * price, starts)
    hi = np.maximum.reduceat(price, starts)
    lo = np.minimum.reduceat(price, starts)
    with np.errstate(invalid="ignore", divide="ignore"):
        vwap = np.where(size > 0, notional / size, price[starts])
    return {
        "ts": ts_ms[starts] / 1000.0,
        "side": side[starts].astype(float),
        "size": size,
        "price": vwap,
        # the best level the order consumed: what a maker resting at the touch
        # was filled at (a sweep's deeper prints paid deeper makers more)
        "touch": np.where(side[starts] > 0, lo, hi),
        "n_prints": np.diff(np.append(starts, n)).astype(float),
        "sweep": hi - lo,
    }


class TapeMid:
    """Mid proxy from the last taker-buy and last taker-sell prints."""

    def __init__(self, ts: np.ndarray, price: np.ndarray, side: np.ndarray,
                 max_side_age_s: float = MAX_SIDE_AGE_S):
        self.ts = ts
        self.price = price
        self.max_age = max_side_age_s
        n = len(ts)
        idx = np.arange(n)
        # index of the latest buy / sell print at or before each print
        self._last_buy = np.maximum.accumulate(np.where(side > 0, idx, -1))
        self._last_sell = np.maximum.accumulate(np.where(side < 0, idx, -1))

    def at(self, t: np.ndarray, inclusive: bool = True):
        """(mid, spread, two_sided) at times `t`, using prints with ts <= t
        (inclusive) or ts < t."""
        t = np.asarray(t, dtype=float)
        k = np.searchsorted(self.ts, t, side="right" if inclusive else "left") - 1
        mid = np.full(t.shape, np.nan)
        spread = np.full(t.shape, np.nan)
        two = np.zeros(t.shape, dtype=bool)
        ok = k >= 0
        if not ok.any():
            return mid, spread, two
        kk = np.where(ok, k, 0)
        ib, isell = self._last_buy[kk], self._last_sell[kk]
        last = self.price[kk]
        ask = np.where(ib >= 0, self.price[np.maximum(ib, 0)], np.nan)
        bid = np.where(isell >= 0, self.price[np.maximum(isell, 0)], np.nan)
        age_b = np.where(ib >= 0, t - self.ts[np.maximum(ib, 0)], np.inf)
        age_s = np.where(isell >= 0, t - self.ts[np.maximum(isell, 0)], np.inf)
        good = ok & (age_b <= self.max_age) & (age_s <= self.max_age) & (ask >= bid)
        mid = np.where(good, (ask + bid) / 2.0, np.where(ok, last, np.nan))
        spread = np.where(good, ask - bid, np.nan)
        return mid, spread, good


def _window_sum(cum: np.ndarray, ts: np.ndarray, t: np.ndarray, window: float) -> np.ndarray:
    """Sum of a per-order quantity over orders with t - window <= ts < t."""
    hi = np.searchsorted(ts, t, side="left")
    lo = np.searchsorted(ts, t - window, side="left")
    return cum[hi] - cum[lo]


def market_rows(ts_ms: Sequence[float], yes_price: Sequence[float], count: Sequence[float],
                taker_side: Sequence[str], close_s: float, settle: Optional[float],
                horizons: Iterable[int] = HORIZONS_S) -> Dict[str, np.ndarray]:
    """One row per aggressive order of one market. `taker_side` is 'yes'/'no'
    per print; prints with an unknown aggressor or price are dropped."""
    ts_ms = np.asarray(ts_ms, dtype=float)
    price = np.asarray(yes_price, dtype=float)
    count = np.asarray(count, dtype=float)
    side = np.array([1.0 if s == "yes" else -1.0 if s == "no" else 0.0 for s in taker_side])
    keep = (side != 0) & np.isfinite(price) & np.isfinite(count) & (count > 0)
    ts_ms, price, count, side = ts_ms[keep], price[keep], count[keep], side[keep]
    order = np.argsort(ts_ms, kind="stable")
    ts_ms, price, count, side = ts_ms[order], price[order], count[order], side[order]

    o = aggregate_orders(ts_ms, price, count, side)
    t, s, size, p = o["ts"], o["side"], o["size"], o["touch"]
    n = len(t)
    out: Dict[str, np.ndarray] = {"ts": t, "side": s, "size": size, "price": o["price"],
                                  "touch": p}
    if n == 0:
        for name in FEATURES + ("two_sided", "mo_settle") + tuple(f"mo_{h}" for h in horizons):
            out[name] = np.zeros(0)
        return out

    mid = TapeMid(ts_ms / 1000.0, price, side)
    pre_mid, pre_spread, pre_two = mid.at(t, inclusive=False)
    have_pre = np.isfinite(pre_mid)

    out["htc_h"] = (close_s - t) / 3600.0
    out["utc_hour"] = np.floor((t % 86400.0) / 3600.0)
    out["log_size"] = np.log1p(size)
    out["n_prints"] = o["n_prints"]
    out["sweep"] = o["sweep"]
    out["dist_half"] = np.abs(p - 0.5)
    out["spread"] = pre_spread
    out["edge"] = np.where(have_pre, s * (p - pre_mid), np.nan)

    past_mid, _, _ = mid.at(t - FEATURE_WINDOW_S, inclusive=False)
    out["absmove_300"] = np.where(have_pre & np.isfinite(past_mid),
                                  np.abs(pre_mid - past_mid), np.nan)
    cum_signed = np.concatenate([[0.0], np.cumsum(s * size)])
    cum_abs = np.concatenate([[0.0], np.cumsum(size)])
    signed = _window_sum(cum_signed, t, t, FEATURE_WINDOW_S)
    total = _window_sum(cum_abs, t, t, FEATURE_WINDOW_S)
    with np.errstate(invalid="ignore", divide="ignore"):
        out["flow_align_300"] = np.where(total > 0, s * signed / total, 0.0)
    out["log_vol_300"] = np.log1p(total)
    dt = np.empty(n)
    dt[0] = np.nan
    dt[1:] = np.diff(t)
    out["log_dt_prev"] = np.log1p(dt)
    signed_60 = _window_sum(cum_signed, t, t, 60.0)
    total_60 = _window_sum(cum_abs, t, t, 60.0)
    out["run_60"] = np.log1p(np.maximum(0.0, (total_60 + s * signed_60) / 2.0))

    two = pre_two.copy()
    for h in horizons:
        m, _, good = mid.at(t + h, inclusive=True)
        alive = t + h <= close_s
        out[f"mo_{h}"] = np.where(alive & np.isfinite(m), s * (p - m), np.nan)
        two &= good | ~alive
    out["two_sided"] = two.astype(float)
    out["mo_settle"] = (s * (p - settle) if settle is not None
                        else np.full(n, np.nan))
    return out


def event_date(ticker: str) -> str:
    """'KXHIGHNY-26NOV13-T57' -> '26NOV13' (the unit of independence)."""
    parts = ticker.split("-")
    return parts[1] if len(parts) > 1 else ""


def build_dataset(conn: sqlite3.Connection, series: Optional[List[str]] = None,
                  horizons: Iterable[int] = HORIZONS_S, min_orders: int = 1,
                  settled_only: bool = True) -> Dict[str, np.ndarray]:
    """Rows for every market in the database (optionally restricted to some
    series). Adds `series`, `date` and `ticker` string columns."""
    horizons = tuple(horizons)
    where = ["close_time IS NOT NULL"]
    if settled_only:
        where.append("result IN ('yes','no')")
    markets = conn.execute(
        f"SELECT ticker, close_time, result FROM markets WHERE {' AND '.join(where)}"
    ).fetchall()
    chunks: List[Dict[str, np.ndarray]] = []
    for ticker, close_iso, result in markets:
        ser = ticker.split("-")[0]
        if series and ser not in series:
            continue
        rows = conn.execute(
            "SELECT ts_ms, yes_price, count, taker_side FROM trades"
            " WHERE market_ticker = ? ORDER BY ts_ms", (ticker,)).fetchall()
        if not rows:
            continue
        ts_ms, yp, cnt, taker = zip(*rows)
        settle = 1.0 if result == "yes" else 0.0 if result == "no" else None
        r = market_rows(ts_ms, [np.nan if v is None else v for v in yp],
                        [np.nan if v is None else v for v in cnt], taker,
                        _iso_to_s(close_iso), settle, horizons)
        n = len(r["ts"])
        if n < min_orders:
            continue
        r["series"] = np.full(n, ser)
        r["date"] = np.full(n, event_date(ticker))
        r["ticker"] = np.full(n, ticker)
        chunks.append(r)
    if not chunks:
        return {}
    return {k: np.concatenate([c[k] for c in chunks]) for k in chunks[0]}


# --------------------------------------------------------------------------
# Statistics with the event date as the unit of independence
# --------------------------------------------------------------------------

def by_date_mean(values: np.ndarray, weights: np.ndarray, dates: np.ndarray):
    """Per-date (sum of w*v, sum of w), dropping nan values."""
    ok = np.isfinite(values) & np.isfinite(weights)
    uniq, inv = np.unique(dates[ok], return_inverse=True)
    num = np.bincount(inv, weights=(values * weights)[ok], minlength=len(uniq))
    den = np.bincount(inv, weights=weights[ok], minlength=len(uniq))
    return uniq, num, den


def date_bootstrap(values: np.ndarray, weights: np.ndarray, dates: np.ndarray,
                   n_boot: int = 2000, seed: int = 0) -> dict:
    """Weighted mean of `values` with a confidence interval from resampling
    whole event dates (strikes and prints of one date are not independent)."""
    uniq, num, den = by_date_mean(values, weights, dates)
    k = len(uniq)
    if k == 0 or den.sum() <= 0:
        return {"mean": float("nan"), "lo": float("nan"), "hi": float("nan"),
                "n_dates": 0, "weight": 0.0}
    rng = np.random.default_rng(seed)
    pick = rng.integers(0, k, size=(n_boot, k))
    means = num[pick].sum(axis=1) / np.maximum(den[pick].sum(axis=1), 1e-12)
    return {"mean": float(num.sum() / den.sum()),
            "lo": float(np.percentile(means, 2.5)),
            "hi": float(np.percentile(means, 97.5)),
            "n_dates": int(k), "weight": float(den.sum())}


def date_order(dates: Iterable[str]) -> List[str]:
    """Event-date codes ('26NOV13') in calendar order."""
    def key(d):
        try:
            return datetime.strptime(d, "%y%b%d")
        except ValueError:
            return datetime.max
    return sorted(set(dates), key=key)
