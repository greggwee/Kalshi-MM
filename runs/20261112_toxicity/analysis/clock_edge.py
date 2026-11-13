"""Is the observation-clock effect an edge WE can capture?

    python runs/20261112_toxicity/analysis/clock_edge.py data/markouts_all.npz

The clock window (:52:00-:57:00, fitted on KXHIGHNY's first half of dates) and
the outcome windows (8-16h before close for highs, 16-24h for lows) are fixed
in advance. Four questions:

A. Back of the queue. We join the touch behind everyone else, so we are only
   filled when an order clears the level. Proxy: orders that printed at more
   than one price (they exhausted the best level and went on). What do those
   fills earn inside and outside the window? If they lose even outside it,
   avoiding the window does not make a late joiner profitable.
B. The whole day. Maker P&L per fill over ALL hours with and without the
   rule, for every fill and for level-clearing fills only.
C. Drift. After the window, do prices keep moving in the direction the window
   traded? Compare later fills whose taker went WITH the window's net flow to
   fills whose taker went AGAINST it. If the market under-reacts, the maker on
   the "against" side earns more: a reason to quote one side after a burst.
D. Stability: the inside-minus-outside gap by month, by distance of the price
   from 0.5, and by taker side.

Cents per contract for a maker at the touch, weight = min(size, 10), fill
price 0.10-0.90, 95% intervals from resampling event dates.
"""
import os
import sys
from datetime import datetime

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from src.clock import in_window  # noqa: E402
from src.markouts import date_bootstrap, date_diff  # noqa: E402

WINDOW = (104, 10)          # :52:00-:57:00 in 30s bins
WINDOW_START_S = 52 * 60
WINDOW_LEN_S = 300
CAP = 10.0
MEASURES = (("mo_300", True, "5 min"), ("mo_1800", True, "30 min"), ("mo_settle", False, "settle"))


def cell(st):
    if st["n_dates"] == 0 or not np.isfinite(st["mean"]):
        return f"{'-':>21s}"
    return f"{100 * st['mean']:+6.2f} [{100 * st['lo']:+5.2f},{100 * st['hi']:+5.2f}]"


def dcell(r):
    if not np.isfinite(r["diff"]):
        return f"{'-':>22s}"
    star = "*" if (r["lo"] > 0 or r["hi"] < 0) else " "
    return f"{100 * r['diff']:+6.2f} [{100 * r['lo']:+5.2f},{100 * r['hi']:+5.2f}]{star}"


def values(d, col, two_sided):
    return np.where(d["two_sided"] > 0, d[col], np.nan) if two_sided else d[col]


