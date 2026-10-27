"""Diagnostic report for a trading session, rebuilt from its journal.

    python demo.py report runs/<session_id>

Everything is recomputed from events.jsonl so a report can be regenerated
(or re-analyzed with different horizons) after the fact. Definitions:

  quoting %        share of session time (dt-weighted) with at least one
                   live quote; two-sided % requires both
  at touch         our bid >= best OTHER bid (ask <= best other ask): we are
                   first in line at a fill. 'inside' = strictly better
  edge at fill     (mid at fill - fill price) * direction, $/contract: the
                   half-spread we captured. + = bought below / sold above mid
  markout h        (mid at fill+h - fill price) * direction: edge net of what
                   the market did next. edge - markout = adverse selection
  adverse h        (mid at fill+h - mid at fill) * direction. Negative means
                   the flow that hit us was informed
  spread terms     A-S width = gamma*sigma^2*tau (inventory) +
                   (2/gamma)ln(1+gamma/k) (liquidity) + resolution premium,
                   floored at min_spread. vol scale = sqrt(p(1-p))/0.5, the
                   binary volatility scaling applied to sigma
  market share     our filled contracts / contracts printed on the public tape
"""
import bisect
import json
import math
import os
from collections import Counter, defaultdict
from typing import Dict, List, Optional

from helper import PerformanceTracker
from src.journal import read_journal

TICK = 0.01


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _wmean(pairs):
    pairs = [(x, w) for x, w in pairs if x is not None and w]
    tw = sum(w for _, w in pairs)
    return sum(x * w for x, w in pairs) / tw if tw else None


def _pct(xs, q):
    xs = sorted(x for x in xs if x is not None)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else None


def _corr(xs, ys):
    pts = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pts) < 3:
        return None
    mx = sum(p[0] for p in pts) / len(pts)
    my = sum(p[1] for p in pts) / len(pts)
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    syy = sum((p[1] - my) ** 2 for p in pts)
    if sxx <= 0 or syy <= 0:
        return None
    return sum((p[0] - mx) * (p[1] - my) for p in pts) / math.sqrt(sxx * syy)


def _r(x, n=4):
    return None if x is None else round(x, n)


def load_events(run_dir: str) -> dict:
    path = os.path.join(run_dir, "events.jsonl") if os.path.isdir(run_dir) else run_dir
    ev: Dict[str, list] = defaultdict(list)
    for e in read_journal(path):
        ev[e["type"]].append(e)
    return ev


class MidSeries:
    def __init__(self, ticks: List[dict]):
        pts = [(t["ts"], t["mid"]) for t in ticks if t.get("mid") is not None]
        self.ts = [p[0] for p in pts]
        self.mid = [p[1] for p in pts]

    def at(self, ts: float) -> Optional[float]:
        i = bisect.bisect_right(self.ts, ts) - 1
        return self.mid[i] if i >= 0 else None

    @property
    def last_ts(self):
        return self.ts[-1] if self.ts else None


def _dt_weights(ticks: List[dict], cap: float) -> List[float]:
    out = []
    for i, t in enumerate(ticks):
        nxt = ticks[i + 1]["ts"] if i + 1 < len(ticks) else t["ts"] + cap / 3
        out.append(min(max(nxt - t["ts"], 0.0), cap))
    return out


