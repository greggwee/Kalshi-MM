"""Instrumented live run of the A-S market maker on the Kalshi DEMO exchange.

    python demo_run.py --dry-run            # pick a market, print params; no orders
    python demo_run.py --minutes 60         # preflight, then quote for an hour
    python demo_run.py --ticker SOME-TICKER --minutes 30

Three stages:
  1. Preflight: place one 1-lot post_only bid at $0.01 (cannot fill), check the
     bot can see it via get_orders, cancel it. The bot's reconcile loop depends
     on seeing its own resting orders; if it can't, it would stack a fresh pair
     of orders every cycle. Abort here rather than find that out live.
  2. Market selection: open demo markets with a two-sided book, a mid away
     from the extremes, room inside the spread, and 2h-7d to close.
  3. Quote with the unmodified AvellanedaMarketMaker, wrapped to record per-
     cycle model state and order outcomes, then print a shortfall report.

Everything lands in runs/<ticker>_<timestamp>.jsonl for later analysis.
Refuses to touch prod.
"""
import argparse
import json
import logging
import os
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import List, Optional

from dotenv import load_dotenv

from check_demo import make_client
from helper import AvellanedaMarketMaker, TICK
from src.clients import Environment

# ----------------------------------------------------------------------------
# Hyperparameters. The binding constraint is the per-contract reservation
# shift gamma * sigma^2 * horizon: at |q| = max_position it should be about
# one half-spread (~2.5c), so a full inventory moves the exit quote to the
# touch. Larger and the exit quote crosses the book and post_only rejects it
# (the bot goes one-sided exactly when it needs to unload); smaller and
# inventory barely skews quotes.
#   sigma ~0.02 $/sqrt(h) is the middle of the estimator's range on a quiet
#   book (floor 0.01, prior 0.04), and max_horizon_h = 4 says we intend to
#   carry inventory for hours, not a day:
#     5 * gamma * 0.02^2 * 4 = 0.025  ->  gamma ~ 3
# The spread itself is set mostly by k, not gamma: (2/gamma) ln(1 + gamma/k)
# ~ 2/k when gamma << k. k is refit from the live book every 10 cycles, so
# default_k only matters until the first fit.
# ----------------------------------------------------------------------------
PARAMS = dict(
    gamma=3.0,
    default_k=40.0,
    base_order_size=1,             # demo test: smallest unit, clean accounting
    max_position=5,
    order_expiration=120,          # if this process dies, orders die in 2 min
    min_spread=0.02,
    premium_scale=0.02,
    min_time_to_resolution_h=0.25, # stand down 15 min before close
    min_quote_mid=0.10,
    max_quote_mid=0.90,
    max_horizon_h=4.0,
    gamma_mode="constant",
    calibration_constant=0.04,
    sigma_floor=0.01,
    vol_window=120,
    k_refresh_every=10,
    summary_every=20,
)
DT = 3.0


def price(x: dict, name: str) -> Optional[float]:
    v = x.get(f"{name}_dollars")
    if v is not None:
        return float(v)
    v = x.get(name)
    return None if v is None else float(v) / 100.0


def hours_to(iso: Optional[str]) -> Optional[float]:
    if not iso:
        return None
    close = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (close - datetime.now(timezone.utc)).total_seconds() / 3600.0


# ----------------------------------------------------------------------------
# 1. market selection
# ----------------------------------------------------------------------------

def select_markets(client, max_pages: int = 10) -> List[dict]:
    """Open markets ranked by 24h volume, then open interest, keeping only
    ones the model can quote sensibly."""
    candidates, cursor = [], None
    for _ in range(max_pages):
        resp = client.get_markets(status="open", limit=200, cursor=cursor)
        for m in resp.get("markets", []):
            bid, ask = price(m, "yes_bid"), price(m, "yes_ask")
            tth = hours_to(m.get("close_time"))
            if bid is None or ask is None or tth is None:
                continue
            if not (0 < bid < ask < 1) or not (2.0 <= tth <= 168.0):
                continue
            mid = (bid + ask) / 2
            if not (0.20 <= mid <= 0.80) or ask - bid < 3 * TICK - 1e-9:
                continue
            candidates.append({
                "ticker": m["ticker"], "bid": bid, "ask": ask, "mid": mid,
                "tth_h": round(tth, 1),
                "volume_24h": float(m.get("volume_24h_fp", m.get("volume_24h", 0)) or 0),
                "open_interest": float(m.get("open_interest_fp", m.get("open_interest", 0)) or 0),
            })
        cursor = resp.get("cursor")
        if not cursor:
            break
    candidates.sort(key=lambda c: (-c["volume_24h"], -c["open_interest"]))
    return candidates


# ----------------------------------------------------------------------------
# 2. preflight: can the bot see its own resting orders?
# ----------------------------------------------------------------------------