def main(path):
    z = np.load(path)
    d = {k: z[k] for k in z.files}
    keep = (d["touch"] >= 0.10) & (d["touch"] <= 0.90) & (d["htc_h"] > 0)
    d = {k: v[keep] for k, v in d.items()}
    w = np.minimum(d["size"], CAP)
    is_low = np.char.startswith(d["series"], "KXLOW")
    ny = d["series"] == "KXHIGHNY"
    outcome = np.where(is_low, (d["htc_h"] >= 16) & (d["htc_h"] < 24),
                       (d["htc_h"] >= 8) & (d["htc_h"] < 16))
    inside = in_window(d["ts"], WINDOW) & outcome       # the fills the rule skips
    clears = d["sweep"] > 0                              # order walked past the best level
    groups = (("KXHIGHNY", ny), ("other highs", ~is_low & ~ny), ("lows", is_low))

    print("A. outcome window only: every fill vs fills from level-clearing orders")
    print(f"   {'group':12s} {'fills':16s} {'measure':7s} {'n in':>7s} {'inside':>21s} "
          f"{'outside':>21s} {'inside-outside':>22s}")
    for label, g in groups:
        for name, sel in (("every order", np.ones(len(w), bool)), ("clears level", clears)):
            base = g & outcome & sel
            for col, two, mname in MEASURES:
                v = values(d, col, two)
                a = date_bootstrap(v[base & inside], w[base & inside], d["date"][base & inside])
                b = date_bootstrap(v[base & ~inside], w[base & ~inside], d["date"][base & ~inside])
                r = date_diff(v, w, d["date"], base & inside, base & ~inside)
                print(f"   {label:12s} {name:16s} {mname:7s} {r['n_a']:7,d} {cell(a)} {cell(b)} {dcell(r)}")
        share = (g & outcome & clears).sum() / max(1, (g & outcome).sum())
        print(f"   {label}: {share:.0%} of orders in the outcome window clear the level")

    print("\nB. all hours of the day: maker P&L per fill without the rule and with it")
    print(f"   {'group':12s} {'fills':16s} {'measure':7s} {'dates':>5s} {'no rule':>21s} "
          f"{'with rule':>21s} {'fills dropped':>13s}")
    for label, g in groups:
        for name, sel in (("every order", np.ones(len(w), bool)), ("clears level", clears)):
            base = g & sel
            for col, two, mname in MEASURES:
                v = values(d, col, two)
                a = date_bootstrap(v[base], w[base], d["date"][base])
                b = date_bootstrap(v[base & ~inside], w[base & ~inside], d["date"][base & ~inside])
                drop = (base & inside).sum() / max(1, base.sum())
                print(f"   {label:12s} {name:16s} {mname:7s} {a['n_dates']:5d} {cell(a)} {cell(b)} {drop:13.1%}")

    print("\nC. drift after the window (outcome window; hours whose window traded)")
    # an hour-cycle starts at :52:00; phase = seconds since then
    cycle = np.floor((d["ts"] - WINDOW_START_S) / 3600.0)
    phase = (d["ts"] - WINDOW_START_S) % 3600.0
    _, tk = np.unique(d["ticker"], return_inverse=True)
    key = tk.astype(np.int64) * 10_000_000 + (cycle - cycle.min()).astype(np.int64)
    _, kinv = np.unique(key, return_inverse=True)
    in_win = phase < WINDOW_LEN_S
    signed = np.bincount(kinv, weights=np.where(in_win, d["side"] * d["size"], 0.0))
    total = np.bincount(kinv, weights=np.where(in_win, d["size"], 0.0))
    net, vol = signed[kinv], total[kinv]
    with np.errstate(invalid="ignore", divide="ignore"):
        imbalance = np.where(vol > 0, np.abs(net) / vol, 0.0)
    later = ~in_win & outcome & (vol > 0)
    aligned = later & (np.sign(net) == d["side"])
    opposed = later & (np.sign(net) == -d["side"])
    print("   maker P&L on later fills where the taker traded WITH the window's net flow, "
          "AGAINST it, and against-minus-with")
    print(f"   {'group':12s} {'when':22s} {'window flow':16s} {'measure':7s} {'n with':>7s} "
          f"{'with':>21s} {'against':>21s} {'against-with':>22s}")
    for label, g in groups:
        for wname, wsel in (("next 10 min (:57-:07)", phase < 900), ("rest of hour (:07-:52)", phase >= 900)):
            for fname, fsel in (("any", np.ones(len(w), bool)),
                                ("one-sided >=80%", imbalance >= 0.8)):
                for col, two, mname in MEASURES:
                    v = values(d, col, two)
                    a_m, o_m = g & wsel & fsel & aligned, g & wsel & fsel & opposed
                    a = date_bootstrap(v[a_m], w[a_m], d["date"][a_m])
                    o = date_bootstrap(v[o_m], w[o_m], d["date"][o_m])
                    r = date_diff(v, w, d["date"], o_m, a_m)
                    print(f"   {label:12s} {wname:22s} {fname:16s} {mname:7s} {r['n_b']:7,d} "
                          f"{cell(a)} {cell(o)} {dcell(r)}")

    print("\nE. the taker's side, to settlement, net of the taker fee (0.07 * p * (1 - p) per contract,")
    print("   unrounded; the taker pays the order's average price, not the touch)")
    fee = 0.07 * d["price"] * (1.0 - d["price"])
    taker = -d["mo_settle"] - d["side"] * (d["price"] - d["touch"]) - fee
    print(f"   {'group':12s} {'which takers':46s} {'orders':>8s} {'dates':>5s} {'net taker P&L':>21s} {'fee':>5s}")
    for label, g in groups:
        rows = (("inside the window", g & inside),
                ("next 10 min, with the window's flow", g & aligned & (phase < 900)),
                ("next 10 min, with a >=80% one-sided window", g & aligned & (phase < 900) & (imbalance >= 0.8)),
                ("next 10 min, against the window's flow", g & opposed & (phase < 900)),
                ("rest of hour, with the window's flow", g & aligned & (phase >= 900)),
                ("any other fill in the outcome window", g & outcome & ~inside))
        for name, m in rows:
            st = date_bootstrap(taker[m], w[m], d["date"][m])
            avg_fee = 100 * np.average(fee[m], weights=w[m]) if m.any() else float("nan")
            print(f"   {label:12s} {name:46s} {m.sum():8,d} {st['n_dates']:5d} {cell(st)} {avg_fee:5.2f}")

    print("\nD. stability of inside-minus-outside in the outcome window")
    month = np.array([datetime.strptime(x, "%y%b%d").strftime("%Y-%m") for x in d["date"]])
    for label, g, col, two in (("KXHIGHNY 5 min", ny, "mo_300", True),
                               ("lows settlement", is_low, "mo_settle", False)):
        v = values(d, col, two)
        print(f"   {label} by month")
        for mth in sorted(set(month[g])):
            base = g & outcome & (month == mth)
            r = date_diff(v, w, d["date"], base & inside, base & ~inside)
            print(f"     {mth}  dates={r['n_dates']:3d} n in={r['n_a']:6,d}  inside {100 * r['a']:+6.2f}  "
                  f"outside {100 * r['b']:+6.2f}  diff {dcell(r)}")
        print(f"   {label} by distance of the fill price from 0.50")
        for lo, hi in ((0.0, 0.1), (0.1, 0.2), (0.2, 0.3), (0.3, 0.41)):
            base = g & outcome & (d["dist_half"] >= lo) & (d["dist_half"] < hi)
            r = date_diff(v, w, d["date"], base & inside, base & ~inside)
            print(f"     |p-0.5| {lo:.1f}-{hi:.1f}  n in={r['n_a']:6,d}  inside {100 * r['a']:+6.2f}  "
                  f"outside {100 * r['b']:+6.2f}  diff {dcell(r)}")
        print(f"   {label} by taker side")
        for sname, sv in (("taker buys YES", 1.0), ("taker sells YES", -1.0)):
            base = g & outcome & (d["side"] == sv)
            r = date_diff(v, w, d["date"], base & inside, base & ~inside)
            print(f"     {sname:16s} n in={r['n_a']:6,d}  inside {100 * r['a']:+6.2f}  "
                  f"outside {100 * r['b']:+6.2f}  diff {dcell(r)}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/markouts_all.npz")