def market_metrics(ticker: str, ev: dict, horizons=(10, 60, 300), cycle_s: float = 2.0,
                   max_position: Optional[float] = None) -> dict:
    ticks = sorted((t for t in ev["tick"] if t["ticker"] == ticker), key=lambda t: t["ts"])
    orders = [o for o in ev["order"] if o["ticker"] == ticker]
    fills = sorted((f for f in ev["fill"] if f["ticker"] == ticker), key=lambda f: f["fill_ts"])
    trades = [t for t in ev["trade"] if t["ticker"] == ticker]
    meta = next((m for m in ev["market_meta"] if m["ticker"] == ticker), {})
    end = (ev["session_end"][-1]["markets"].get(ticker) if ev["session_end"] else None)
    mids = MidSeries(ticks)
    w = _dt_weights(ticks, cap=3 * cycle_s)
    total_w = sum(w) or 1.0
    out: dict = {"ticker": ticker, "liquidity_class": meta.get("liquidity_class"),
                 "title": (meta.get("meta") or {}).get("title"),
                 "selection": meta.get("selection"), "ticks": len(ticks),
                 "observed_s": round(sum(w), 1)}

    # ---------------- time allocation ----------------
    quoting = [t["state"] == "quoting" and (t.get("bid") is not None or t.get("ask") is not None)
               and (t.get("bid_size", 0) > 0 or t.get("ask_size", 0) > 0) for t in ticks]
    two_sided = [q and t.get("bid") is not None and t.get("ask") is not None
                 and t.get("bid_size", 0) > 0 and t.get("ask_size", 0) > 0
                 for q, t in zip(quoting, ticks)]
    out["quoting_pct"] = _r(100 * sum(x for x, q in zip(w, quoting) if q) / total_w, 1)
    out["two_sided_pct"] = _r(100 * sum(x for x, q in zip(w, two_sided) if q) / total_w, 1)
    reasons = Counter()
    for x, t in zip(w, ticks):
        if t["state"] != "quoting":
            reasons[t.get("reason") or "unknown"] += x
    out["stand_down_pct"] = {k: _r(100 * v / total_w, 1) for k, v in reasons.most_common()}

    # ---------------- the market itself ----------------
    mkt_spreads = [t.get("market_spread") for t in ticks]
    out["market"] = {
        "two_sided_book_pct": _r(100 * sum(x for x, s in zip(w, mkt_spreads) if s is not None)
                                 / total_w, 1),
        "avg_spread_c": _r((_wmean(zip(mkt_spreads, w)) or 0) * 100, 2)
        if any(s is not None for s in mkt_spreads) else None,
        "avg_top_bid_depth": _r(_mean(t.get("bid_depth_top") for t in ticks), 1),
        "avg_top_ask_depth": _r(_mean(t.get("ask_depth_top") for t in ticks), 1),
        "mid_first": mids.mid[0] if mids.mid else None,
        "mid_last": mids.mid[-1] if mids.mid else None,
        "mid_min": min(mids.mid) if mids.mid else None,
        "mid_max": max(mids.mid) if mids.mid else None,
        "mid_changes": sum(1 for a, b in zip(mids.mid, mids.mid[1:]) if abs(a - b) > 1e-9),
        "mid_source_last_price_pct": _r(100 * sum(1 for t in ticks if t.get("mid_source") ==
                                                  "last_price") / max(len(ticks), 1), 1),
    }
    if len(mids.mid) > 2 and mids.ts[-1] > mids.ts[0]:
        hours = (mids.ts[-1] - mids.ts[0]) / 3600.0
        out["market"]["realized_vol_per_sqrt_h"] = _r(math.sqrt(
            sum((b - a) ** 2 for a, b in zip(mids.mid, mids.mid[1:])) / hours), 4)
    out["market"]["public_trades"] = len(trades)
    out["market"]["public_contracts"] = _r(sum(t["qty"] or 0 for t in trades), 2)

    # ---------------- our quotes ----------------
    qt = [(t, x) for t, x, q in zip(ticks, w, quoting) if q]
    def frac(pred):
        tw = sum(x for _, x in qt)
        return _r(100 * sum(x for t, x in qt if pred(t)) / tw, 1) if tw else None
    bid_q = [(t, x) for t, x in qt if t.get("bid") is not None and t.get("bid_size", 0) > 0]
    ask_q = [(t, x) for t, x in qt if t.get("ask") is not None and t.get("ask_size", 0) > 0]

    def touch(side_q, ours, best, better):
        tw = sum(x for _, x in side_q)
        if not tw:
            return None, None, None
        at = sum(x for t, x in side_q if t.get(best) is None or better(t[ours], t[best], True))
        ins = sum(x for t, x in side_q if t.get(best) is None or better(t[ours], t[best], False))
        behind = [abs(t[best] - t[ours]) / TICK for t, _ in side_q
                  if t.get(best) is not None and not better(t[ours], t[best], True)]
        return _r(100 * at / tw, 1), _r(100 * ins / tw, 1), _r(_mean(behind), 2)

    bid_better = lambda ours, best, eq: ours >= best - 1e-9 if eq else ours > best + 1e-9
    ask_better = lambda ours, best, eq: ours <= best + 1e-9 if eq else ours < best - 1e-9
    b_at, b_in, b_behind = touch(bid_q, "bid", "best_bid", bid_better)
    a_at, a_in, a_behind = touch(ask_q, "ask", "best_ask", ask_better)
    both = [(t, x) for t, x in qt if t.get("bid") is not None and t.get("ask") is not None]
    out["quotes"] = {
        "avg_quoted_spread_c": _r(_wmean((100 * (t["ask"] - t["bid"]), x) for t, x in both), 2),
        "avg_model_spread_c": _r(_wmean((100 * t["spread"], x) for t, x in qt
                                        if t.get("spread") is not None), 2),
        "bid_at_touch_pct": b_at, "bid_inside_pct": b_in, "bid_ticks_behind_when_behind": b_behind,
        "ask_at_touch_pct": a_at, "ask_inside_pct": a_in, "ask_ticks_behind_when_behind": a_behind,
        "cross_clamped_pct": frac(lambda t: t.get("bid_crossed") or t.get("ask_crossed")),
        "risk_gated_pct": frac(lambda t: bool(t.get("risk_gate"))),
    }

    # ---------------- model internals ----------------
    def avg(key):
        return _wmean((t.get(key), x) for t, x in qt)
    inv, liq, prem = avg("spread_inv"), avg("spread_liq"), avg("spread_prem")
    raw = (inv or 0) + (liq or 0) + (prem or 0)
    skew = [(t["reservation"] - t["mid"]) if t.get("reservation") is not None else None
            for t, _ in qt]
    out["model"] = {
        "sigma": _r(avg("sigma"), 5), "sigma_raw": _r(avg("sigma_raw"), 5),
        "vol_scale": _r(avg("binary_scale"), 3),
        "sigma_from_data_pct": frac(lambda t: t.get("sigma_from_data")),
        "k": _r(avg("k"), 2), "gamma": _r(avg("gamma"), 3),
        "spread_inv_c": _r(inv * 100 if inv is not None else None, 3),
        "spread_liq_c": _r(liq * 100 if liq is not None else None, 3),
        "spread_prem_c": _r(prem * 100 if prem is not None else None, 3),
        "inv_share_pct": _r(100 * inv / raw, 1) if raw and inv is not None else None,
        "liq_share_pct": _r(100 * liq / raw, 1) if raw and liq is not None else None,
        "prem_share_pct": _r(100 * prem / raw, 1) if raw and prem is not None else None,
        "min_spread_binding_pct": frac(lambda t: t.get("min_spread_binding")),
        "avg_reservation_skew_c": _r((_mean(skew) or 0) * 100, 3) if skew else None,
        "corr_skew_vs_q": _r(_corr(skew, [t.get("q") for t, _ in qt]), 3),
    }

    # ---------------- inventory ----------------
    qs = [t.get("q") or 0.0 for t in ticks]
    out["inventory"] = {
        "start": meta.get("start_position"), "final": qs[-1] if qs else None,
        "max_abs": max((abs(q) for q in qs), default=0.0),
        "avg": _r(_wmean(zip(qs, w)), 3), "avg_abs": _r(_wmean((abs(q), x) for q, x in zip(qs, w)), 3),
        "at_limit_pct": _r(100 * sum(x for q, x in zip(qs, w) if max_position and
                                     abs(q) >= max_position - 1e-9) / total_w, 1),
    }

    # ---------------- orders ----------------
    oc = Counter(f"{o['action']}{'' if o['ok'] else '_failed'}" for o in orders)
    rejects = Counter(o.get("reject_reason") for o in orders if not o["ok"])
    lat = defaultdict(list)
    for o in orders:
        lat[o["action"]].append(o.get("latency_ms"))
    out["orders"] = {
        "counts": dict(oc), "rejects": dict(rejects),
        "reject_examples": [{"action": o["action"], "reason": o.get("reject_reason"),
                             "error": o.get("error")} for o in orders if not o["ok"]][:5],
        "amend_reasons": dict(Counter(o.get("why") for o in orders if o["action"] == "amend")),
        "latency_ms": {a: {"p50": _r(_pct(v, .5), 1), "p95": _r(_pct(v, .95), 1)}
                       for a, v in lat.items()},
        "writes_per_hour": _r(len(orders) / (sum(w) / 3600.0), 1) if sum(w) else None,
    }

    # ---------------- fills ----------------
    rows = []
    for f in fills:
        d = 1.0 if f["side"] == "bid" else -1.0
        m0 = mids.at(f["fill_ts"] - 1e-6)
        if m0 is None:
            m0 = f.get("mid_before")
        row = {"qty": f["qty"], "edge": (m0 - f["price"]) * d if m0 is not None else None,
               "taker": f.get("is_taker"), "fee": f.get("fee") or 0.0}
        for h in horizons:
            t_h = f["fill_ts"] + h
            m_h = mids.at(t_h) if mids.last_ts is not None and t_h <= mids.last_ts else None
            row[f"mo_{h}"] = (m_h - f["price"]) * d if m_h is not None else None
            row[f"as_{h}"] = (m_h - m0) * d if (m_h is not None and m0 is not None) else None
        rows.append(row)
    contracts = sum(r["qty"] for r in rows)
    buy = sum(f["qty"] for f in fills if f["side"] == "bid")
    sell = contracts - buy
    quoting_h = sum(x for x, q in zip(w, quoting) if q) / 3600.0
    out["fills"] = {
        "count": len(fills), "contracts": _r(contracts, 2), "bought": _r(buy, 2),
        "sold": _r(sell, 2), "round_trip_contracts": _r(min(buy, sell), 2),
        "maker_pct": _r(100 * sum(1 for r in rows if not r["taker"]) / len(rows), 1) if rows else None,
        "fees": _r(sum(r["fee"] for r in rows), 4),
        "edge_c": _r(100 * (_wmean((r["edge"], r["qty"]) for r in rows) or 0), 3) if rows else None,
        "fills_per_quoting_hour": _r(len(fills) / quoting_h, 2) if quoting_h else None,
        "contracts_per_quoting_hour": _r(contracts / quoting_h, 2) if quoting_h else None,
        "market_share_pct": _r(100 * contracts / out["market"]["public_contracts"], 1)
        if out["market"]["public_contracts"] else None,
    }
    for h in horizons:
        out["fills"][f"markout_{h}s_c"] = _r(100 * _wmean((r[f"mo_{h}"], r["qty"]) for r in rows), 3) \
            if any(r[f"mo_{h}"] is not None for r in rows) else None
        out["fills"][f"adverse_{h}s_c"] = _r(100 * _wmean((r[f"as_{h}"], r["qty"]) for r in rows), 3) \
            if any(r[f"as_{h}"] is not None for r in rows) else None
        out["fills"][f"markout_{h}s_n"] = sum(1 for r in rows if r[f"mo_{h}"] is not None)

    # ---------------- tape vs our quotes ----------------
    tick_ts = [t["ts"] for t in ticks]
    through = at_level = 0
    for tr in trades:
        i = bisect.bisect_right(tick_ts, tr["trade_ts"]) - 1
        if i < 0 or tr.get("price") is None:
            continue
        t = ticks[i]
        if tr.get("taker_side") == "no" and t.get("bid") is not None and t.get("bid_size", 0) > 0:
            if tr["price"] < t["bid"] - 1e-9:
                through += 1
            elif abs(tr["price"] - t["bid"]) < 1e-9:
                at_level += 1
        if tr.get("taker_side") == "yes" and t.get("ask") is not None and t.get("ask_size", 0) > 0:
            if tr["price"] > t["ask"] + 1e-9:
                through += 1
            elif abs(tr["price"] - t["ask"]) < 1e-9:
                at_level += 1
    out["tape"] = {"prints_through_our_quote": through, "prints_at_our_level": at_level}

    # ---------------- P&L ----------------
    if end:
        out["pnl"] = {k: end.get(k) for k in ("realized_pnl", "unrealized_pnl", "total_pnl",
                                              "fees", "net_pnl", "final_mark",
                                              "exchange_position", "exchange_realized_pnl",
                                              "exchange_fees_paid", "halted")}
    else:  # crashed before session_end: rebuild from fills
        tracker = PerformanceTracker(_NullLogger(), log_path=None)
        tracker.position = meta.get("start_position") or 0.0
        if tracker.position and mids.mid:
            tracker.avg_entry = mids.mid[0]
        for f in fills:
            tracker.on_fill({"book_side": f["side"], "count_fp": f["qty"],
                             "yes_price_dollars": f["price"]}, None)
        s = tracker.summary(mids.mid[-1] if mids.mid else None)
        fees = out["fills"]["fees"] or 0.0
        out["pnl"] = {**{k: s[k] for k in ("realized_pnl", "unrealized_pnl", "total_pnl")},
                      "fees": fees, "net_pnl": round(s["total_pnl"] - fees, 4),
                      "final_mark": mids.mid[-1] if mids.mid else None, "rebuilt": True}
    return out