def preflight(client, ticker: str, logger: logging.Logger) -> bool:
    coid = str(uuid.uuid4())
    resp = client.create_order_v2(ticker=ticker, side="bid", count=1, price=0.01,
                                  client_order_id=coid, post_only=True,
                                  expiration_ts=int(time.time()) + 60)
    order_id = resp.get("order_id") or (resp.get("order") or {}).get("order_id")
    logger.info(f"preflight: placed probe bid 1 @ 0.01 (order {order_id})")
    try:
        time.sleep(1.0)
        resting = client.get_orders(ticker=ticker, status="resting").get("orders", [])
        seen = [o for o in resting
                if o.get("order_id") == order_id or o.get("client_order_id") == coid]
        if seen:
            logger.info(f"preflight: probe visible via get_orders "
                        f"(action={seen[0].get('action')}, side={seen[0].get('side')})")
        else:
            logger.error(f"preflight: probe NOT visible via get_orders "
                         f"({len(resting)} resting orders listed). The bot's "
                         f"reconcile loop would stack orders; aborting.")
        return bool(seen)
    finally:
        if order_id:
            client.cancel_order_v2(order_id)
            logger.info("preflight: probe cancelled")


# ----------------------------------------------------------------------------
# 3. instrumented market maker
# ----------------------------------------------------------------------------

class CountingClient:
    """Pass-through client that tallies order calls and their failures."""

    def __init__(self, inner):
        self._inner = inner
        self.calls = Counter()
        self.errors: List[str] = []

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if name not in ("create_order_v2", "cancel_order_v2"):
            return attr

        def wrapped(*args, **kwargs):
            self.calls[name] += 1
            try:
                return attr(*args, **kwargs)
            except Exception as e:
                self.calls[name + "_failed"] += 1
                body = getattr(getattr(e, "response", None), "text", "") or ""
                self.errors.append(f"{name}: {e} {body[:200]}")
                raise
        return wrapped


