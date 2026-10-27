# Usage, from the repo root: python runs/20261027-165339_demo/analysis/whatif_q.py runs/<run_id>
"""Replay recorded quoting states through helper.compute_quotes with q=+-5, +-10."""
import json, logging, sys
sys.path.insert(0, ".")  # run from the repo root
from helper import AvellanedaMarketMaker
run = sys.argv[1]
ev = [json.loads(l) for l in open(f"{run}/events.jsonl", encoding="utf-8")]
start = next(e for e in ev if e["type"] == "session_start")
strat = start["strategy_effective"]
log = logging.getLogger("x"); log.addHandler(logging.NullHandler())
bad = checked = 0
zero_skew_q0 = all(abs(e["reservation"] - e["mid"]) < 1e-9 for e in ev
                   if e["type"] == "tick" and e["state"] == "quoting" and not e["q"])
for e in ev:
    if e["type"] != "tick" or e["state"] != "quoting":
        continue
    mm = AvellanedaMarketMaker(log, None, e["ticker"], perf_log_path="", **strat[e["ticker"]])
    for q in (-10, -5, 5, 10):
        r = mm.compute_quotes(e["mid"], q, e["tth_h"], e["sigma"], e["k"])
        checked += 1
        if not ((q > 0 and r["reservation"] < e["mid"]) or (q < 0 and r["reservation"] > e["mid"])):
            bad += 1
        if not (r["bid"] < r["ask"]):
            bad += 1
print(f"q=0 ticks: reservation == mid: {zero_skew_q0}")
print(f"what-if q in +-5,+-10 over recorded states: {checked} checks, {bad} violations "
      f"(reservation below mid when long, above when short, bid < ask)")
