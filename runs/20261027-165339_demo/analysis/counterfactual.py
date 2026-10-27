# Usage, from the repo root: python runs/20261027-165339_demo/analysis/counterfactual.py runs/<run_id>
"""Replay recorded live states through helper.compute_quotes under alternative
parameters. Mids, books and tth are the recorded ones; only parameters change.
sigma: 'live' = the recorded sigma (2s realized vol, binary-scaled);
'coarse' = realized vol of mids resampled every 60s over the trailing 60
minutes (floored, binary-scaled) -- what a noise-robust estimator would see."""
import json
import logging
import math
import sys
from collections import Counter, defaultdict

sys.path.insert(0, ".")  # run from the repo root
from helper import AvellanedaMarketMaker

run = sys.argv[1]
ticks = defaultdict(list)
cls = {}
start = None
for line in open(f"{run}/events.jsonl", encoding="utf-8"):
    e = json.loads(line)
    if e["type"] == "tick" and e["state"] == "quoting":
        ticks[e["ticker"]].append(e)
    elif e["type"] == "market_meta":
        cls[e["ticker"]] = e["liquidity_class"]
    elif e["type"] == "session_start":
        start = e
base = start["strategy_effective"]
log = logging.getLogger("cf")
log.addHandler(logging.NullHandler())


def coarse_sigma(series, i, floor, every_s=60.0, window_s=3600.0):
    t_now = series[i]["ts"]
    pts, nxt = [], None
    for t in reversed(series[:i + 1]):
        if t_now - t["ts"] > window_s:
            break
        if nxt is None or t["ts"] <= nxt:
            pts.append(t["mid"])
            nxt = t["ts"] - every_s
    if len(pts) < 5:
        return None
    d = [a - b for a, b in zip(pts, pts[1:])]
    m = sum(d) / len(d)
    var = sum((x - m) ** 2 for x in d) / max(len(d) - 1, 1)
    return max(math.sqrt(var / (every_s / 3600.0)), floor)


SCENARIOS = {
    "as_run": {},
    "k_floor_40": {"k_floor": 40},
    "h4_cc02": {"max_horizon_h": 4.0, "calibration_constant": 0.02},
    "proposal": {"max_horizon_h": 4.0, "sigma": "coarse", "k_floor": 40,
                 "k_liquid": 200.0, "min_spread_liquid": 0.01},
}

print(f"{'scenario':12s} {'market':34s} {'class':8s} {'spread c p50':>12s} {'bid@/in touch':>13s} {'ask@/in touch':>13s} {'skew c/ct p50':>13s} {'p95':>7s}")
for name, sc in SCENARIOS.items():
    for tk, series in ticks.items():
        c = cls[tk]
        p = dict(base[tk])
        p["max_horizon_h"] = sc.get("max_horizon_h", p["max_horizon_h"])
        p["calibration_constant"] = sc.get("calibration_constant", p["calibration_constant"])
        if c == "liquid" and "min_spread_liquid" in sc:
            p["min_spread"] = sc["min_spread_liquid"]
        mm = AvellanedaMarketMaker(log, None, tk, perf_log_path="", **p)
        spreads, skews, bt, at = [], [], 0, 0
        for i, t in enumerate(series):
            sigma = t["sigma"]
            if sc.get("sigma") == "coarse" and t.get("sigma_from_data"):
                s = coarse_sigma(series, i, p["sigma_floor"])
                if s is not None:
                    sigma = s * t["binary_scale"]
            k = t["k"]
            if c == "liquid" and "k_liquid" in sc:
                k = sc["k_liquid"]
            elif "k_floor" in sc:
                k = max(k, sc["k_floor"])
            q = mm.compute_quotes(t["mid"], 0.0, t["tth_h"], sigma, k)
            spreads.append(100 * (q["ask"] - q["bid"]))
            h = min(t["tth_h"], p["max_horizon_h"])
            skews.append(100 * q["gamma"] * sigma ** 2 * h)
            if t.get("best_bid") is not None and q["bid"] >= t["best_bid"] - 1e-9:
                bt += 1
            if t.get("best_ask") is not None and q["ask"] <= t["best_ask"] + 1e-9:
                at += 1
        n = len(series)
        srt = sorted(spreads)
        sk = sorted(skews)
        print(f"{name:12s} {tk[:34]:34s} {c:8s} {srt[n // 2]:12.1f} {100 * bt / n:12.0f}% {100 * at / n:12.0f}% "
              f"{sk[n // 2]:13.3f} {sk[int(0.95 * (n - 1))]:7.2f}")
    print()
