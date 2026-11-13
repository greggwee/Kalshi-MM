"""Adverse-selection research on the public tape (read-only).

    python toxicity.py build --db data/kalshi_backfill.db --out data/markouts.npz
    python toxicity.py curves  --data data/markouts.npz
    python toxicity.py buckets --data data/markouts.npz --series KXHIGHNY
    python toxicity.py clock   --data data/markouts.npz
    python toxicity.py model   --data data/markouts.npz --series KXHIGHNY

build    one row per aggressive order: maker markouts + pre-trade features
         (src/markouts.py).
curves   maker edge at the fill and markout at 10s..30min and to settlement,
         per series.
buckets  markouts by hours-to-close and hour of day, split into periods, so a
         rule chosen on one period can be judged on another.
clock    the observation clock: per series, find the part of each hour whose
         fills lose money on the first half of the event dates, and judge a
         no-quote rule for it on the second half (src/clock.py).
model    walk-forward toxicity forecast: fit on earlier event dates, score
         later ones, report realized markout by predicted decile and the
         value of a no-quote filter. By default it uses only features known
         before the order arrives (--features quote).

Every figure is in cents per contract from the maker's side, weighted by
min(order size, --cap) contracts (what a small maker resting at the touch
would have been filled on), with a 95% interval from resampling whole event
dates. The number of event dates is printed next to every figure.
"""
import argparse
from datetime import datetime
import sqlite3
import sys

import numpy as np

from src.markouts import (FEATURES, HORIZONS_S, QUOTE_FEATURES, build_dataset,
                          date_bootstrap, date_order)

MO_COLS = ("edge",) + tuple(f"mo_{h}" for h in HORIZONS_S) + ("mo_settle",)
HTC_BUCKETS = [(0, 2), (2, 4), (4, 8), (8, 16), (16, 24), (24, 36), (36, 1e9)]


def load(path, series=None, atm=None):
    z = np.load(path, allow_pickle=False)
    d = {k: z[k] for k in z.files}
    mask = np.ones(len(d["ts"]), dtype=bool)
    if series:
        mask &= np.isin(d["series"], series)
    if atm:
        mask &= (d["touch"] >= atm[0]) & (d["touch"] <= atm[1])
    return {k: v[mask] for k, v in d.items()}


def subset(d, mask):
    return {k: v[mask] for k, v in d.items()}


def weights(d, cap):
    return np.minimum(d["size"], cap)


def fmt(stat):
    if stat["n_dates"] == 0:
        return f"{'-':>22s}"
    return f"{100 * stat['mean']:+6.2f} [{100 * stat['lo']:+5.2f},{100 * stat['hi']:+5.2f}]"


def stat(d, col, cap, mid_based=True):
    v = d[col]
    if mid_based and col != "mo_settle":
        v = np.where(d["two_sided"] > 0, v, np.nan)
    return date_bootstrap(v, weights(d, cap), d["date"])


def period_masks(d, split):
    """Split rows into periods at the given event dates (calendar order)."""
    order = date_order(d["date"])
    rank = {x: i for i, x in enumerate(order)}
    r = np.array([rank[x] for x in d["date"]])
    days = [datetime.strptime(x, "%y%b%d") for x in order]
    # a split date that has no prints starts the period at the next date that does
    cuts = sorted({sum(day < datetime.strptime(s, "%y%b%d") for day in days) for s in split})
    cuts = [0] + [c for c in cuts if 0 < c < len(order)] + [len(order)]
    out = []
    for a, b in zip(cuts[:-1], cuts[1:]):
        if b > a:
            out.append((f"{order[a]}..{order[b - 1]}", (r >= a) & (r < b)))
    return out


# --------------------------------------------------------------------------

def cmd_build(args):
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    series = args.series.split(",") if args.series else None
    d = build_dataset(conn, series=series)
    if not d:
        sys.exit("no settled markets with trades")
    np.savez_compressed(args.out, **d)
    dates = date_order(d["date"])
    print(f"{len(d['ts']):,} aggressive orders, {len(set(d['ticker'])):,} markets, "
          f"{len(dates)} event dates ({dates[0]}..{dates[-1]}) -> {args.out}")
    for s in sorted(set(d["series"])):
        m = d["series"] == s
        print(f"  {s:12s} orders={m.sum():9,d} dates={len(set(d['date'][m])):4d} "
              f"two_sided={d['two_sided'][m].mean():.0%}")


