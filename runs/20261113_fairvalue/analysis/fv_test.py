"""Observation-driven fair value for the lows, tested walk-forward on the tape.

    python runs/20261113_fairvalue/analysis/fv_test.py

Run from the checkout (needs data/kalshi_backfill.db, data/obs.db,
data/markouts_all.npz, data/markouts_holdout.npz).

For every aggressive order in the 15 low series, placed during the climate
day, three fair values are computed, each fitted on EARLIER dates only
(trailing 90 days, refitted weekly):

  station   the drop model alone: only what the station had reported
  price     the traded price, recalibrated (logit regression on the outcome)
  blend     price and station model together

The order's price is the touch it took, so every row is a price that was
really available to a taker at that moment. The question is which orders each
fair value would have taken, and what those orders earned after the fee.

The whole thing is repeated for several assumed delays between a report's
observation time and the moment it is public.

Cents per contract, weight = min(contracts, 10), 95% intervals from
resampling event dates.
"""
import json
import os
import sqlite3
import sys
from datetime import datetime

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from src import obs as obs_mod  # noqa: E402
from src.clock import in_window  # noqa: E402
from src.fairvalue import HOUR_GROUPS, Combiner, DayState, DropModel, taker_fee, yes_interval  # noqa: E402
from src.markouts import date_bootstrap  # noqa: E402

WINDOW = (104, 10)
CAP = 10.0
TRAIN_DAYS = 90
REFIT_DAYS = 7
MIN_TRAIN_DAYS = 45
COMBINE_MIN_DAYS = 30
GRID_S = 600
LIKE = 0.02                      # an order is "liked" when the model sees 2c or more after the fee
DELAYS = ((0, 0), (60, 300), (180, 900), (600, 1800))     # (hourly report, 5-minute reading) seconds
MAIN = (180, 900)
BLOCKS = list(zip(HOUR_GROUPS[:-1], HOUR_GROUPS[1:]))


def parse_date(code):
    return datetime.strptime(code, "%y%b%d").date()


def load_markets(db):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    out = {}
    for ticker, raw, result in conn.execute(
            "SELECT ticker, raw_json, result FROM markets WHERE series_ticker LIKE 'KXLOW%'"
            " AND result IN ('yes','no')"):
        j = json.loads(raw)
        try:
            lo, hi = yes_interval(j.get("strike_type"), j.get("floor_strike"), j.get("cap_strike"))
            low = int(round(float(j["expiration_value"])))
        except (TypeError, ValueError, KeyError):
            continue
        out[ticker] = (lo, hi, low, 1.0 if result == "yes" else 0.0)
    return out


def load_obs(obs_db, series):
    conn = sqlite3.connect(f"file:{obs_db}?mode=ro", uri=True)
    out = {}
    for s in series:
        rows = conn.execute("SELECT ts, temp_c, precise, min6_c FROM obs WHERE station = ?"
                            " ORDER BY ts", (obs_mod.STATIONS[s][0],)).fetchall()
        out[s] = np.array(rows, dtype=float) if rows else np.zeros((0, 4))
    return out


def load_orders(markets):
    cols = ("ts", "side", "size", "touch", "series", "date", "ticker", "sweep")
    parts = []
    for path in ("data/markouts_all.npz", "data/markouts_holdout.npz"):
        z = np.load(path)
        m = np.char.startswith(z["series"], "KXLOW")
        parts.append({k: z[k][m] for k in cols})
    o = {k: np.concatenate([p[k] for p in parts]) for k in cols}
    keep = np.array([t in markets for t in o["ticker"]]) & (o["touch"] >= 0.03) & (o["touch"] <= 0.97)
    o = {k: v[keep] for k, v in o.items()}
    info = np.array([markets[t] for t in o["ticker"]])
    o["lo"], o["hi"], o["y"] = info[:, 0], info[:, 1], info[:, 3]
    o["day"] = np.array([parse_date(c).toordinal() for c in o["date"]])
    o["key"] = np.char.add(np.char.add(o["series"], "|"), o["date"])
    return o


def cell(st):
    if st["n_dates"] == 0:
        return f"{'-':>22s}"
    return f"{100 * st['mean']:+7.2f} [{100 * st['lo']:+6.2f},{100 * st['hi']:+6.2f}]"