class InstrumentedMM(AvellanedaMarketMaker):
    """Records what the model wanted, what the book offered, and what the
    exchange did, once per cycle. No change to the strategy itself."""

    def __init__(self, *args, trace_path: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.trace = open(trace_path, "a")
        self.cycle = {}
        self.max_resting_per_side = 0
        self.stacking_cycles = 0
        self.standdowns = Counter()
        self._shutting_down = False

    def cancel_all(self):
        # the stacking guard must never raise here: this is the cleanup path
        self._shutting_down = True
        super().cancel_all()

    def get_resting_orders(self):
        orders = super().get_resting_orders()
        sides = Counter(self._order_book_side(o) for o in orders)
        worst = max(sides.values(), default=0)
        self.max_resting_per_side = max(self.max_resting_per_side, worst)
        if worst > 1:
            self.stacking_cycles += 1
            self.logger.error(f"STACKING: {dict(sides)} resting orders per side")
            if self.stacking_cycles >= 3 and not self._shutting_down:
                raise RuntimeError("orders stacking on one side for 3 cycles; aborting")
        self.cycle = {"resting": dict(sides)}
        return orders

    def get_book_and_mid(self, own_orders=None):
        book, mid = super().get_book_and_mid(own_orders)
        if own_orders is not None:  # the main-loop call, not the final mark
            if mid is None:
                self.standdowns["no usable mid"] += 1
            elif not (self.min_quote_mid <= mid <= self.max_quote_mid):
                self.standdowns["mid out of range"] += 1
            self.cycle.update({
                "ts": time.time(), "mid": mid,
                "others_bid": book.best_yes_bid if book else None,
                "others_ask": book.best_yes_ask if book else None,
            })
        return book, mid

    def compute_quotes(self, mid, q, tth, sigma, k=None):
        quotes = super().compute_quotes(mid, q, tth, sigma, k)
        horizon = min(tth, self.max_horizon_h)
        self.cycle.update({
            "q": q, "tth_h": round(tth, 3), "sigma": sigma, "k": quotes["k"],
            "reservation": quotes["reservation"], "spread": quotes["spread"],
            "bid": quotes["bid"], "ask": quotes["ask"],
            "skew_per_contract": quotes["gamma"] * sigma ** 2 * horizon,
        })
        return quotes

    def manage_orders(self, bid, ask, bid_size, ask_size, resting=None):
        before = Counter(self.client.calls)
        super().manage_orders(bid, ask, bid_size, ask_size, resting=resting)
        delta = Counter(self.client.calls)
        delta.subtract(before)
        self.cycle.update({"bid_size": bid_size, "ask_size": ask_size,
                           "orders": {k: v for k, v in delta.items() if v}})
        self.trace.write(json.dumps(self.cycle) + "\n")
        self.trace.flush()


# ----------------------------------------------------------------------------
# 4. report
# ----------------------------------------------------------------------------

def report(trace_path: str, mm: InstrumentedMM, client: CountingClient) -> None:
    rows = [json.loads(line) for line in open(trace_path)]
    quoted = [r for r in rows if "bid" in r]
    n = len(quoted)
    print("\n" + "=" * 72)
    print(f"SHORTFALL REPORT  {mm.market_ticker}  ({n} quoting cycles)")
    print("=" * 72)
    if mm.standdowns:
        print(f"stood down: {dict(mm.standdowns)}")
    if not n:
        print("No quoting cycles: the market never had a usable mid in range.")
        return

    def position_vs_touch(ours, theirs, side):
        if theirs is None:
            return "alone"
        diff = (theirs - ours) if side == "bid" else (ours - theirs)
        if diff < -TICK / 2:
            return "inside"      # better than everyone else
        if diff < TICK / 2:
            return "joined"
        return "behind"

    for side in ("bid", "ask"):
        placement = Counter(position_vs_touch(r[side], r[f"others_{side}"], side)
                            for r in quoted)
        opposite = "others_ask" if side == "bid" else "others_bid"
        crossed = sum(1 for r in quoted if r[opposite] is not None and (
            r["bid"] >= r[opposite] if side == "bid" else r["ask"] <= r[opposite]))
        print(f"{side:>4}: {dict(placement)}  | would cross book: {crossed}/{n}")

    def rng(key):
        vals = [r[key] for r in quoted if r.get(key) is not None]
        return f"{min(vals):.4f} .. {max(vals):.4f}" if vals else "-"

    creates = client.calls["create_order_v2"]
    print(f"model   sigma {rng('sigma')}  k {rng('k')}  spread {rng('spread')}")
    print(f"        skew/contract {rng('skew_per_contract')}  (target ~0.005)")
    print(f"orders  creates {creates} ({client.calls['create_order_v2_failed']} rejected), "
          f"cancels {client.calls['cancel_order_v2']}, "
          f"creates/cycle {creates / n:.2f}")
    print(f"        max resting per side {mm.max_resting_per_side}, "
          f"stacking cycles {mm.stacking_cycles}")
    print(f"perf    {json.dumps(mm.perf.summary(quoted[-1]['mid']))}")
    for err in client.errors[:5]:
        print(f"  error: {err}")

    print("\nReading it:")
    if mm.perf.fill_count == 0:
        print(" - No fills: nothing to learn about edge or adverse selection. On demo")
        print("   this usually means no counterparties, not a bad model.")
    if client.calls["create_order_v2_failed"]:
        print(" - Rejections: post_only refused crossing quotes, leaving that side")
        print("   empty for the cycle (see brief section 5.1).")
    if creates / n > 0.5:
        print(" - High churn: requoting cancels and replaces, losing queue priority")
        print("   each time. Consider a requote threshold or amend.")
    behind = sum(1 for r in quoted
                 if position_vs_touch(r["bid"], r["others_bid"], "bid") == "behind")
    if behind > n / 2:
        print(" - Mostly behind the touch: the model's spread is wider than the book's;")
        print("   fills only come when the price moves through us (adverse selection).")


# ----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Instrumented demo MM run")
    parser.add_argument("--ticker", help="market to quote (default: auto-select)")
    parser.add_argument("--minutes", type=float, default=60.0)
    parser.add_argument("--dry-run", action="store_true",
                        help="select a market and print params; place nothing")
    args = parser.parse_args()

    load_dotenv()
    client = make_client(Environment.DEMO)   # demo only, by construction
    os.makedirs("runs", exist_ok=True)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s - %(levelname)s - %(message)s",
                        handlers=[logging.StreamHandler()])
    logger = logging.getLogger("demo_run")

    if args.ticker:
        ticker = args.ticker
    else:
        candidates = select_markets(client)
        print(f"\n{len(candidates)} quotable demo markets; top 10:")
        for c in candidates[:10]:
            print(f"  {c['ticker']:40s} {c['bid']:.2f}/{c['ask']:.2f}  "
                  f"tth {c['tth_h']:>6}h  vol24h {c['volume_24h']:g}  "
                  f"OI {c['open_interest']:g}")
        if not candidates:
            sys.exit("No demo market passes the filters; pass --ticker explicitly.")
        ticker = candidates[0]["ticker"]
    print(f"\nMarket: {ticker}\nParams: {json.dumps(PARAMS)}  dt={DT}s  "
          f"runtime={args.minutes:g} min")
    if args.dry_run:
        return

    if not preflight(client, ticker, logger):
        sys.exit(1)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    trace_path = f"runs/{ticker}_{stamp}.jsonl"
    fh = logging.FileHandler(f"runs/{ticker}_{stamp}.log")
    fh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(fh)

    counting = CountingClient(client)
    mm = InstrumentedMM(logger, counting, ticker, trace_path=trace_path,
                        max_runtime=args.minutes * 60,
                        perf_log_path=f"runs/{ticker}_{stamp}_perf.jsonl",
                        **PARAMS)
    try:
        mm.run(DT)          # cancels all resting orders on exit, even on error
    except KeyboardInterrupt:
        logger.info("stopped by user")
    finally:
        mm.trace.close()
        report(trace_path, mm, counting)
        print(f"\nTrace: {trace_path}")


if __name__ == "__main__":
    main()
