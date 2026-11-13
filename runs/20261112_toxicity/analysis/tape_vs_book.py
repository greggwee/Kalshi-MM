"""How good is the tape mid? July 2026 has both recorded books (~34s cadence)
and the tape, so mark the same prints against both.

    python runs/20261112_toxicity/analysis/tape_vs_book.py data/kalshi_recorder.db

Read-only. Prints maker cents/contract, weight = min(size, 10), prints with a
touch price in 0.10-0.90, per series and pooled, with event-date bootstrap CIs.
"""
import json
import os
import sqlite3
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from src.markouts import HORIZONS_S, _iso_to_s, date_bootstrap, event_date, market_rows  # noqa: E402

MAX_BOOK_AGE_S = 90.0
CAP = 10.0


def book_mids(conn, ticker):
    ts, mid = [], []
    for ts_ms, yes, no in conn.execute(
            "SELECT ts_ms, yes_levels, no_levels FROM book_snapshots"
            " WHERE market_ticker = ? ORDER BY ts_ms", (ticker,)):
        y, n = json.loads(yes), json.loads(no)
        if not y or not n:
            continue
        bid, ask = float(y[0][0]), 1.0 - float(n[0][0])
        if ask <= bid:
            continue
        ts.append(ts_ms / 1000.0)
        mid.append((bid + ask) / 2.0)
    return np.array(ts), np.array(mid)


def lookup(bt, bm, t, inclusive):
    k = np.searchsorted(bt, t, side="right" if inclusive else "left") - 1
    ok = k >= 0
    kk = np.where(ok, k, 0)
    fresh = ok & (t - bt[kk] <= MAX_BOOK_AGE_S)
    return np.where(fresh, bm[kk], np.nan)


def main(db):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    cols = {}
    for ticker, close_iso in conn.execute(
            "SELECT ticker, close_time FROM markets WHERE close_time IS NOT NULL").fetchall():
        if not ticker.startswith("KXHIGH"):
            continue
        tr = conn.execute("SELECT ts_ms, yes_price, count, taker_side FROM trades"
                          " WHERE market_ticker = ? ORDER BY ts_ms", (ticker,)).fetchall()
        bt, bm = book_mids(conn, ticker)
        if len(tr) < 5 or len(bt) < 5:
            continue
        ts_ms, yp, cnt, taker = zip(*tr)
        r = market_rows(ts_ms, [np.nan if v is None else v for v in yp],
                        [np.nan if v is None else v for v in cnt], taker,
                        _iso_to_s(close_iso), None)
        t, s, p = r["ts"], r["side"], r["touch"]
        r["b_edge"] = s * (p - lookup(bt, bm, t, inclusive=False))
        for h in HORIZONS_S:
            r[f"b_mo_{h}"] = np.where(t + h <= _iso_to_s(close_iso),
                                      s * (p - lookup(bt, bm, t + h, inclusive=True)), np.nan)
        n = len(t)
        r["series"] = np.full(n, ticker.split("-")[0])
        r["date"] = np.full(n, event_date(ticker))
        for k, v in r.items():
            cols.setdefault(k, []).append(v)
    d = {k: np.concatenate(v) for k, v in cols.items()}
    keep = (d["touch"] >= 0.10) & (d["touch"] <= 0.90)
    d = {k: v[keep] for k, v in d.items()}
    w = np.minimum(d["size"], CAP)
    names = ["edge"] + [f"mo_{h}" for h in HORIZONS_S]

    def line(label, m, prefix, two_sided_only):
        cells = []
        for c in names:
            v = d[prefix + c].copy()
            if two_sided_only:
                v = np.where(d["two_sided"] > 0, v, np.nan)
            # same rows for tape and book: both references must exist
            v = np.where(np.isfinite(d[c]) & np.isfinite(d["b_" + c]), v, np.nan)
            st = date_bootstrap(v[m], w[m], d["date"][m])
            cells.append(f"{100 * st['mean']:+6.2f} [{100 * st['lo']:+5.2f},{100 * st['hi']:+5.2f}]")
        print(f"{label:28s} " + " ".join(cells))

    print(f"{len(d['ts']):,} orders, {len(set(d['date']))} event dates, "
          f"two-sided tape mid on {d['two_sided'].mean():.0%}")
    print(f"{'':28s} " + " ".join(f"{c:>22s}" for c in names))
    for s in ["ALL"] + sorted(set(d["series"])):
        m = np.ones(len(w), dtype=bool) if s == "ALL" else d["series"] == s
        for two in (True, False):
            tag = "two-sided rows" if two else "all rows"
            line(f"{s} tape  ({tag})", m, "", two)
            line(f"{s} book  ({tag})", m, "b_", two)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/kalshi_recorder.db")