def run(markets, lows, raw_obs, base, lat_precise, lat_coarse):
    """Walk-forward station, price and blend fair values for one report delay."""
    states = {}
    for series, code in lows:
        a = raw_obs[series]
        start, end = obs_mod.climate_day_bounds(parse_date(code), obs_mod.STATIONS[series][1])
        i, j = np.searchsorted(a[:, 0], [start, end])
        d = a[i:j]
        states[(series, code)] = DayState(d[:, 0], d[:, 1], d[:, 2], d[:, 3], start,
                                          lat_precise, lat_coarse)

    g = {k: [] for k in ("hour", "gap", "coarse", "d", "day", "x", "low")}
    for (series, code), st in states.items():
        t = st.day_start + np.arange(0, 86400, GRID_S, dtype=float)
        a = st.at(t)
        n = len(t)
        g["hour"].append(a["hour"]); g["gap"].append(a["gap"]); g["coarse"].append(a["coarse"])
        g["x"].append(a["x"]); g["low"].append(np.full(n, lows[(series, code)]))
        g["d"].append(lows[(series, code)] - a["x"])
        g["day"].append(np.full(n, parse_date(code).toordinal()))
    g = {k: np.concatenate(v) for k, v in g.items()}

    o = dict(base)
    n = len(o["ts"])
    for k in ("x", "gap", "hour"):
        o[k] = np.full(n, np.nan)
    o["coarse"] = np.zeros(n, bool)
    order = np.argsort(o["key"], kind="stable")
    uniq, starts = np.unique(o["key"][order], return_index=True)
    for kk, a0, a1 in zip(uniq, starts, list(starts[1:]) + [n]):
        idx = order[a0:a1]
        series, code = kk.split("|")
        a = states[(series, code)].at(o["ts"][idx])
        in_day = (a["hour"] >= 0) & (a["hour"] < 24)
        o["x"][idx] = np.where(in_day, a["x"], np.nan)
        o["gap"][idx], o["hour"][idx], o["coarse"][idx] = a["gap"], a["hour"], a["coarse"]

    # stage 1: the station model, on the orders and (for calibration) on the plain grid
    o["fv"] = np.full(n, np.nan)
    g["p_drop"] = np.full(len(g["day"]), np.nan)      # P(low ends at least 1F below the rounded running low)
    first, last = int(g["day"].min()) + MIN_TRAIN_DAYS, int(o["day"].max())
    for start in range(first, last + 1, REFIT_DAYS):
        tr = (g["day"] < start) & (g["day"] >= start - TRAIN_DAYS)
        if tr.sum() < 1000:
            continue
        model = DropModel().fit(g["hour"][tr], g["gap"][tr], g["coarse"][tr], g["d"][tr])
        te = (o["day"] >= start) & (o["day"] < start + REFIT_DAYS) & np.isfinite(o["x"])
        if te.any():
            o["fv"][te] = model.prob(o["hour"][te], o["gap"][te], o["coarse"][te], o["x"][te],
                                     o["lo"][te], o["hi"][te])
        tg = (g["day"] >= start) & (g["day"] < start + REFIT_DAYS) & np.isfinite(g["x"])
        if tg.any():
            hi = np.floor(g["x"][tg] + 0.5) - 1
            g["p_drop"][tg] = model.prob(g["hour"][tg], g["gap"][tg], g["coarse"][tg], g["x"][tg],
                                         np.full(tg.sum(), -999.0), hi)

    # stage 2: recalibrated price, and price blended with the station model
    usable = np.isfinite(o["fv"])
    wt = np.minimum(o["size"], CAP)
    o["pfv"] = np.full(n, np.nan)
    o["cfv"] = np.full(n, np.nan)
    blend = None
    for start in range(first + COMBINE_MIN_DAYS, last + 1, REFIT_DAYS):
        tr = usable & (o["day"] < start) & (o["day"] >= start - TRAIN_DAYS)
        te = usable & (o["day"] >= start) & (o["day"] < start + REFIT_DAYS)
        if not te.any() or tr.sum() < 5000:
            continue
        price_only = Combiner().fit(o["touch"][tr], None, o["hour"][tr], o["y"][tr], wt[tr])
        blend = Combiner().fit(o["touch"][tr], o["fv"][tr], o["hour"][tr], o["y"][tr], wt[tr])
        o["pfv"][te] = price_only.predict(o["touch"][te], None, o["hour"][te])
        o["cfv"][te] = blend.predict(o["touch"][te], o["fv"][te], o["hour"][te])
    ok = np.isfinite(o["cfv"]) & np.isfinite(o["pfv"])
    o = {k: v[ok] for k, v in o.items()}
    return o, g, blend, states


