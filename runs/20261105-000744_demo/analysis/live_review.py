"""Review the live demo runs, and confirm with the exchange that nothing is left resting."""
import collections, glob, json, os, statistics as st, sys
from dotenv import load_dotenv
ROOT = r"C:\Users\grego\Documents\Github\Kalshi-MM"
WT = r"C:\Users\grego\AppData\Local\Temp\claude\840c593829146935\8552379b-feeb-4475-830b-aaa408586bd7\scratchpad\wt"
sys.path.insert(0, WT)
os.chdir(ROOT)
load_dotenv(os.path.join(ROOT, ".env"))
from src.clients import Environment
from src.credentials import make_client
from src.gateway import ExchangeGateway

runs = sorted(glob.glob(os.path.join("runs", "20261105-*_demo")))
for run in runs:
    ev = []
    for line in open(os.path.join(run, "events.jsonl"), encoding="utf-8"):
        try:
            ev.append(json.loads(line))
        except Exception:
            pass
    c = collections.Counter(e["type"] for e in ev)
    end = next((e for e in ev if e["type"] == "session_end"), {})
    print("==", run, {k: c[k] for k in ("cycle", "fill", "order", "trade", "pmm_solve", "position_mismatch", "risk", "cycle_error", "api_error")})
    print("  end", end.get("reason"), "duration", end.get("duration_s"), "left", end.get("orders_left_resting"), "verified", end.get("orders_left_verified"), "total", end.get("total"))
    print("  errors", collections.Counter(e.get("code") for e in ev if e["type"] == "api_error"))
    o = [e for e in ev if e["type"] == "order"]
    print("  writes", collections.Counter((e.get("action"), e.get("ok")) for e in o))
    cyc = [e["ms"] for e in ev if e["type"] == "cycle"]
    print("  cycle ms p50 %.0f p99 %.0f max %.0f; solve ms max %.0f" % (st.median(cyc), sorted(cyc)[int(.99 * (len(cyc) - 1))], max(cyc), max(e["ms"] for e in ev if e["type"] == "pmm_solve")))
    ticks = [e for e in ev if e["type"] == "tick"]
    for t in sorted({e["ticker"] for e in ticks}):
        tt = [e for e in ticks if e["ticker"] == t]
        q = [e for e in tt if e.get("state") == "quoting"]
        both = [e for e in q if e.get("bid") is not None and e.get("ask") is not None]
        crossed = sum(1 for e in both if e["bid"] >= e["ask"])
        bq = [e for e in q if e.get("bid") is not None and e.get("best_bid") is not None]
        aq = [e for e in q if e.get("ask") is not None and e.get("best_ask") is not None]
        at_b = 100 * sum(1 for e in bq if e["bid"] >= e["best_bid"] - 1e-9) / max(len(bq), 1)
        at_a = 100 * sum(1 for e in aq if e["ask"] <= e["best_ask"] + 1e-9) / max(len(aq), 1)
        sp = [100 * (e["ask"] - e["bid"]) for e in both]
        msp = [e["market_spread"] * 100 for e in q if e.get("market_spread") is not None]
        pts = [(e["reservation"] - e["mid"], e["q"]) for e in q if e.get("reservation") is not None]
        corr = None
        if len({p[1] for p in pts}) > 1:
            a = [p[0] for p in pts]
            b = [p[1] for p in pts]
            ma, mb = st.mean(a), st.mean(b)
            sa, sb = st.pstdev(a), st.pstdev(b)
            corr = round(sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (len(a) * sa * sb), 3) if sa and sb else None
        fills = [e for e in ev if e["type"] == "fill" and e["ticker"] == t]
        prints = [e for e in ev if e["type"] == "trade" and e["ticker"] == t]
        m = (end.get("markets") or {}).get(t, {})
        reasons = collections.Counter(e.get("reason") for e in tt if e.get("state") != "quoting")
        print(f"  {t[:38]:38s} quoting {100 * len(q) / max(len(tt), 1):3.0f}% crossed {crossed} touch b/a {at_b:3.0f}/{at_a:3.0f}% spread {st.mean(sp) if sp else 0:4.1f}c vs mkt {st.mean(msp) if msp else 0:4.1f}c "
              f"prints {len(prints):3d} fills {len(fills):2d} maxq {max(abs(e.get('q') or 0) for e in tt):5.2f} corr(skew,q) {corr} net {m.get('net_pnl')} {dict(reasons) if reasons else ''}")
    mk = [e for e in ev if e["type"] == "markout"]
    for h in (10, 60, 300):
        v = [e["markout"] for e in mk if e["horizon"] == h]
        if v:
            print(f"  markout {h}s: n {len(v)} mean {100 * st.mean(v):+.2f}c")
    for f in [e for e in ev if e["type"] == "fill"][:8]:
        print("   fill", f["ticker"][:30], f["side"], f["qty"], "@", f["price"], "mid", f.get("mid_before"), "res", f.get("reservation"), "q", f.get("position_before"), "->", f.get("position_after"), "fee", f.get("fee"))

gw = ExchangeGateway(make_client(Environment.DEMO))
acct = gw.client.get_all_orders(status="resting")
print("\nresting on the demo account now:", len(acct))
print("balance", gw.balance())
