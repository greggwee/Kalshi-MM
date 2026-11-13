"""Does the observation-clock window found on KXHIGHNY transfer to other series?

    python runs/20261112_toxicity/analysis/clock_transfer.py data/markouts_all.npz

The window (:52:00-:57:00) was fitted on KXHIGHNY's first half of dates only,
so every date of every OTHER series is out of sample for it. For each group:
maker P&L inside the window, outside it, and the difference with an interval
from resampling event dates (inside and outside resampled together).
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from src.clock import in_window  # noqa: E402

WINDOW = (104, 10)       # :52:00-:57:00 in 30s bins
CAP = 10.0


def diff(d, m, col, two_sided, n_boot=2000):
    v = d[col][m]
    w = np.minimum(d["size"][m], CAP)
    ok = np.isfinite(v) & ((d["two_sided"][m] > 0) | (not two_sided))
    v, w = v[ok], w[ok]
    dates = d["date"][m][ok]
    inside = in_window(d["ts"][m][ok], WINDOW)
    uniq, inv = np.unique(dates, return_inverse=True)
    k = len(uniq)
    agg = lambda sel: (np.bincount(inv[sel], weights=(v * w)[sel], minlength=k),
                       np.bincount(inv[sel], weights=w[sel], minlength=k))
    ni, di = agg(inside)
    no, do = agg(~inside)
    rng = np.random.default_rng(0)
    pick = rng.integers(0, k, size=(n_boot, k))
    boot = (ni[pick].sum(1) / np.maximum(di[pick].sum(1), 1e-9)
            - no[pick].sum(1) / np.maximum(do[pick].sum(1), 1e-9))
    return (100 * ni.sum() / di.sum(), 100 * no.sum() / do.sum(),
            100 * np.percentile(boot, 2.5), 100 * np.percentile(boot, 97.5), k, int(inside.sum()))


def main(path):
    z = np.load(path)
    d = {k: z[k] for k in z.files}
    atm = (d["touch"] >= 0.10) & (d["touch"] <= 0.90)
    is_low = np.char.startswith(d["series"], "KXLOW")
    hi_win = (d["htc_h"] >= 8) & (d["htc_h"] < 16)
    lo_win = (d["htc_h"] >= 16) & (d["htc_h"] < 24)
    groups = [
        ("KXHIGHNY (fitted here; reference)", (d["series"] == "KXHIGHNY") & hi_win),
        ("other highs pooled, 8-16h", ~is_low & (d["series"] != "KXHIGHNY") & hi_win),
        ("lows pooled, 16-24h", is_low & lo_win),
    ]
    for s in sorted(set(d["series"])):
        if s != "KXHIGHNY":
            groups.append((f"  {s}", (d["series"] == s) & (lo_win if s.startswith("KXLOW") else hi_win)))
    print("window :52:00-:57:00; cents/contract; diff = inside - outside [95% CI by event date]")
    print(f"{'group':36s} {'measure':10s} {'dates':>5s} {'fills in':>8s} {'inside':>7s} {'outside':>8s}  diff CI")
    for label, m in groups:
        for col, two, name in (("mo_300", True, "5 min"), ("mo_1800", True, "30 min"),
                               ("mo_settle", False, "settlement")):
            a, b, lo, hi, k, n = diff(d, m & atm, col, two)
            flag = "  *" if (lo > 0 or hi < 0) else ""
            print(f"{label:36s} {name:10s} {k:5d} {n:8,d} {a:+7.2f} {b:+8.2f}  "
                  f"{a - b:+6.2f} [{lo:+6.2f},{hi:+6.2f}]{flag}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/markouts_all.npz")