def cmd_curves(args):
    d = load(args.data, args.series.split(",") if args.series else None, args.atm)
    print(f"maker cents/contract, weight=min(size,{args.cap:g}), ATM {args.atm}, "
          f"mid-based columns on two-sided rows; [95% CI by event date]")
    header = f"{'series':12s} {'period':18s} {'dates':>5s} {'orders':>9s} " + " ".join(
        f"{c:>22s}" for c in MO_COLS)
    print(header)
    for s in sorted(set(d["series"])):
        ds = subset(d, d["series"] == s)
        for label, m in period_masks(ds, args.split or []):
            dp = subset(ds, m)
            cells = " ".join(fmt(stat(dp, c, args.cap)) for c in MO_COLS)
            print(f"{s:12s} {label:18s} {len(set(dp['date'])):5d} {len(dp['ts']):9,d} {cells}")


def cmd_buckets(args):
    d = load(args.data, args.series.split(",") if args.series else None, args.atm)
    cols = ("edge", "mo_60", "mo_300", "mo_settle")
    for label, m in period_masks(d, args.split or []):
        dp = subset(d, m)
        print(f"\n== {label}: {len(set(dp['date']))} event dates, {len(dp['ts']):,} orders ==")
        print(f"{'hours to close':16s} {'orders':>9s} " + " ".join(f"{c:>22s}" for c in cols))
        for lo, hi in HTC_BUCKETS:
            b = subset(dp, (dp["htc_h"] >= lo) & (dp["htc_h"] < hi))
            if len(b["ts"]) == 0:
                continue
            name = f"{lo:g}-{hi:g}h" if hi < 1e8 else f">{lo:g}h"
            print(f"{name:16s} {len(b['ts']):9,d} " + " ".join(fmt(stat(b, c, args.cap)) for c in cols))
        print(f"{'UTC hour':16s} {'orders':>9s} " + " ".join(f"{c:>22s}" for c in cols))
        for h0 in range(0, 24, 3):
            b = subset(dp, (dp["utc_hour"] >= h0) & (dp["utc_hour"] < h0 + 3))
            if len(b["ts"]) == 0:
                continue
            print(f"{h0:02d}-{h0 + 3:02d}{'':11s} {len(b['ts']):9,d} "
                  + " ".join(fmt(stat(b, c, args.cap)) for c in cols))


def design(d, features):
    X = np.column_stack([d[f] for f in features]).astype(float)
    X[~np.isfinite(X)] = np.nan
    return X