class _NullLogger:
    def __getattr__(self, name):
        return lambda *a, **k: None


def aggregate(markets: List[dict], horizons) -> dict:
    if not markets:
        return {}
    contracts = sum(m["fills"]["contracts"] or 0 for m in markets)

    def vw(key):
        pairs = [(m["fills"].get(key), m["fills"]["contracts"]) for m in markets]
        v = _wmean(pairs)
        return _r(v, 3)

    agg = {
        "markets": len(markets),
        "fills": sum(m["fills"]["count"] for m in markets),
        "contracts": _r(contracts, 2),
        "net_pnl": _r(sum((m["pnl"].get("net_pnl") or 0) for m in markets), 4),
        "fees": _r(sum((m["pnl"].get("fees") or 0) for m in markets), 4),
        "quoting_pct": _r(_mean(m["quoting_pct"] for m in markets), 1),
        "two_sided_pct": _r(_mean(m["two_sided_pct"] for m in markets), 1),
        "market_spread_c": _r(_mean(m["market"]["avg_spread_c"] for m in markets), 2),
        "quoted_spread_c": _r(_mean(m["quotes"]["avg_quoted_spread_c"] for m in markets), 2),
        "bid_at_touch_pct": _r(_mean(m["quotes"]["bid_at_touch_pct"] for m in markets), 1),
        "ask_at_touch_pct": _r(_mean(m["quotes"]["ask_at_touch_pct"] for m in markets), 1),
        "public_contracts": _r(sum(m["market"]["public_contracts"] or 0 for m in markets), 2),
        "edge_c": vw("edge_c"),
        "writes": sum(sum(m["orders"]["counts"].values()) for m in markets),
        "rejects": sum(sum(m["orders"]["rejects"].values()) for m in markets),
        "max_abs_inventory": max(m["inventory"]["max_abs"] for m in markets),
        "vol_scale": _r(_mean(m["model"]["vol_scale"] for m in markets), 3),
        "min_spread_binding_pct": _r(_mean(m["model"]["min_spread_binding_pct"] for m in markets), 1),
    }
    for h in horizons:
        agg[f"markout_{h}s_c"] = vw(f"markout_{h}s_c")
        agg[f"adverse_{h}s_c"] = vw(f"adverse_{h}s_c")
    agg["market_share_pct"] = (_r(100 * contracts / agg["public_contracts"], 1)
                               if agg["public_contracts"] else None)
    return agg


