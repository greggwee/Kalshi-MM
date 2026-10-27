# Usage, from the repo root: python runs/20261027-165339_demo/analysis/analyze_run.py runs/<run_id>
"""Supplementary analysis of runs/<id>/events.jsonl: quote invariants, the
reservation/inventory relationship (observed and what-if at live sigma),
spread-term dominance, the binary vol scaling's effect, competitiveness,
order activity, fills/markouts. Prints JSON-ish text for the write-up."""
import json
import math
import statistics as st
import sys
from collections import Counter, defaultdict

run = sys.argv[1]
ev = defaultdict(list)
for line in open(f"{run}/events.jsonl", encoding="utf-8"):
    e = json.loads(line)
    ev[e["type"]].append(e)
cls = {m["ticker"]: m["liquidity_class"] for m in ev["market_meta"]}
start = ev["session_start"][0]
cfg = start.get("strategy_effective") or {}


def pct(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    return xs[min(len(xs) - 1, int(q * (len(xs) - 1) + 0.5))]


def r(x, n=3):
    return None if x is None else round(x, n)


def corr(a, b):
    pairs = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
    if len(pairs) < 3:
        return None
    xs, ys = zip(*pairs)
    if st.pstdev(xs) == 0 or st.pstdev(ys) == 0:
        return None
    return st.correlation(xs, ys)


ticks = defaultdict(list)
for t in ev["tick"]:
    ticks[t["ticker"]].append(t)

print(f"run {run}: dry_run={start.get('dry_run')} cycles={len(ev['cycle'])} "
      f"ticks={len(ev['tick'])} orders={len(ev['order'])} fills={len(ev['fill'])} "
      f"trades={len(ev['trade'])} markouts={len(ev['markout'])}")
end = (ev["session_end"] or [{}])[-1]
print(f"end: reason={end.get('reason')} left_resting={end.get('orders_left_resting')} "
      f"verified={end.get('orders_left_verified')} total={end.get('total')}")
print("shutdown checks:", [(c["attempt"], c["verified"], c["remaining"]) for c in ev["shutdown_check"]])
print("event types:", dict(Counter({k: len(v) for k, v in ev.items()})))
cyc = [c["ms"] for c in ev["cycle"]]
if cyc:
    print(f"cycle ms p50={pct(cyc, .5)} p95={pct(cyc, .95)} max={max(cyc)} "
          f"overrun(>2000)={sum(1 for c in cyc if c > 2000)}/{len(cyc)}")
print("errors:", Counter((e.get("phase"), e.get("error", "")[:80]) for e in ev["cycle_error"]).most_common(5))
print("risk events:", [(e.get("scope"), e.get("reason")) for e in ev["risk"]][:10])
print("position_mismatch:", len(ev["position_mismatch"]))

print("\n== per market")
for tk, ts in ticks.items():
    q_ticks = [t for t in ts if t["state"] == "quoting"]
    reasons = Counter(t.get("reason") for t in ts if t["state"] != "quoting")
    inv = {"bid>=ask": 0, "bid>=best_ask": 0, "ask<=best_bid": 0, "res_sign": 0, "q!=0": 0}
    skew, qs = [], []
    whatif = []          # per-contract skew in cents at the live sigma: gamma*sigma^2*h
    noscale = []         # same without the binary sqrt(p(1-p))/0.5 scaling
    terms = defaultdict(list)
    ours_vs_mkt = []
    behind_b, behind_a, at_b, at_a, inside_b, inside_a = [], [], 0, 0, 0, 0
    sig_cold, sig_data, k_vals, scale = [], [], [], []
    for t in q_ticks:
        b, a, bb, ba, mid = t.get("bid"), t.get("ask"), t.get("best_bid"), t.get("best_ask"), t.get("mid")
        if b is not None and a is not None and b >= a - 1e-9:
            inv["bid>=ask"] += 1
        if b is not None and ba is not None and b >= ba - 1e-9:
            inv["bid>=best_ask"] += 1
        if a is not None and bb is not None and a <= bb + 1e-9:
            inv["ask<=best_bid"] += 1
        q = t.get("q") or 0.0
        res = t.get("reservation")
        if res is not None and mid is not None:
            skew.append(res - mid)
            qs.append(q)
            if abs(q) > 1e-9:
                inv["q!=0"] += 1
                if (q > 0 and not res < mid) or (q < 0 and not res > mid):
                    inv["res_sign"] += 1
        h = min(t.get("tth_h") or 24.0, cfg.get(tk, {}).get("max_horizon_h", 24.0) if isinstance(cfg.get(tk), dict) else 24.0)
        g, sg = t.get("gamma"), t.get("sigma")
        if g and sg:
            whatif.append(100 * g * sg ** 2 * h)
            sc = t.get("binary_scale") or 1.0
            noscale.append(100 * g * (sg / sc) ** 2 * h)
            scale.append(sc)
        for key in ("spread_inv", "spread_liq", "spread_prem", "spread"):
            if t.get(key) is not None:
                terms[key].append(100 * t[key])
        if b is not None and a is not None and bb is not None and ba is not None:
            ours_vs_mkt.append((100 * (a - b), 100 * (ba - bb)))
        if b is not None and bb is not None:
            if b > bb + 1e-9:
                inside_b += 1
            elif abs(b - bb) < 1e-9:
                at_b += 1
            else:
                behind_b.append(round(100 * (bb - b)))
        if a is not None and ba is not None:
            if a < ba - 1e-9:
                inside_a += 1
            elif abs(a - ba) < 1e-9:
                at_a += 1
            else:
                behind_a.append(round(100 * (a - ba)))
        (sig_data if t.get("sigma_from_data") else sig_cold).append(t.get("sigma"))
        if t.get("k") is not None:
            k_vals.append(t["k"])
    n = len(q_ticks) or 1
    dom = Counter()
    for t in q_ticks:
        parts = {k: t.get(k) or 0 for k in ("spread_inv", "spread_liq", "spread_prem")}
        dom[max(parts, key=parts.get)] += 1
    mids = [t["mid"] for t in ts if t.get("mid") is not None]
    print(f"\n{tk} [{cls.get(tk)}] ticks={len(ts)} quoting={len(q_ticks)} standdown={dict(reasons)}")
    print(f"  invariants violated: {inv}")
    print(f"  mid {r(min(mids) if mids else None)}..{r(max(mids) if mids else None)} changes="
          f"{sum(1 for x, y in zip(mids, mids[1:]) if abs(x - y) > 1e-9)}")
    print(f"  sigma cold={r(st.mean(sig_cold), 4) if sig_cold else None} (n={len(sig_cold)}) "
          f"data p50={r(pct(sig_data, .5), 4)} p95={r(pct(sig_data, .95), 4)} max={r(max(sig_data) if sig_data else None, 4)}")
    print(f"  k p50={r(pct(k_vals, .5), 1)} min={r(min(k_vals) if k_vals else None, 1)} max={r(max(k_vals) if k_vals else None, 1)}  vol_scale={r(st.mean(scale) if scale else None)}")
    for key in ("spread", "spread_inv", "spread_liq", "spread_prem"):
        v = terms[key]
        print(f"  {key:12s} c: p50={r(pct(v, .5), 2)} p95={r(pct(v, .95), 2)} mean={r(st.mean(v) if v else None, 2)}")
    print(f"  dominant term (% of quoting ticks): " + ", ".join(f"{k}={100 * c / n:.0f}%" for k, c in dom.items()))
    print(f"  skew per contract (c) at live sigma: p50={r(pct(whatif, .5), 3)} p95={r(pct(whatif, .95), 3)}"
          f" | without vol scaling p50={r(pct(noscale, .5), 3)} | x5 contracts p50={r(5 * pct(whatif, .5) if whatif else None, 2)}")
    if ours_vs_mkt:
        print(f"  quoted spread c mean={r(st.mean(o for o, _ in ours_vs_mkt), 2)} vs market mean={r(st.mean(m for _, m in ours_vs_mkt), 2)}")
    print(f"  bid: inside={100 * inside_b / n:.0f}% at={100 * at_b / n:.0f}% behind={100 * len(behind_b) / n:.0f}% (ticks behind p50={pct(behind_b, .5)})"
          f" | ask: inside={100 * inside_a / n:.0f}% at={100 * at_a / n:.0f}% behind={100 * len(behind_a) / n:.0f}% (p50={pct(behind_a, .5)})")
    if skew and any(abs(q) > 0 for q in qs):
        print(f"  corr(reservation-mid, q)={r(corr(skew, qs))}  max|q|={max(abs(q) for q in qs)}")
    o = [x for x in ev["order"] if x["ticker"] == tk]
    print(f"  orders: {dict(Counter((x['action'], x['ok']) for x in o))} why={dict(Counter(x.get('why') for x in o if x.get('why')))}")
    rej = Counter((x["action"], x.get("reject_reason")) for x in o if not x["ok"])
    if rej:
        print(f"  rejects: {dict(rej)} e.g. {[x.get('error') for x in o if not x['ok']][:2]}")
    f = [x for x in ev["fill"] if x["ticker"] == tk]
    if f:
        print(f"  fills: {[(x['side'], x['qty'], x['price'], x['is_taker'], r(x.get('mid_before')), x['position_after']) for x in f]}")
    m = [x for x in ev["markout"] if x["ticker"] == tk]
    if m:
        by = defaultdict(list)
        for x in m:
            by[x["horizon"]].append(x["markout"])
        print(f"  markouts: " + ", ".join(f"{h}s mean={100 * st.mean(v):.2f}c n={len(v)}" for h, v in sorted(by.items())))
    tr = [x for x in ev["trade"] if x["ticker"] == tk]
    if tr:
        print(f"  public prints: {len(tr)} contracts={sum(x['qty'] for x in tr)}")