def main():
    markets = load_markets("data/kalshi_backfill.db")
    lows = {}
    for ticker, (_, _, low, _) in markets.items():
        s, code = ticker.split("-")[:2]
        lows[(s, code)] = low
    raw_obs = load_obs("data/obs.db", {s for s, _ in lows})
    base = load_orders(markets)
    print(f"{len(markets):,} settled markets, {len(lows):,} station-days, "
          f"{len(base['ts']):,} orders at 0.03-0.97")

    print("\n1. WHICH FAIR VALUE FINDS THE ORDERS THAT EARN? taker P&L after the fee on the orders")
    print(f"   each model liked by {100 * LIKE:.0f}c or more, by assumed report delay (hourly / 5-minute)")
    print(f"   {'delay':>10s} {'model':8s} {'orders':>8s} {'dates':>5s} {'taker net (c/contract)':>24s}"
          f"   station weight c by block of the climate day")
    kept = None
    for lat in DELAYS:
        o, g, blend, states = run(markets, lows, raw_obs, base, *lat)
        w = np.minimum(o["size"], CAP)
        fee = taker_fee(o["touch"])
        net = o["side"] * (o["y"] - o["touch"]) - fee
        cs = " ".join(f"{blend.coef[i][2]:+.2f}" if i in blend.coef else "  -  " for i in range(len(BLOCKS)))
        for name, col in (("station", "fv"), ("price", "pfv"), ("blend", "cfv")):
            m = o["side"] * (o[col] - o["touch"]) - fee >= LIKE
            st = date_bootstrap(net[m], w[m], o["date"][m])
            extra = f"   {cs}" if name == "blend" else ""
            print(f"   {lat[0]:4d}/{lat[1]:<5d} {name:8s} {m.sum():8,d} {st['n_dates']:5d} {cell(st)}{extra}")
        if lat == MAIN:
            kept = (o, g, states)
    o, g, states = kept
    w = np.minimum(o["size"], CAP)
    s, p, y, dates = o["side"], o["touch"], o["y"], o["date"]
    fee = taker_fee(p)
    gross = s * (y - p)
    net = gross - fee
    print(f"   blocks: " + ", ".join(f"hour {a:02d}-{b:02d}" for a, b in BLOCKS))
    print(f"\nEverything below uses the {MAIN[0]}s / {MAIN[1]}s delay: {len(p):,} orders on "
          f"{len(set(dates))} event dates from {datetime.fromordinal(int(o['day'].min())).date()}")

    diff = []
    for key, st in states.items():
        end = st.at(np.array([st.day_start + 86400 + 3 * 3600.0]))
        if np.isfinite(end["x"][0]):
            diff.append(lows[key] - end["x"][0])
    diff = np.array(diff)
    print(f"\n2. DATA CHECK. settled low minus the lowest report of the day, {len(diff):,} station-days: "
          f"median {np.median(diff):+.2f}F, within 1F {np.mean(np.abs(diff) <= 1.0):.1%}, "
          f"settled lower by more than 1F {np.mean(diff < -1.0):.1%}, higher by more than 1F "
          f"{np.mean(diff > 1.0):.1%}")

    print("\n3. IS THE STATION MODEL CALIBRATED where nobody chose the moment? Every 10 minutes of every")
    print("   station-day, out of sample: P(the low ends at least 1F below the running low), predicted vs realised")
    happened = (g["low"] <= np.floor(g["x"] + 0.5) - 1).astype(float)
    print(f"   {'predicted':11s}" + "".join(f"{f'hour {a:02d}-{b:02d}':>16s}" for a, b in BLOCKS))
    for lo, hi in ((0, 0.02), (0.02, 0.1), (0.1, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 0.98),
                   (0.98, 1.01)):
        line = f"   {lo:.2f}-{min(hi, 1):.2f}  "
        for a, b in BLOCKS:
            m = np.isfinite(g["p_drop"]) & (g["p_drop"] >= lo) & (g["p_drop"] < hi) \
                & (g["hour"] >= a) & (g["hour"] < b)
            line += f"{happened[m].mean():8.2f} n={m.sum():<5d}" if m.sum() >= 100 else f"{'-':>16s}"
        print(line)

    def edge_table(title, col):
        edge = s * (o[col] - p) - fee
        print(f"\n{title}")
        print(f"   {'model edge after fee':22s} {'orders':>9s} {'dates':>5s} "
              f"{'taker net (c/contract)':>24s} {'$ per date, 10 lots':>20s}")
        for lo, hi in ((-9, -0.05), (-0.05, -0.02), (-0.02, 0.0), (0.0, 0.01), (0.01, 0.02),
                       (0.02, 0.03), (0.03, 0.05), (0.05, 0.10), (0.10, 9)):
            m = (edge >= lo) & (edge < hi)
            if m.sum() == 0:
                continue
            st = date_bootstrap(net[m], w[m], dates[m])
            lab = f"{100 * lo:+.0f}c to {100 * hi:+.0f}c" if abs(lo) < 9 and abs(hi) < 9 else \
                (f"below {100 * hi:+.0f}c" if lo <= -9 else f"above {100 * lo:+.0f}c")
            print(f"   {lab:22s} {m.sum():9,d} {st['n_dates']:5d} {cell(st)} "
                  f"{(net[m] * w[m]).sum() / len(set(dates)):20.2f}")
        return edge

    edge_table("4a. STATION MODEL ALONE: taker P&L to settlement by the edge the model saw", "fv")
    edge = edge_table("4b. RECALIBRATED PRICE (no station data): same table", "pfv")

    good = edge >= LIKE
    print(f"\n5. THE ORDERS THE RECALIBRATED PRICE LIKED by {100 * LIKE:.0f}c or more: who are they?")
    print(f"   {'slice':28s} {'orders':>9s} {'dates':>5s} {'taker net (c/contract)':>24s}")

    def row(label, m):
        m = m & good
        if m.sum():
            print(f"   {label:28s} {m.sum():9,d} {len(set(dates[m])):5d} "
                  f"{cell(date_bootstrap(net[m], w[m], dates[m]))}")

    everything = np.ones(len(p), bool)
    inside = in_window(o["ts"], WINDOW)
    row("all", everything)
    row("taker buys YES", s > 0)
    row("taker sells YES", s < 0)
    row("taker on the favourite", s * (p - 0.5) > 0)
    row("taker on the longshot", s * (p - 0.5) < 0)
    row("inside :52-:57", inside)
    row("outside :52-:57", ~inside)
    for a, b in BLOCKS:
        row(f"climate-day hour {a:02d}-{b:02d}", (o["hour"] >= a) & (o["hour"] < b))
    row("level-clearing orders", o["sweep"] > 0)
    row("single-level orders", o["sweep"] == 0)
    for ser in sorted(set(o["series"])):
        row(ser, o["series"] == ser)
    months = np.array([parse_date(c).strftime("%Y-%m") for c in dates])
    for mth in sorted(set(months)):
        row(mth, months == mth)

    print("\n6. IS IT A STANDING MISPRICING OR THE TAKER'S INFORMATION? If favourites were simply")
    print("   underpriced, the resting order that BOUGHT the favourite would earn at least as much")
    print(f"   {'who holds the favourite':28s} {'price band':12s} {'orders':>9s} {'dates':>5s} "
          f"{'P&L before fees (c)':>24s}")
    fav_taker = s * (p - 0.5) > 0
    for a, b in ((0.03, 0.20), (0.20, 0.40), (0.40, 0.60), (0.60, 0.80), (0.80, 0.97)):
        band = (p >= a) & (p < b)
        if a < 0.5 <= b:
            continue
        for who, m, val in (("taker took the favourite", fav_taker, gross),
                            ("maker ended with the favourite", ~fav_taker, -gross)):
            m = m & band
            if m.sum():
                print(f"   {who:28s} {a:.2f}-{b:.2f}    {m.sum():9,d} {len(set(dates[m])):5d} "
                      f"{cell(date_bootstrap(val[m], w[m], dates[m]))}")


if __name__ == "__main__":
    main()