def findings(report: dict) -> List[dict]:
    """Rule-based observations. Each is (severity, area, text) with the
    numbers that triggered it, so nothing is asserted without evidence."""
    out = []

    def add(sev, area, text):
        out.append({"severity": sev, "area": area, "text": text})

    s = report["session"]
    if s.get("dry_run"):
        add("info", "session", "DRY RUN: no orders reached the exchange; fills and P&L are "
                               "zero by construction. Quote placement metrics are still valid.")
    if s.get("orders_left_resting"):
        add("critical", "safety", f"{s['orders_left_resting']} orders were still resting after "
                                  f"shutdown; verify the account.")
    if s.get("kill_reason"):
        add("high", "risk", f"session kill switch fired: {s['kill_reason']}")
    if s.get("position_mismatches"):
        add("high", "accounting", f"{s['position_mismatches']} tracked-vs-exchange position "
                                  f"mismatches persisted >= 3 cycles (missed or late fills).")
    api = s.get("api", {})
    n429 = sum(e.get("errors", {}).get("429", 0) for e in api.get("endpoints", {}).values())
    if n429:
        add("medium", "infra", f"{n429} requests were rate limited (429) despite client pacing.")
    if s.get("cycle_overrun_pct") and s["cycle_overrun_pct"] > 10:
        add("medium", "infra", f"{s['cycle_overrun_pct']}% of cycles overran cycle_s "
                               f"(p95 {s['cycle_ms_p95']} ms): quotes react slower than configured.")

    for m in report["markets"]:
        t, cls = m["ticker"], m["liquidity_class"]
        q, mo, f = m["quotes"], m["model"], m["fills"]
        if m["quoting_pct"] is not None and m["quoting_pct"] < 50:
            top = next(iter(m["stand_down_pct"]), None)
            add("high", t, f"[{cls}] quoted only {m['quoting_pct']}% of the time; main stand-down "
                           f"reason: {top} ({m['stand_down_pct'].get(top)}%).")
        if f["count"] == 0 and m["quoting_pct"]:
            why = []
            if (q["bid_at_touch_pct"] or 0) < 25 and (q["ask_at_touch_pct"] or 0) < 25:
                why.append(f"quotes rarely at touch (bid {q['bid_at_touch_pct']}%, ask "
                           f"{q['ask_at_touch_pct']}%)")
            if not m["market"]["public_trades"]:
                why.append("no public prints in the market during the session")
            add("medium", t, f"[{cls}] no fills. " + ("; ".join(why) if why else
                             "Market traded, quotes were competitive: check queue position."))
        if (q["avg_quoted_spread_c"] and m["market"]["avg_spread_c"]
                and q["avg_quoted_spread_c"] > 2 * m["market"]["avg_spread_c"]
                and (q["bid_at_touch_pct"] or 0) < 20):
            add("medium", t, f"[{cls}] quoted {q['avg_quoted_spread_c']}c vs market "
                             f"{m['market']['avg_spread_c']}c: the A-S width is not competitive "
                             f"here (k={mo['k']}, gamma={mo['gamma']}).")
        if (mo["min_spread_binding_pct"] or 0) > 80:
            add("info", t, f"[{cls}] min_spread floor bound {mo['min_spread_binding_pct']}% of "
                           f"the time: the model terms (inventory {mo['spread_inv_c']}c + "
                           f"liquidity {mo['spread_liq_c']}c + premium {mo['spread_prem_c']}c) "
                           f"are narrower than the floor, so the floor sets the quote.")
        if (q["cross_clamped_pct"] or 0) > 10:
            add("medium", t, f"[{cls}] the model's quote crossed the market "
                             f"{q['cross_clamped_pct']}% of the time (clamped post-only): the "
                             f"reservation price disagrees with the book.")
        if mo["corr_skew_vs_q"] is not None and mo["corr_skew_vs_q"] > 0.1:
            add("high", t, f"[{cls}] reservation skew is POSITIVELY correlated with inventory "
                           f"({mo['corr_skew_vs_q']}): quotes lean the wrong way.")
        mo60 = f.get("markout_60s_c")
        if mo60 is not None and f["count"] >= 3 and f["edge_c"] is not None:
            if mo60 < 0:
                add("high", t, f"[{cls}] 60s markout {mo60}c/contract on {f['count']} fills "
                               f"(edge at fill {f['edge_c']}c): adverse selection exceeded the "
                               f"spread captured.")
            elif mo60 < f["edge_c"] / 2:
                add("medium", t, f"[{cls}] 60s markout {mo60}c vs edge {f['edge_c']}c: more than "
                                 f"half of the captured spread was given back.")
        if m["inventory"]["at_limit_pct"] and m["inventory"]["at_limit_pct"] > 10:
            add("medium", t, f"[{cls}] at max_position {m['inventory']['at_limit_pct']}% of the "
                             f"time (quoting one side only).")
        rej = sum(m["orders"]["rejects"].values())
        writes = sum(m["orders"]["counts"].values())
        if writes and rej / writes > 0.05:
            add("medium", t, f"[{cls}] {rej}/{writes} order writes failed: "
                             f"{m['orders']['rejects']}.")
        if m["tape"]["prints_through_our_quote"]:
            add("info", t, f"[{cls}] {m['tape']['prints_through_our_quote']} public prints "
                           f"traded through our quoted price without filling us (approximate: "
                           f"quote state sampled once per cycle).")
    order = {"critical": 0, "high": 1, "medium": 2, "info": 3}
    out.sort(key=lambda x: order.get(x["severity"], 9))
    return out


