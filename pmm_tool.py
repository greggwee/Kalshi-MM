"""Tools for the Feil-Nendel prediction market model (src/pmm.py).

    python pmm_tool.py reproduce              # check the solver against the paper
    python pmm_tool.py calibrate --out pmm_calibration.json
    python pmm_tool.py surface --series KXHIGHNY --calibration pmm_calibration.json
    python pmm_tool.py replay --series KXHIGHNY --calibration pmm_calibration.json

None of these touch an account or place orders. calibrate and replay read a recorder
database; replay also looks up settled outcomes on the public market endpoint.
"""
import argparse
import json
import logging
import sqlite3
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from typing import List, Optional

import yaml

from src.pmm import ModelParams, simulate, solve
from src.pmm_calibrate import fit_series_ramps
from src.pmm_quoter import DEFAULT_PMM, MODEL_KEYS, load_calibration
from src.shadow import ShadowEngine, fetch_settle_price, replay_events

logger = logging.getLogger("pmm_tool")
quiet = logging.getLogger("pmm_tool.engine")
quiet.addHandler(logging.NullHandler())
quiet.propagate = False

# the paper's numbers, from its figures 1-2 and table 2
PAPER_SPREADS = {(0.0, 0.5): 0.0568, (0.0, 0.3): 0.0560, (0.0, 0.1): 0.0524,
                 (1.0, 0.5): 0.0442, (1.0, 0.1): 0.0394}
PAPER_SKEWS = {(0.4, -50): 0.0065, (0.4, 0): 0.0001, (0.4, 50): -0.0060,
               (0.2, 0): 0.0008, (0.2, 50): -0.0038, (0.8, -50): 0.0038}
PAPER_TABLE = {"baseline": (12.47, 28.11, 49.37, 32.41, 40.50),
               "optimal": (12.39, 10.34, 15.23, 4.20, 9.68)}
TABLE_FIELDS = ("mean_pnl", "std_pnl", "mean_abs_q", "var5", "es5")

# Replay settings: the strategy sizes from session_config.yaml
REPLAY_STRATEGY = {"base_order_size": 2, "max_position": 10, "min_spread": 0.02,
                   "gamma": 1.0, "default_k": 40.0, "premium_scale": 0.02,
                   "max_horizon_h": 24.0, "calibration_constant": 0.04, "sigma_floor": 0.01}


def cmd_reproduce(args):
    t0 = time.perf_counter()
    surface = solve(ModelParams(), n_p=args.n_p, n_t=args.n_t)
    print(f"solved the paper's Table 1 in {time.perf_counter() - t0:.2f}s: {surface.stats}\n")
    print("Figure 1, spread at q = 0        ours    paper")
    for (t, p), paper in PAPER_SPREADS.items():
        c = surface.continuous_quotes(1.0 - t, p, 0.0)
        print(f"  t = {t:.1f}, p = {p:.2f}            {c['ask'] - c['bid']:.4f}   {paper:.4f}")
    print("Figure 2, skew at t = 0          ours    paper")
    for (p, q), paper in PAPER_SKEWS.items():
        c = surface.continuous_quotes(1.0, p, float(q))
        print(f"  p = {p:.1f}, q = {q:+4d}            {0.5 * (c['ask'] + c['bid']) - p:+.4f}  {paper:+.4f}")
    print(f"\nTable 2, {args.paths} paths         mean    std    |q|   VaR5    ES5")
    print("  paper baseline         " + "".join(f"{x:7.2f}" for x in PAPER_TABLE["baseline"]))
    print("  paper optimal          " + "".join(f"{x:7.2f}" for x in PAPER_TABLE["optimal"]))
    runs = (("ours baseline, bounded", None, {"bound_baseline": True}),
            ("ours baseline, no bound", None, {}), ("ours optimal", surface, {}))
    for name, surf, kw in runs:
        r = simulate(surf, ModelParams(), n_paths=args.paths, seed=args.seed, **kw)
        print(f"  {name:23s}" + "".join(f"{r[k]:7.2f}" for k in TABLE_FIELDS))
    print("\nThe paper's footnote 4 says its baseline has no inventory bound, but its figures"
          "\nmatch the bounded run here (figure 5b also shows it stopping flat at +-100).")
    return 0


def recorded_series(db: str) -> List[str]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = conn.execute("SELECT DISTINCT series_ticker FROM markets WHERE series_ticker IS NOT NULL"
                        " ORDER BY 1").fetchall()
    conn.close()
    return [r[0] for r in rows]


