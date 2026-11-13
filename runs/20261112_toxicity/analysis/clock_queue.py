"""Queue position and the clock rule, from recorded books.

    python runs/20261112_toxicity/analysis/clock_queue.py data/kalshi_recorder.db [--since-ms N]

A maker resting at the touch with a share q of the level ahead of them is
reached by an aggressive order only if the order takes more than q of the
level. For every order: level size = the recorded touch size on the side the
order hit (latest book snapshot before it, same price, at most 60s old), and
consumed = contracts the order took at that price / level size.

Reports, per queue position q, the maker's P&L on the orders that reach it,
inside and outside the :52:00-:57:00 window, in the outcome window (8-16h
before close for highs, 16-24h for lows) and over the whole day.

Read-only. Cents per contract, weight = min(contracts at the touch, 10),
fill price 0.10-0.90, 95% intervals from resampling event dates.
"""
import json
import os
import sqlite3
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from src.clock import in_window  # noqa: E402
from src.markouts import _iso_to_s, date_bootstrap, event_date, market_rows  # noqa: E402

WINDOW = (104, 10)
CAP = 10.0
MAX_BOOK_AGE_S = 60.0
QUEUE = (0.0, 0.25, 0.5, 0.75, 0.999)


def touch_series(conn, ticker, since_ms):
    """Per snapshot: ts, best YES bid (price, size), best YES ask (price, size)."""
    rows = []
    for ts_ms, yes, no in conn.execute(
            "SELECT ts_ms, yes_levels, no_levels FROM book_snapshots WHERE market_ticker = ?"
            " AND ts_ms >= ? ORDER BY ts_ms", (ticker, since_ms)):
        y, n = json.loads(yes), json.loads(no)
        rows.append((ts_ms / 1000.0,
                     float(y[0][0]) if y else np.nan, float(y[0][1]) if y else np.nan,
                     1.0 - float(n[0][0]) if n else np.nan, float(n[0][1]) if n else np.nan))
    return np.array(rows) if rows else np.zeros((0, 5))


def main(db, since_ms=0, until_ms=10 ** 15):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    cols = {}
    for ticker, close_iso, result in conn.execute(
            "SELECT ticker, close_time, result FROM markets WHERE close_time IS NOT NULL"
            " AND (ticker LIKE 'KXHIGH%' OR ticker LIKE 'KXLOW%')").fetchall():
        tr = conn.execute("SELECT ts_ms, yes_price, count, taker_side FROM trades WHERE"
                          " market_ticker = ? AND ts_ms >= ? AND ts_ms < ? ORDER BY ts_ms",
                          (ticker, since_ms, until_ms)).fetchall()
        book = touch_series(conn, ticker, since_ms)
        if len(tr) < 5 or len(book) < 5:
            continue
        ts_ms, yp, cnt, taker = zip(*tr)
        settle = 1.0 if result == "yes" else 0.0 if result == "no" else None
        r = market_rows(ts_ms, [np.nan if v is None else v for v in yp],
                        [np.nan if v is None else v for v in cnt], taker,
                        _iso_to_s(close_iso), settle)
        t, s, p = r["ts"], r["side"], r["touch"]
        k = np.searchsorted(book[:, 0], t, side="left") - 1
        ok = k >= 0
        kk = np.where(ok, k, 0)
        # a taker buying YES hits the ask; a taker selling YES hits the bid
        lvl_price = np.where(s > 0, book[kk, 3], book[kk, 1])
        lvl_size = np.where(s > 0, book[kk, 4], book[kk, 2])
        fresh = ok & (t - book[kk, 0] <= MAX_BOOK_AGE_S) & (np.abs(lvl_price - p) < 1e-9) \
            & (lvl_size > 0)
        with np.errstate(invalid="ignore", divide="ignore"):
            r["consumed"] = np.where(fresh, np.minimum(1.0, r["touch_size"] / lvl_size), np.nan)
        r["level_size"] = np.where(fresh, lvl_size, np.nan)
        n = len(t)
        r["is_low"] = np.full(n, ticker.startswith("KXLOW"))
        r["date"] = np.full(n, event_date(ticker))
        for key, v in r.items():
            cols.setdefault(key, []).append(v)
    if not cols:
        sys.exit("no markets with both books and trades in that range")
    d = {k: np.concatenate(v) for k, v in cols.items()}
    keep = (d["touch"] >= 0.10) & (d["touch"] <= 0.90) & (d["htc_h"] > 0)
    matched = keep & np.isfinite(d["consumed"])
    print(f"{keep.sum():,} orders at 0.10-0.90 on {len(set(d['date'][keep]))} event dates; "
          f"{matched.sum():,} ({matched.sum() / max(1, keep.sum()):.0%}) matched to a fresh book at the same price")
    d = {k: v[matched] for k, v in d.items()}
    w = np.minimum(d["touch_size"], CAP)
    outcome = np.where(d["is_low"], (d["htc_h"] >= 16) & (d["htc_h"] < 24),
                       (d["htc_h"] >= 8) & (d["htc_h"] < 16))
    inside = in_window(d["ts"], WINDOW) & outcome
    print(f"median level size {np.median(d['level_size']):.0f} contracts, "
          f"median order takes {np.median(d['consumed']):.0%} of it; "
          f"orders taking the whole level: {np.mean(d['consumed'] >= 0.999):.0%}")

    def cell(m, col, two):
        v = np.where(d["two_sided"] > 0, d[col], np.nan) if two else d[col]
        st = date_bootstrap(v[m], w[m], d["date"][m])
        if st["n_dates"] == 0:
            return f"{'-':>21s}"
        return f"{100 * st['mean']:+6.2f} [{100 * st['lo']:+5.2f},{100 * st['hi']:+5.2f}]"

    for title, scope in (("outcome window", outcome), ("whole day", np.ones(len(w), bool))):
        for gname, g in (("highs", ~d["is_low"]), ("lows", d["is_low"])):
            if not (g & scope).any():
                continue
            print(f"\n{gname}, {title}: maker P&L on the orders that reach a maker with share q "
                  f"of the level ahead")
            print(f"  {'q ahead':8s} {'reached by':>10s} {'measure':7s} {'inside window':>21s} "
                  f"{'outside window':>21s} {'outside, n':>10s}")
            for q in QUEUE:
                reach = g & scope & (d["consumed"] > q)
                share = reach.sum() / max(1, (g & scope).sum())
                for col, two, mname in (("mo_300", True, "5 min"), ("mo_1800", True, "30 min"),
                                        ("mo_settle", False, "settle")):
                    label = "front" if q == 0 else "back" if q > 0.99 else f"{q:.0%}"
                    print(f"  {label:8s} {share:10.0%} {mname:7s} {cell(reach & inside, col, two)} "
                          f"{cell(reach & ~inside, col, two)} {(reach & ~inside).sum():10,d}")


if __name__ == "__main__":
    args = sys.argv[1:]
    db = args[0] if args and not args[0].startswith("--") else "data/kalshi_recorder.db"
    since = int(args[args.index("--since-ms") + 1]) if "--since-ms" in args else 0
    until = int(args[args.index("--until-ms") + 1]) if "--until-ms" in args else 10 ** 15
    main(db, since, until)