def build_report(run_dir: str, horizons=None) -> dict:
    ev = load_events(run_dir)
    start = ev["session_start"][0] if ev["session_start"] else {}
    end = ev["session_end"][-1] if ev["session_end"] else {}
    cfg = start.get("config") or {}
    session_cfg = start.get("session_effective") or cfg.get("session") or {}
    horizons = tuple(horizons or session_cfg.get("markout_horizons") or (10, 60, 300))
    cycle_s = session_cfg.get("cycle_s", 2.0)
    strategy_eff = start.get("strategy_effective") or {}
    tickers = start.get("tickers") or sorted({t["ticker"] for t in ev["tick"]})

    markets = [market_metrics(t, ev, horizons, cycle_s,
                              (strategy_eff.get(t) or {}).get("max_position"))
               for t in tickers]
    cycles_ms = [c["ms"] for c in ev["cycle"]]
    api = end.get("api_stats") or (ev["api_stats"][-1]["stats"] if ev["api_stats"] else {})
    report = {
        "run_dir": run_dir,
        "session": {
            "start_ts": start.get("ts"), "end_ts": end.get("ts"),
            "duration_s": end.get("duration_s"), "cycles": end.get("cycles", len(ev["cycle"])),
            "end_reason": end.get("reason", "no session_end (crashed or still running)"),
            "dry_run": start.get("dry_run"), "environment": cfg.get("environment"),
            "kill_reason": end.get("kill_reason"), "halted": end.get("halted"),
            "orders_left_resting": end.get("orders_left_resting"),
            "start_balance": start.get("balance"), "end_balance": end.get("end_balance"),
            "cycle_ms_p50": _r(_pct(cycles_ms, .5), 1), "cycle_ms_p95": _r(_pct(cycles_ms, .95), 1),
            "cycle_overrun_pct": _r(100 * sum(1 for c in cycles_ms if c > cycle_s * 1000)
                                    / len(cycles_ms), 1) if cycles_ms else None,
            "cycle_errors": len(ev["cycle_error"]),
            "api_errors": dict(Counter(f"{e.get('endpoint')} {e.get('status')}"
                                       for e in ev["api_error"])),
            "position_mismatches": len(ev["position_mismatch"]),
            "risk_events": [{k: e.get(k) for k in ("scope", "reason", "ticker", "ts")}
                            for e in ev["risk"]],
            "api": api,
            "strategy": next(iter(strategy_eff.values()), None) or cfg.get("strategy"),
            "strategy_overrides": cfg.get("strategy_overrides"),
            "execution": start.get("execution_effective") or cfg.get("execution"),
            "risk_limits": start.get("risk_effective") or cfg.get("risk"),
        },
        "horizons": list(horizons),
        "markets": markets,
        "classes": {cls: aggregate([m for m in markets if m["liquidity_class"] == cls], horizons)
                    for cls in sorted({m["liquidity_class"] for m in markets if m["liquidity_class"]})},
        "total": aggregate(markets, horizons),
    }
    report["findings"] = findings(report)
    return report


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def _f(x, suffix="", nd=None):
    if x is None:
        return "-"
    if isinstance(x, float) and nd is not None:
        return f"{x:.{nd}f}{suffix}"
    return f"{x}{suffix}"