def cmd_calibrate(args):
    series = args.series.split(",") if args.series else recorded_series(args.db)
    out = fit_series_ramps(args.db, series, stand_down_h=args.stand_down, delta=args.delta,
                           T=args.T, logger=logger)
    out["source"] = args.db
    out["generated_ts"] = time.time()
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    print(f"{len(out['series'])} of {len(series)} series fitted, stand-down {args.stand_down}h: {args.out}")
    for name, fitted in out["series"].items():
        p = fitted["params"]
        print(f"  {name:12s} sigma {p['sigma0']:.2f}->{p['sigma0'] + p['sigma1']:.2f} (eta {p['eta']:g})"
              f"  A {p['A0']:.1f}->{p['A0'] + p['A1']:.0f} (xi {p['xi']:g})"
              f"  k {p['k0']:.0f}->{p['k1']:.0f}")
    return 0


def _surface_params(args) -> ModelParams:
    values = {k: DEFAULT_PMM[k] for k in MODEL_KEYS}
    fitted = (load_calibration(args.calibration).get("series") or {}).get(args.series)
    if fitted:
        values.update({k: fitted["params"][k] for k in MODEL_KEYS})
    elif args.series:
        print(f"no calibration for {args.series}: using the flat priors")
    return ModelParams(gamma=args.gamma, gamma_T=args.gamma_T, Q=args.Q, delta=args.delta,
                       min_offset=0.005, **values)


def cmd_surface(args):
    params = _surface_params(args)
    t0 = time.perf_counter()
    surface = solve(params, horizon=max(args.taus) + 1.0, n_p=60, growth=1.1)
    print(f"solved in {time.perf_counter() - t0:.2f}s: {surface.stats}")
    print(f"gamma {params.gamma:g}, gamma_T {params.gamma_T:g}, Q {params.Q:g}, delta {params.delta:g}, "
          f"mid {args.mid}; hours are to the stand-down\n")
    inventories = [-params.Q, -params.Q / 2, 0.0, params.Q / 2, params.Q]
    print("   hours      k  fills/h  " + "".join(f"q={q:<+11g}" for q in inventories))
    for tau in args.taus:
        cells = []
        for q in inventories:
            c = surface.tick_quotes(tau, args.mid, q)
            cells.append(f"{c['bid'] or 0:.2f}/{c['ask'] or 0:.2f}".ljust(13))
        print(f"{tau:8.1f} {float(params.k(tau)):6.0f} {params.max_intensity(tau):8.2f}  " + "".join(cells))
    print("\n0.00 on a side means no quote there: the limit is reached, or no fill is worth having.")
    return 0


def replay_market(job: dict) -> Optional[dict]:
    """One market under one strategy. Runs in a worker process."""
    conn = sqlite3.connect(f"file:{job['db']}?mode=ro", uri=True)
    row = conn.execute("SELECT close_time, fee_type, fee_multiplier FROM markets WHERE ticker = ?",
                       (job["ticker"],)).fetchone()
    params = {**REPLAY_STRATEGY, "min_time_to_resolution_h": job["stand_down"]}
    engine = ShadowEngine(job["ticker"], row[0], row[1] or "quadratic", row[2] or 1.0, params, quiet,
                          fill_mode=job["fill_mode"], pmm_config=job["pmm"])
    events = 0
    for kind, ts, payload in replay_events(conn, job["ticker"]):
        engine.on_event(kind, ts, payload)
        events += 1
    conn.close()
    if not events:
        return None
    result = engine.finalize(job["settle"])
    result.update(strategy=job["strategy"], fill_mode=job["fill_mode"],
                  solves=engine.pmm.solves if engine.pmm else 0)
    return result