def cmd_model(args):
    import warnings
    warnings.filterwarnings("ignore", message="Could not find the number of physical cores")
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.linear_model import Ridge

    d = load(args.data, args.series.split(",") if args.series else None, args.atm)
    target = f"mo_{args.horizon}"
    ok = (d["two_sided"] > 0) & np.isfinite(d[target]) & (d["htc_h"] > 0)
    d = subset(d, ok)
    order = date_order(d["date"])
    rank = {x: i for i, x in enumerate(order)}
    r = np.array([rank[x] for x in d["date"]])
    n_dates = len(order)
    if n_dates < 2 * args.folds + 2:
        sys.exit(f"only {n_dates} event dates; need more for {args.folds} walk-forward folds")
    first_test = int(n_dates * args.min_train)
    edges = np.linspace(first_test, n_dates, args.folds + 1).astype(int)
    features = QUOTE_FEATURES if args.features == "quote" else FEATURES
    print(f"features ({args.features}): {', '.join(features)}")
    X, y, w = design(d, features), d[target], weights(d, args.cap)
    pred = np.full(len(y), np.nan)
    pred_lin = np.full(len(y), np.nan)
    med = None
    for a, b in zip(edges[:-1], edges[1:]):
        tr, te = r < a, (r >= a) & (r < b)
        if not te.any():
            continue
        gb = HistGradientBoostingRegressor(max_iter=200, learning_rate=0.05, max_leaf_nodes=15,
                                           min_samples_leaf=500, l2_regularization=1.0,
                                           random_state=0)
        gb.fit(X[tr], y[tr], sample_weight=w[tr])
        pred[te] = gb.predict(X[te])
        med = np.nanmedian(X[tr], axis=0)
        mu, sd = np.nanmean(X[tr], axis=0), np.nanstd(X[tr], axis=0) + 1e-9
        Z = lambda M: (np.where(np.isnan(M), med, M) - mu) / sd
        lin = Ridge(alpha=10.0).fit(Z(X[tr]), y[tr], sample_weight=w[tr])
        pred_lin[te] = lin.predict(Z(X[te]))
        print(f"fold: train {order[0]}..{order[a - 1]} ({a} dates, {tr.sum():,} orders) -> "
              f"test {order[a]}..{order[b - 1]} ({b - a} dates, {te.sum():,} orders)")
        if b == edges[-1]:
            coef = sorted(zip(features, lin.coef_), key=lambda kv: -abs(kv[1]))
            print("  ridge coefficients on the last fold (cents per 1 sd of the feature):")
            print("   " + ", ".join(f"{k}={100 * c:+.2f}" for k, c in coef))

    te = np.isfinite(pred)
    t = subset(d, te)
    for name, p in (("gradient boosting", pred[te]), ("ridge", pred_lin[te])):
        print(f"\n== out-of-sample, {name}: {len(set(t['date']))} test dates, "
              f"{te.sum():,} orders, target {target} ==")
        wt = weights(t, args.cap)
        cw = np.average((p - np.average(p, weights=wt)) * (t[target] - np.average(t[target], weights=wt)),
                        weights=wt)
        corr = cw / np.sqrt(np.average((p - np.average(p, weights=wt)) ** 2, weights=wt)
                            * np.average((t[target] - np.average(t[target], weights=wt)) ** 2,
                                         weights=wt))
        print(f"weighted correlation of forecast with realized: {corr:+.3f}")
        qs = np.quantile(p, np.linspace(0, 1, 11))
        print(f"{'forecast decile':16s} {'orders':>8s} {'forecast':>9s} "
              f"{'realized ' + target:>22s} {'mo_settle':>22s}")
        for i in range(10):
            m = (p >= qs[i]) & (p <= qs[i + 1]) if i == 9 else (p >= qs[i]) & (p < qs[i + 1])
            b = subset(t, m)
            print(f"{i + 1:2d} {'(most toxic)' if i == 0 else '(least toxic)' if i == 9 else '':13s} "
                  f"{m.sum():8,d} {100 * np.average(p[m], weights=wt[m]):+9.2f} "
                  f"{fmt(stat(b, target, args.cap))} {fmt(stat(b, 'mo_settle', args.cap))}")
        print(f"{'no-quote filter':16s} {'kept':>8s} {'':9s} {'realized ' + target:>22s} {'mo_settle':>22s}")
        for drop in (0.0, 0.1, 0.2, 0.3, 0.5, 0.7):
            m = p >= np.quantile(p, drop)
            b = subset(t, m)
            print(f"drop worst {drop:4.0%}  {m.mean():8.0%} {'':9s} "
                  f"{fmt(stat(b, target, args.cap))} {fmt(stat(b, 'mo_settle', args.cap))}")


