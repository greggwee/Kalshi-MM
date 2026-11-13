"""Quick evidence for candidate research projects (read-only).

    python runs/20261112_toxicity/analysis/idea_probes.py data/markouts.npz data/kalshi_recorder.db

A. Favourite-longshot: what the maker earned to settlement by the price of the
   contract the TAKER bought (YES price for taker buys, 1 - price for taker
   sells), all prices, event-date bootstrap.
B. Settlement-source change (Aug 12 2026, NWS -> The Weather Company): maker
   settlement P&L before and after, per series.
C. Ladder coherence: an event's strikes are mutually exclusive and exhaustive,
   so their YES prices must sum to 1. From July recorded books: the sum of
   asks (cost to buy every strike), the sum of bids, and the sum of mids.
D. Do mid changes reverse or continue? Autocorrelation of 1-minute book-mid
   changes (July books).
"""
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from src.markouts import date_bootstrap, event_date  # noqa: E402

CAP = 10.0


def show(st):
    return f"{100 * st['mean']:+6.2f} [{100 * st['lo']:+5.2f},{100 * st['hi']:+5.2f}] n_dates={st['n_dates']}"


def probe_a(d):
    print("\nA. maker settlement P&L (cents/contract) by price of the contract the taker bought")
    cost = np.where(d["side"] > 0, d["touch"], 1.0 - d["touch"])
    w = np.minimum(d["size"], CAP)
    edges = [0.0, 0.03, 0.06, 0.10, 0.20, 0.35, 0.50, 0.65, 0.80, 0.90, 0.94, 0.97, 1.0]
    for series in ("ALL",) + tuple(sorted(set(d["series"]))):
        sm = np.ones(len(w), dtype=bool) if series == "ALL" else d["series"] == series
        if series not in ("ALL", "KXHIGHNY"):
            continue
        print(f"  {series}")
        for lo, hi in zip(edges[:-1], edges[1:]):
            m = sm & (cost > lo) & (cost <= hi) & (d["htc_h"] > 0)
            if m.sum() < 200:
                continue
            st = date_bootstrap(d["mo_settle"][m], w[m], d["date"][m])
            # maker return on capital: the maker's side costs 1 - cost
            ret = st["mean"] / (1.0 - np.average(cost[m], weights=w[m]))
            print(f"    taker pays {lo:.2f}-{hi:.2f}: orders={m.sum():8,d} maker {show(st)}"
                  f"  maker return on stake {100 * ret:+.2f}%")


def probe_b(d):
    print("\nB. maker settlement P&L before/after the Aug 12 2026 settlement-source change (0.10-0.90)")
    w = np.minimum(d["size"], CAP)
    day = np.array([datetime.strptime(x, "%y%b%d").toordinal() for x in d["date"]])
    cut = datetime(2026, 8, 12).toordinal()
    atm = (d["touch"] >= 0.10) & (d["touch"] <= 0.90)
    for series in sorted(set(d["series"])):
        sm = (d["series"] == series) & atm
        pre, post = sm & (day < cut), sm & (day >= cut)
        if pre.sum() < 500 or post.sum() < 500:
            continue
        a = date_bootstrap(d["mo_settle"][pre], w[pre], d["date"][pre])
        b = date_bootstrap(d["mo_settle"][post], w[post], d["date"][post])
        print(f"  {series:11s} before {show(a)}   after {show(b)}")


def probe_cd(db):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    cutoff_ms = 1_790_000_000_000          # July recording only
    events = defaultdict(dict)
    for (ticker,) in conn.execute("SELECT ticker FROM markets WHERE ticker LIKE 'KXHIGH%'"):
        ts, bid, ask = [], [], []
        for ts_ms, yes, no in conn.execute(
                "SELECT ts_ms, yes_levels, no_levels FROM book_snapshots WHERE market_ticker=?"
                " AND ts_ms < ? ORDER BY ts_ms", (ticker, cutoff_ms)):
            y, n = json.loads(yes), json.loads(no)
            ts.append(ts_ms / 1000.0)
            bid.append(float(y[0][0]) if y else 0.0)
            ask.append(1.0 - float(n[0][0]) if n else 1.0)
        if len(ts) > 50:
            events[ticker.rsplit("-", 1)[0]][ticker] = (np.array(ts), np.array(bid), np.array(ask))

    print("\nC. ladder coherence on a 60s grid (events with all strikes recorded)")
    sums_ask, sums_bid, sums_mid, n_events = [], [], [], 0
    ac_num = ac_den = 0.0
    n_ret = 0
    for ev, strikes in events.items():
        if len(strikes) < 6:
            continue
        t0 = max(v[0][0] for v in strikes.values())
        t1 = min(v[0][-1] for v in strikes.values())
        if t1 - t0 < 3600:
            continue
        grid = np.arange(t0, t1, 60.0)
        sa = np.zeros(len(grid))
        sb = np.zeros(len(grid))
        for ts, bid, ask in strikes.values():
            k = np.searchsorted(ts, grid, side="right") - 1
            sa += ask[k]
            sb += bid[k]
            mid = (bid[k] + ask[k]) / 2.0
            live = (mid > 0.10) & (mid < 0.90)
            r = np.diff(mid)
            ok = live[1:-1] & live[2:]
            x, y = r[:-1][ok], r[1:][ok]
            ac_num += float((x * y).sum())
            ac_den += float((x * x).sum())
            n_ret += int(ok.sum())
        n_events += 1
        sums_ask.append(sa)
        sums_bid.append(sb)
        sums_mid.append((sa + sb) / 2.0)
    sa, sb, sm = map(np.concatenate, (sums_ask, sums_bid, sums_mid))
    print(f"  {n_events} events, {len(sa):,} minute-snapshots")
    print(f"  sum of asks: median {np.median(sa):.3f}, p1 {np.percentile(sa, 1):.3f}; "
          f"below 1.00 in {np.mean(sa < 0.9999):.2%} of minutes, below 0.98 in {np.mean(sa < 0.98):.2%}")
    print(f"  sum of bids: median {np.median(sb):.3f}, p99 {np.percentile(sb, 99):.3f}; "
          f"above 1.00 in {np.mean(sb > 1.0001):.2%} of minutes, above 1.02 in {np.mean(sb > 1.02):.2%}")
    print(f"  sum of mids: mean {sm.mean():.4f}, sd {sm.std():.4f}; "
          f"|sum-1| > 0.02 in {np.mean(np.abs(sm - 1) > 0.02):.1%} of minutes")
    print("\nD. first-order autocorrelation of 1-minute book-mid changes (mid in 0.10-0.90)")
    print(f"  {ac_num / ac_den:+.3f} on {n_ret:,} pairs "
          "(negative = moves partly reverse, good for a maker; positive = they continue)")


if __name__ == "__main__":
    z = np.load(sys.argv[1])
    data = {k: z[k] for k in z.files}
    weather = np.char.startswith(data["series"], "KXHIGH") | np.char.startswith(data["series"], "KXLOW")
    data = {k: v[weather] for k, v in data.items()}      # daily temperature series only
    probe_a(data)
    probe_b(data)
    if len(sys.argv) > 2:
        probe_cd(sys.argv[2])