def cmd_replay(args):
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    prefixes = args.series.split(",")
    tickers = [r[0] for r in conn.execute("SELECT ticker FROM markets ORDER BY ticker")
               if r[0].split("-")[0] in prefixes]
    conn.close()
    print(f"{len(tickers)} recorded markets in {prefixes}; looking up outcomes", flush=True)
    settled = {t: fetch_settle_price(t) for t in tickers}
    tickers = [t for t in tickers if settled[t] is not None or not args.settled_only]
    pmm = {"calibration_file": args.calibration, "resolve_every_s": 14400.0, "resolve_min_s": 1800.0,
           "gamma": args.gamma, "gamma_T": args.gamma_T}
    strategies = {"avellaneda": None, "pmm": pmm, "pmm_myopic": {**pmm, "gamma": 0.0, "gamma_T": 0.0}}
    jobs = [{"db": args.db, "ticker": t, "settle": settled[t], "strategy": name, "pmm": cfg,
             "fill_mode": mode, "stand_down": args.stand_down}
            for t in tickers for name, cfg in strategies.items() for mode in args.fill_modes]
    print(f"{len(tickers)} markets with outcomes, {len(jobs)} replays", flush=True)
    t0 = time.perf_counter()
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        results = [r for r in pool.map(replay_market, jobs, chunksize=1) if r]
    print(f"replayed in {time.perf_counter() - t0:.0f}s\n")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=1, default=str)

    print(f"{'strategy':12s} {'fills':>6s} {'mkts':>5s} {'net $':>9s} {'fees':>7s} {'$ per fill':>10s} "
          f"{'worst mkt':>9s} {'mo60 c':>7s} {'solves':>7s}")
    for mode in args.fill_modes:
        print(f"-- fill model: {mode}")
        for name in strategies:
            rows = [r for r in results if r["strategy"] == name and r["fill_mode"] == mode]
            fills = sum(r["fills"] for r in rows)
            net = sum(r["net_pnl"] for r in rows)
            traded = [r for r in rows if r["fills"]]
            mo = [r["avg_markout_60s"] for r in traded if r.get("avg_markout_60s") is not None]
            print(f"{name:12s} {fills:6d} {len(traded):5d} {net:9.2f} "
                  f"{sum(r['fees'] for r in rows):7.2f} {net / fills if fills else 0:10.3f} "
                  f"{min((r['net_pnl'] for r in rows), default=0):9.2f} "
                  f"{100 * sum(mo) / len(mo) if mo else 0:7.2f} {sum(r['solves'] for r in rows):7d}")
    print("\nA replay sits on a tape our quotes did not influence, and a few recorded days are "
          "little evidence.\nRead the sizes and risk, not the sign of the P&L.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Feil-Nendel prediction market model tools")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("reproduce", help="check the solver against the paper's published numbers")
    p.add_argument("--paths", type=int, default=10000)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--n-p", type=int, default=200)
    p.add_argument("--n-t", type=int, default=2000)
    p.set_defaults(func=cmd_reproduce)

    p = sub.add_parser("calibrate", help="fit series parameters from a recorder database")
    p.add_argument("--db", default="data/kalshi_recorder.db")
    p.add_argument("--series", help="comma-separated series (default: all recorded)")
    p.add_argument("--stand-down", type=float, default=8.0, help="hours before close the bot stops")
    p.add_argument("--delta", type=float, default=2.0, help="our order size, contracts")
    p.add_argument("--T", type=float, default=40.0, help="hours over which the ramps act")
    p.add_argument("--out", default="pmm_calibration.json")
    p.set_defaults(func=cmd_calibrate)

    p = sub.add_parser("surface", help="print the quotes a parameter set implies")
    p.add_argument("--series")
    p.add_argument("--calibration", default="pmm_calibration.json")
    p.add_argument("--gamma", type=float, default=DEFAULT_PMM["gamma"])
    p.add_argument("--gamma-T", type=float, default=DEFAULT_PMM["gamma_T"])
    p.add_argument("--Q", type=float, default=10.0)
    p.add_argument("--delta", type=float, default=2.0)
    p.add_argument("--mid", type=float, default=0.505)
    p.add_argument("--taus", type=float, nargs="+", default=[60.0, 30.0, 15.0, 8.0, 4.0, 1.0, 0.1])
    p.set_defaults(func=cmd_surface)

    p = sub.add_parser("replay", help="compare strategies on recorded data with simulated fills")
    p.add_argument("--db", default="data/kalshi_recorder.db")
    p.add_argument("--series", required=True, help="comma-separated series")
    p.add_argument("--calibration", default="pmm_calibration.json")
    p.add_argument("--stand-down", type=float, default=8.0)
    p.add_argument("--gamma", type=float, default=DEFAULT_PMM["gamma"])
    p.add_argument("--gamma-T", type=float, default=DEFAULT_PMM["gamma_T"])
    p.add_argument("--fill-modes", nargs="+", default=["queue", "strict"])
    p.add_argument("--settled-only", action="store_true", default=True)
    p.add_argument("--jobs", type=int, default=4)
    p.add_argument("--out")
    p.set_defaults(func=cmd_replay)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
