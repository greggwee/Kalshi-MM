"""Holdout test of the clock findings on low-temperature series that were
never looked at (committed before their tape was downloaded).

    python runs/20261112_toxicity/analysis/clock_holdout.py data/markouts_holdout.npz

Fixed in advance: window :52:00-:57:00 of every hour; outcome window 16-24h
before close; fill price 0.10-0.90; weight min(size, 10); settlement P&L;
95% intervals from resampling event dates; all series pooled.

H1  Maker settlement P&L is lower for fills inside the window than outside it
    (inside the outcome window).                          pass: interval < 0
H2  Takers inside the window earn money after the taker fee.
                                                          pass: interval > 0
H3  Takers in the rest of the outcome window earn money after the fee.
                                                          pass: interval > 0
H4  A touch maker who skips the window still loses at settlement over the
    whole day.                                            pass: interval < 0
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from src.clock import in_window  # noqa: E402
from src.markouts import date_bootstrap, date_diff  # noqa: E402

WINDOW = (104, 10)
CAP = 10.0


def verdict(lo, hi, want):
    ok = (hi < 0) if want == "<0" else (lo > 0)
    return "PASS" if ok else "not confirmed"


def main(path):
    z = np.load(path)
    d = {k: z[k] for k in z.files}
    keep = (d["touch"] >= 0.10) & (d["touch"] <= 0.90) & (d["htc_h"] > 0)
    d = {k: v[keep] for k, v in d.items()}
    w = np.minimum(d["size"], CAP)
    outcome = (d["htc_h"] >= 16) & (d["htc_h"] < 24)
    inside = in_window(d["ts"], WINDOW) & outcome
    fee = 0.07 * d["price"] * (1.0 - d["price"])
    taker = -d["mo_settle"] - d["side"] * (d["price"] - d["touch"]) - fee
    print(f"series: {', '.join(sorted(set(d['series'])))}")
    print(f"{len(w):,} orders, {len(set(d['date']))} event dates; {inside.sum():,} inside the window")

    r = date_diff(d["mo_settle"], w, d["date"], inside, outcome & ~inside)
    print(f"H1 maker settle inside {100 * r['a']:+.2f}, outside {100 * r['b']:+.2f}, "
          f"diff {100 * r['diff']:+.2f} [{100 * r['lo']:+.2f},{100 * r['hi']:+.2f}]  "
          f"{verdict(r['lo'], r['hi'], '<0')}")
    for name, m, want in (("H2 taker net of fee, inside the window", inside, ">0"),
                          ("H3 taker net of fee, rest of outcome window", outcome & ~inside, ">0")):
        st = date_bootstrap(taker[m], w[m], d["date"][m])
        print(f"{name}: {100 * st['mean']:+.2f} [{100 * st['lo']:+.2f},{100 * st['hi']:+.2f}] "
              f"n={m.sum():,} dates={st['n_dates']}  {verdict(st['lo'], st['hi'], want)}")
    st = date_bootstrap(d["mo_settle"][~inside], w[~inside], d["date"][~inside])
    print(f"H4 maker settle, whole day, skipping the window: {100 * st['mean']:+.2f} "
          f"[{100 * st['lo']:+.2f},{100 * st['hi']:+.2f}] dates={st['n_dates']}  "
          f"{verdict(st['lo'], st['hi'], '<0')}")
    print("per series (not part of the test):")
    for s in sorted(set(d["series"])):
        g = d["series"] == s
        r = date_diff(d["mo_settle"], w, d["date"], g & inside, g & outcome & ~inside)
        st = date_bootstrap(taker[g & inside], w[g & inside], d["date"][g & inside])
        print(f"  {s:11s} maker inside-outside {100 * r['diff']:+6.2f} [{100 * r['lo']:+6.2f},"
              f"{100 * r['hi']:+6.2f}]   taker net inside {100 * st['mean']:+6.2f} "
              f"[{100 * st['lo']:+6.2f},{100 * st['hi']:+6.2f}] n={int((g & inside).sum()):,}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/markouts_holdout.npz")