def _table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(lines)


def render_markdown(r: dict) -> str:
    s, H = r["session"], r["horizons"]
    hmid = H[1] if len(H) > 1 else H[0]
    L = []
    L.append("# Market-making diagnostic report")
    L.append("")
    L.append(f"- Run: `{r['run_dir']}`  \n- Environment: **{s.get('environment')}**"
             f"{' (DRY RUN)' if s.get('dry_run') else ''}  \n- Duration: "
             f"{_f(s.get('duration_s'))} s over {s.get('cycles')} cycles  \n- End reason: "
             f"{s.get('end_reason')}  \n- Orders left resting after shutdown: "
             f"{_f(s.get('orders_left_resting'))}")
    sb, eb = s.get("start_balance") or {}, s.get("end_balance") or {}
    if sb or eb:
        L.append(f"- Balance: {_f(sb.get('balance'))} -> {_f(eb.get('balance'))} "
                 f"(portfolio value {_f(sb.get('portfolio_value'))} -> "
                 f"{_f(eb.get('portfolio_value'))})")
    L.append("")
    tot = r["total"]
    if tot:
        L.append("## Headline")
        L.append("")
        L.append(f"**{tot['fills']} fills / {tot['contracts']} contracts** across "
                 f"{tot['markets']} markets, **net P&L ${tot['net_pnl']:+.4f}** after "
                 f"${tot['fees']:.4f} fees. Edge at fill {_f(tot['edge_c'], 'c')}/contract, "
                 f"{hmid}s markout {_f(tot.get(f'markout_{hmid}s_c'), 'c')}/contract.")
        L.append("")

    L.append("## Liquid vs illiquid")
    L.append("")
    rows = []
    metrics = [("markets", "markets"), ("quoting % of time", "quoting_pct"),
               ("two-sided %", "two_sided_pct"), ("market spread (c)", "market_spread_c"),
               ("our quoted spread (c)", "quoted_spread_c"), ("bid at touch %", "bid_at_touch_pct"),
               ("ask at touch %", "ask_at_touch_pct"), ("public contracts printed", "public_contracts"),
               ("fills", "fills"), ("contracts filled", "contracts"),
               ("market share %", "market_share_pct"), ("edge at fill (c/ct)", "edge_c")]
    metrics += [(f"markout {h}s (c/ct)", f"markout_{h}s_c") for h in H]
    metrics += [(f"adverse sel. {h}s (c/ct)", f"adverse_{h}s_c") for h in H]
    metrics += [("net P&L ($)", "net_pnl"), ("fees ($)", "fees"), ("order writes", "writes"),
                ("failed writes", "rejects"), ("max abs inventory", "max_abs_inventory"),
                ("avg vol scale sqrt(p(1-p))/0.5", "vol_scale"),
                ("min_spread floor binding %", "min_spread_binding_pct")]
    classes = list(r["classes"])
    for label, key in metrics:
        rows.append([label] + [_f(r["classes"][c].get(key)) for c in classes]
                    + [_f(tot.get(key))])
    L.append(_table(["metric"] + classes + ["all"], rows))
    L.append("")

    L.append("## Findings")
    L.append("")
    if r["findings"]:
        for f in r["findings"]:
            L.append(f"- **{f['severity'].upper()}** ({f['area']}): {f['text']}")
    else:
        L.append("- No rule-based flags fired.")
    L.append("")

    L.append("## Per-market detail")
    for m in r["markets"]:
        sel, mk, q, mo, inv, fl, o, p = (m.get("selection") or {}, m["market"], m["quotes"],
                                         m["model"], m["inventory"], m["fills"], m["orders"],
                                         m["pnl"])
        L.append("")
        L.append(f"### {m['ticker']} ({m['liquidity_class']})")
        if m.get("title"):
            L.append(f"_{m['title']}_")
        L.append("")
        L.append(f"- Selection: mid {_f(sel.get('mid'))}, spread {_f(sel.get('spread'))}, "
                 f"24h vol {_f(sel.get('volume_24h'))}, OI {_f(sel.get('open_interest'))}, "
                 f"depth +-5c {_f(sel.get('depth_band_contracts'))}, closes in "
                 f"{_f(sel.get('hours_to_close'))} h, fee_type {_f(sel.get('fee_type'))}")
        L.append(f"- Time: quoting {_f(m['quoting_pct'], '%')}, two-sided "
                 f"{_f(m['two_sided_pct'], '%')}; stood down: "
                 f"{', '.join(f'{k} {v}%' for k, v in m['stand_down_pct'].items()) or 'never'}")
        L.append(f"- Market: book two-sided {_f(mk['two_sided_book_pct'], '%')}, avg spread "
                 f"{_f(mk['avg_spread_c'], 'c')}, top depth bid/ask "
                 f"{_f(mk['avg_top_bid_depth'])}/{_f(mk['avg_top_ask_depth'])}, mid "
                 f"{_f(mk['mid_first'])} -> {_f(mk['mid_last'])} (range {_f(mk['mid_min'])}-"
                 f"{_f(mk['mid_max'])}, {mk['mid_changes']} changes, realized vol "
                 f"{_f(mk.get('realized_vol_per_sqrt_h'))}/sqrt h), {mk['public_trades']} public "
                 f"prints / {_f(mk['public_contracts'])} contracts")
        L.append(f"- Quotes: quoted spread {_f(q['avg_quoted_spread_c'], 'c')} (model "
                 f"{_f(q['avg_model_spread_c'], 'c')}); bid at touch {_f(q['bid_at_touch_pct'], '%')} "
                 f"(inside {_f(q['bid_inside_pct'], '%')}, {_f(q['bid_ticks_behind_when_behind'])} "
                 f"ticks behind otherwise); ask at touch {_f(q['ask_at_touch_pct'], '%')} (inside "
                 f"{_f(q['ask_inside_pct'], '%')}, {_f(q['ask_ticks_behind_when_behind'])} ticks "
                 f"behind); crossed->clamped {_f(q['cross_clamped_pct'], '%')}; risk-gated "
                 f"{_f(q['risk_gated_pct'], '%')}")
        L.append(f"- Model: sigma {_f(mo['sigma'])} (raw {_f(mo['sigma_raw'])} x vol scale "
                 f"{_f(mo['vol_scale'])}; from data {_f(mo['sigma_from_data_pct'], '%')} of the "
                 f"time), k {_f(mo['k'])}, gamma {_f(mo['gamma'])}; width terms inventory "
                 f"{_f(mo['spread_inv_c'], 'c')} / liquidity {_f(mo['spread_liq_c'], 'c')} / "
                 f"premium {_f(mo['spread_prem_c'], 'c')} (shares {_f(mo['inv_share_pct'], '%')}/"
                 f"{_f(mo['liq_share_pct'], '%')}/{_f(mo['prem_share_pct'], '%')}); floor binding "
                 f"{_f(mo['min_spread_binding_pct'], '%')}; reservation skew "
                 f"{_f(mo['avg_reservation_skew_c'], 'c')} avg, corr with q "
                 f"{_f(mo['corr_skew_vs_q'])}")
        L.append(f"- Inventory: start {_f(inv['start'])}, final {_f(inv['final'])}, max |q| "
                 f"{_f(inv['max_abs'])}, avg {_f(inv['avg'])} (avg |q| {_f(inv['avg_abs'])}), "
                 f"at limit {_f(inv['at_limit_pct'], '%')}")
        mo_txt = ", ".join(f"{h}s {_f(fl.get(f'markout_{h}s_c'), 'c')} (adverse "
                           f"{_f(fl.get(f'adverse_{h}s_c'), 'c')}, n={fl.get(f'markout_{h}s_n')})"
                           for h in H)
        L.append(f"- Fills: {fl['count']} ({_f(fl['contracts'])} ct: bought {_f(fl['bought'])}, "
                 f"sold {_f(fl['sold'])}, round-trip {_f(fl['round_trip_contracts'])}), maker "
                 f"{_f(fl['maker_pct'], '%')}, {_f(fl['fills_per_quoting_hour'])}/quoting-hour, "
                 f"market share {_f(fl['market_share_pct'], '%')}; edge at fill "
                 f"{_f(fl['edge_c'], 'c')}; markouts: {mo_txt}")
        L.append(f"- Orders: {o['counts']} rejects {o['rejects'] or 'none'}; amend reasons "
                 f"{o['amend_reasons'] or '-'}; {_f(o['writes_per_hour'])} writes/h; latency "
                 + ", ".join(f"{a} p50 {v['p50']}ms p95 {v['p95']}ms"
                             for a, v in o["latency_ms"].items()))
        for ex in o.get("reject_examples", []):
            L.append(f"  - reject `{ex['action']}` {ex['reason']}: {ex['error']}")
        L.append(f"- Tape vs quotes: {m['tape']['prints_through_our_quote']} prints through our "
                 f"quote, {m['tape']['prints_at_our_level']} at our level")
        L.append(f"- P&L: realized {_f(p.get('realized_pnl'))}, unrealized "
                 f"{_f(p.get('unrealized_pnl'))} (mark {_f(p.get('final_mark'))}), fees "
                 f"{_f(p.get('fees'))}, **net {_f(p.get('net_pnl'))}**; exchange position "
                 f"{_f(p.get('exchange_position'))}"
                 + (" (rebuilt from fills)" if p.get("rebuilt") else ""))

    L.append("")
    L.append("## Infrastructure")
    L.append("")
    L.append(f"- Cycle time p50 {_f(s['cycle_ms_p50'])} ms, p95 {_f(s['cycle_ms_p95'])} ms, "
             f"overrun {_f(s['cycle_overrun_pct'], '%')}; cycle errors {s['cycle_errors']}; "
             f"position mismatches {s['position_mismatches']}")
    api = s.get("api") or {}
    if api.get("endpoints"):
        L.append(f"- API: {api.get('total_requests')} requests, {api.get('total_errors')} errors, "
                 f"{api.get('retries')} retries, {api.get('throttle_wait_s')} s spent pacing")
        L.append("")
        L.append(_table(["endpoint", "count", "errors", "p50 ms", "p95 ms", "max ms"],
                        [[k, v["count"], v["errors"] or "-", _f(v["p50_ms"]), _f(v["p95_ms"]),
                          _f(v["max_ms"])] for k, v in sorted(api["endpoints"].items())]))
    if s["risk_events"]:
        L.append("")
        L.append("Risk events:")
        for e in s["risk_events"]:
            L.append(f"- {e['scope']} {e.get('ticker') or ''}: {e['reason']}")
    L.append("")
    L.append("## Configuration")
    L.append("")
    L.append("```json")
    L.append(json.dumps({"strategy": s.get("strategy"),
                         "strategy_overrides": s.get("strategy_overrides"),
                         "execution": s.get("execution"), "risk": s.get("risk_limits")},
                        indent=2, default=str))
    L.append("```")
    L.append("")
    L.append("Definitions: see the docstring of `src/diagnostics.py`. Markouts use the mid "
             "net of our own orders, sampled once per cycle.")
    return "\n".join(L) + "\n"


def write_report(run_dir: str) -> dict:
    report = build_report(run_dir)
    with open(os.path.join(run_dir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    with open(os.path.join(run_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write(render_markdown(report))
    return report
