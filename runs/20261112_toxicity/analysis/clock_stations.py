"""Station reporting schedules and what they predict (exploratory).

    python runs/20261112_toxicity/analysis/clock_stations.py data/markouts_all.npz

Central Park (KNYC) reports once an hour at :51. The airport stations report
hourly at :53/:54 and also every five minutes. Predictions:

1. Markets settled at Central Park (KXHIGHNY, KXLOWTNYC) are uninformed for
   most of the hour, so outside the window a maker there should do better
   than in the airport markets.
2. Airport markets should show a five-minute rhythm instead of one hourly lump.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from src.clock import in_window  # noqa: E402
from src.markouts import date_bootstrap  # noqa: E402

WINDOW = (104, 10)
CAP = 10.0


def cell(st):
    return f"{100 * st['mean']:+6.2f} [{100 * st['lo']:+5.2f},{100 * st['hi']:+5.2f}]"


def main(path):
    z = np.load(path)
    d = {k: z[k] for k in z.files}
    keep = (d["touch"] >= 0.10) & (d["touch"] <= 0.90) & (d["htc_h"] > 0)
    d = {k: v[keep] for k, v in d.items()}
    w = np.minimum(d["size"], CAP)
    is_low = np.char.startswith(d["series"], "KXLOW")
    outcome = np.where(is_low, (d["htc_h"] >= 16) & (d["htc_h"] < 24),
                       (d["htc_h"] >= 8) & (d["htc_h"] < 16))
    inside = in_window(d["ts"], WINDOW) & outcome
    clears = d["sweep"] > 0

    print("1. maker settlement P&L with the clock rule (window skipped), by series")
    print(f"   {'series':11s} {'dates':>5s} {'outcome window, all':>21s} {'whole day, all':>21s} "
          f"{'whole day, clears':>21s} {'whole day, 30 min':>21s}")
    for s in sorted(set(d["series"])):
        g = (d["series"] == s) & ~inside
        if len(set(d["date"][g])) < 60:
            continue
        a = date_bootstrap(d["mo_settle"][g & outcome], w[g & outcome], d["date"][g & outcome])
        b = date_bootstrap(d["mo_settle"][g], w[g], d["date"][g])
        c = date_bootstrap(d["mo_settle"][g & clears], w[g & clears], d["date"][g & clears])
        v = np.where(d["two_sided"] > 0, d["mo_1800"], np.nan)
        e = date_bootstrap(v[g], w[g], d["date"][g])
        tag = "  <- Central Park" if s in ("KXHIGHNY", "KXLOWTNYC") else ""
        print(f"   {s:11s} {b['n_dates']:5d} {cell(a)} {cell(b)} {cell(c)} {cell(e)}{tag}")

    print("\n2. five-minute rhythm: maker 5-minute markout by second within each 5-minute block,")
    print("   outcome window, hourly window :50-:59 excluded")
    sec = d["ts"] % 300.0
    minute = (d["ts"] % 3600.0) // 60
    v = np.where(d["two_sided"] > 0, d["mo_300"], np.nan)
    ny = d["series"] == "KXHIGHNY"
    for label, g in (("airport highs", ~is_low & ~ny), ("KXHIGHNY", ny)):
        base = g & outcome & (minute < 50)
        print(f"   {label}")
        for lo in range(0, 300, 30):
            m = base & (sec >= lo) & (sec < lo + 30)
            st = date_bootstrap(v[m], w[m], d["date"][m])
            share = m.sum() / max(1, base.sum())
            print(f"     +{lo:3d}-{lo + 30:3d}s  orders {share:5.1%}  {cell(st)}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/markouts_all.npz")