def cmd_clock(args):
    from src import clock

    d = load(args.data, args.series.split(",") if args.series else None, args.atm)
    target = f"mo_{args.horizon}"
    max_bins = max(1, int(args.max_window // clock.BIN_S))
    print(f"observation clock: window fitted on the first {args.train_share:.0%} of event dates, "
          f"judged on the rest; target {target}, weight=min(size,{args.cap:g}), "
          f"window <= {args.max_window:g}s")
    print(f"{'series':11s} {'hours to close':>14s} {'window':>15s} {'test dates':>10s} "
          f"{'fills in':>8s} {'inside':>22s} {'outside':>22s} {'pnl/date':>9s} {'gain/date':>26s} "
          f"{'placebo':>8s} {'settle in':>22s} {'settle out':>22s}")
    for s in sorted(set(d["series"])):
        lo, hi = args.htc if args.htc else ((16.0, 24.0) if s.startswith("KXLOW") else (8.0, 16.0))
        m = (d["series"] == s) & (d["htc_h"] >= lo) & (d["htc_h"] < hi) & (d["two_sided"] > 0)
        ds = subset(d, m)
        if len(set(ds["date"])) < args.min_dates:
            continue
        w = weights(ds, args.cap)
        train, test = clock.split_dates(ds["date"], args.train_share)
        window = clock.find_window(clock.bin_pnl(ds["ts"][train], (ds[target] * w)[train]), max_bins)
        if window is None:
            print(f"{s:11s} {f'{lo:g}-{hi:g}h':>14s} {'none loses':>15s}")
            continue
        r = clock.evaluate(ds["ts"][test], ds[target][test], w[test], ds["date"][test], window)
        inside = clock.in_window(ds["ts"], window)
        # settlement P&L uses every fill in the window, not only two-sided ones
        allrows = subset(d, (d["series"] == s) & (d["htc_h"] >= lo) & (d["htc_h"] < hi))
        _, te_all = clock.split_dates(allrows["date"], args.train_share)
        ins_all = clock.in_window(allrows["ts"], window)
        wa = weights(allrows, args.cap)
        st_in = date_bootstrap(allrows["mo_settle"][te_all & ins_all], wa[te_all & ins_all],
                               allrows["date"][te_all & ins_all])
        st_out = date_bootstrap(allrows["mo_settle"][te_all & ~ins_all], wa[te_all & ~ins_all],
                                allrows["date"][te_all & ~ins_all])
        gain = f"{100 * r['gain_per_date']:+7.1f} [{100 * r['gain_lo']:+6.1f},{100 * r['gain_hi']:+6.1f}]"
        print(f"{s:11s} {f'{lo:g}-{hi:g}h':>14s} {clock.window_label(window):>15s} "
              f"{r['n_dates']:10d} {r['share_of_fills']:8.1%} {fmt(r['inside'])} {fmt(r['outside'])} "
              f"{100 * r['pnl_per_date']:+9.1f} {gain:>26s} {r['placebo_rank']:3d}/{r['placebo_n']:<4d} {fmt(st_in)} {fmt(st_out)}")
    print("pnl/date: pool P&L per event date in the window of hours, no rule; "
          "gain/date: cents added per event date by not being filled inside the window, both per "
          "min(size, cap) contracts; placebo: rank of the fitted window among all same-length "
          "windows of the hour on the test dates (1 = the worst one)")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--data", default="data/markouts.npz")
        p.add_argument("--series", default=None, help="comma-separated series filter")
        p.add_argument("--cap", type=float, default=10.0,
                       help="contracts per order a small maker would be filled on")
        p.add_argument("--atm", type=float, nargs=2, default=(0.10, 0.90),
                       metavar=("LO", "HI"), help="fill-price band")
        p.add_argument("--split", nargs="*", default=None, metavar="DATE",
                       help="event dates (e.g. 26AUG10) that start a new period")

    b = sub.add_parser("build")
    b.add_argument("--db", default="data/kalshi_backfill.db")
    b.add_argument("--out", default="data/markouts.npz")
    b.add_argument("--series", default=None)
    b.set_defaults(fn=cmd_build)
    for name, fn in (("curves", cmd_curves), ("buckets", cmd_buckets), ("model", cmd_model),
                     ("clock", cmd_clock)):
        p = sub.add_parser(name)
        common(p)
        p.set_defaults(fn=fn)
        if name == "clock":
            p.add_argument("--horizon", type=int, default=300, choices=HORIZONS_S)
            p.add_argument("--max-window", type=float, default=300.0,
                           help="longest window of the hour to stand down for, seconds")
            p.add_argument("--train-share", type=float, default=0.5)
            p.add_argument("--min-dates", type=int, default=30)
            p.add_argument("--htc", type=float, nargs=2, default=None, metavar=("LO", "HI"),
                           help="hours-to-close window (default: 8-16 highs, 16-24 lows)")
        if name == "model":
            p.add_argument("--horizon", type=int, default=300, choices=HORIZONS_S)
            p.add_argument("--folds", type=int, default=4)
            p.add_argument("--features", choices=("quote", "all"), default="quote",
                           help="quote: only what is known before the order arrives "
                                "(usable as a no-quote filter); all: adds the order's "
                                "own size and sweep (diagnostic only)")
            p.add_argument("--min-train", type=float, default=0.4,
                           help="share of event dates before the first test fold")
    args = ap.parse_args()
    if getattr(args, "atm", None) is not None:
        args.atm = tuple(args.atm)
    args.fn(args)


if __name__ == "__main__":
    main()
