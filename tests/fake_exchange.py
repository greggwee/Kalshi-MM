"""In-memory exchange implementing the ExchangeGateway interface, for tests.

Models just enough of Kalshi to exercise the session engine end to end:
other participants' resting book (yes bids / no bids), our post-only orders
(rejected if they would cross), V2 amend semantics (count = filled +
remaining), positions, fills with fees, a public tape, and a hook for a
taker to hit our resting orders. Prices are yes-axis dollars.

listing_lag_s models the real order listing trailing writes (~0.5-1s on
demo): resting_orders() shows the orders as they were listing_lag_s ago, so
new orders are missing, cancelled/filled ones linger and amends show the
old price until the lag has passed.
"""
import itertools
from collections import defaultdict
from typing import Dict, List

from src.clients import KalshiAPIError


class FakeExchange:
    def __init__(self, clock):
        self.clock = clock
        self.mkts: Dict[str, dict] = {}
        self.books: Dict[str, Dict[str, Dict[float, float]]] = {}
        self.orders: Dict[str, dict] = {}
        self.pos: Dict[str, float] = defaultdict(float)
        self.fills: List[dict] = []
        self.trades: List[dict] = []
        self.calls = defaultdict(int)
        self.trading_active = True
        self.fail_reads = 0
        self.fee_per_contract = 0.0
        self.listing_lag_s = 0.0
        self._ghosts: Dict[str, tuple] = {}  # gone orders the lagging listing still shows
        self._ids = itertools.count(1)

    # ---------------- test setup ----------------

    def add_market(self, ticker, yes_bids, no_bids, close_in_h=48.0, event=None,
                   volume_24h=0.0, open_interest=0.0, last_price=None):
        self.mkts[ticker] = {
            "ticker": ticker, "event_ticker": event or f"EV-{ticker}",
            "series_ticker": "FAKE", "title": f"Fake {ticker}", "status": "active",
            "tradable": True, "close_ts": self.clock() + close_in_h * 3600,
            "yes_bid": max(yes_bids) if yes_bids else None,
            "yes_ask": round(1 - max(no_bids), 4) if no_bids else None,
            "bid_size": 0.0, "ask_size": 0.0, "last_price": last_price,
            "volume": volume_24h, "volume_24h": volume_24h, "open_interest": open_interest,
            "max_tick": 0.01, "mve": False,
        }
        self.set_book(ticker, yes_bids, no_bids)

    def set_book(self, ticker, yes_bids: Dict[float, float], no_bids: Dict[float, float]):
        self.books[ticker] = {"yes": dict(yes_bids), "no": dict(no_bids)}

    def taker_hits(self, ticker: str, side: str, qty: float) -> float:
        """A taker trades against OUR resting order on `side`. Returns qty filled."""
        for o in self.orders.values():
            if o["ticker"] == ticker and o["side"] == side and o["remaining"] > 0:
                q = min(qty, o["remaining"])
                o["remaining"] -= q
                o["filled"] += q
                self.pos[ticker] += q if side == "bid" else -q
                fid = f"f{next(self._ids)}"
                now = self.clock()
                self.fills.append({"fill_id": fid, "trade_id": f"t{fid}", "order_id": o["order_id"],
                                   "ticker": ticker, "side": side, "qty": q, "price": o["price"],
                                   "is_taker": False, "fee": self.fee_per_contract * q, "ts": now})
                self.trades.append({"trade_id": f"t{fid}", "ticker": ticker, "price": o["price"],
                                    "qty": q, "taker_side": "no" if side == "bid" else "yes",
                                    "ts": now})
                if o["remaining"] <= 1e-9:
                    self._ghosts[o["order_id"]] = (dict(o, remaining=o["remaining"] + q,
                                                        filled=o["filled"] - q), now)
                    del self.orders[o["order_id"]]
                return q
        return 0.0

    def our_orders(self, ticker=None) -> List[dict]:
        return [_public(o) for o in self.orders.values() if ticker in (None, o["ticker"])]

    # ---------------- gateway interface: reads ----------------

    def _read(self, name):
        self.calls[name] += 1
        if self.fail_reads > 0:
            self.fail_reads -= 1
            raise ConnectionError("simulated outage")

    def exchange_status(self):
        self.calls["exchange_status"] += 1
        return {"exchange_active": True, "trading_active": self.trading_active}

    def balance(self):
        self.calls["balance"] += 1
        return {"balance": 1000.0, "portfolio_value": 0.0}

    def markets(self, tickers):
        self.calls["markets"] += 1
        return {t: dict(self.mkts[t]) for t in tickers if t in self.mkts}

    def list_open_markets(self, min_close_ts=None, max_close_ts=None, max_pages=10,
                          exclude_multivariate=True):
        self.calls["list_open_markets"] += 1
        return [dict(m) for m in self.mkts.values()]

    def series_info(self, series):
        return {"fee_type": "quadratic", "fee_multiplier": 1.0}

    def orderbooks(self, tickers):
        self._read("orderbooks")
        out = {}
        for t in tickers:
            if t not in self.books:
                continue
            yes = defaultdict(float, self.books[t]["yes"])
            no = defaultdict(float, self.books[t]["no"])
            for o in self.orders.values():  # the real book includes our orders
                if o["ticker"] != t:
                    continue
                if o["side"] == "bid":
                    yes[round(o["price"], 4)] += o["remaining"]
                else:
                    no[round(1 - o["price"], 4)] += o["remaining"]
            out[t] = {"yes_dollars": [[f"{p:.4f}", f"{c:.2f}"] for p, c in sorted(yes.items())],
                      "no_dollars": [[f"{p:.4f}", f"{c:.2f}"] for p, c in sorted(no.items())]}
        return out

    def resting_orders(self, tickers):
        self._read("resting_orders")
        now, lag = self.clock(), self.listing_lag_s
        rows = []
        for o in self.orders.values():
            if now - o["created_ts"] < lag:
                continue                       # not listed yet
            prev = o.get("_prev")
            rows.append(prev[0] if prev and now - prev[1] < lag else o)
        rows += [g for g, gone_ts in self._ghosts.values()
                 if now - gone_ts < lag and now - g["created_ts"] >= lag]
        out = {t: [] for t in tickers}
        for o in rows:
            if o["ticker"] in out:
                out[o["ticker"]].append(_public(o))
        return out

    def positions(self, tickers):
        self._read("positions")
        return {t: {"ticker": t, "position": self.pos[t], "realized_pnl": 0.0,
                    "fees_paid": 0.0, "exposure": 0.0} for t in tickers if t in self.pos}

    def fills_since(self, min_ts):
        self._read("fills")
        return [dict(f) for f in self.fills if f["ts"] >= min_ts]

    def trades_since(self, ticker, min_ts, limit=1000):
        self.calls["trades"] += 1
        return [dict(t) for t in self.trades if t["ticker"] == ticker and t["ts"] >= min_ts]

    def api_stats(self):
        return {"endpoints": {}, "total_requests": sum(self.calls.values()), "total_errors": 0,
                "retries": 0, "throttle_wait_s": 0.0}

    # ---------------- gateway interface: writes ----------------

    def _best_other(self, ticker):
        b = self.books[ticker]
        bid = max(b["yes"]) if b["yes"] else None
        ask = round(1 - max(b["no"]), 4) if b["no"] else None
        return bid, ask

    def create_order(self, ticker, side, count, price, expiration_ts=None, post_only=True,
                     **_):
        self.calls["create"] += 1
        bid, ask = self._best_other(ticker)
        for o in self.orders.values():   # a post-only order cannot cross us either
            if o["ticker"] == ticker and o["side"] == "bid":
                bid = o["price"] if bid is None else max(bid, o["price"])
            if o["ticker"] == ticker and o["side"] == "ask":
                ask = o["price"] if ask is None else min(ask, o["price"])
        if post_only and ((side == "bid" and ask is not None and price >= ask - 1e-9) or
                          (side == "ask" and bid is not None and price <= bid + 1e-9)):
            raise KalshiAPIError(400, "POST", "/portfolio/events/orders",
                                 code="post_only_cross", message="post only order would cross")
        oid = f"o{next(self._ids)}"
        self.orders[oid] = {"order_id": oid, "client_order_id": None, "ticker": ticker,
                            "side": side, "price": round(price, 4), "remaining": float(count),
                            "filled": 0.0, "initial": float(count), "status": "resting",
                            "expiration_ts": expiration_ts, "created_ts": self.clock()}
        return {"order_id": oid, "fill_count": 0.0, "remaining_count": float(count)}

    def amend_order(self, order_id, ticker, side, count, price, expiration_ts=None):
        self.calls["amend"] += 1
        o = self.orders.get(order_id)
        if o is None:
            raise KalshiAPIError(404, "POST", f"/portfolio/events/orders/{order_id}/amend",
                                 code="not_found", message="order not found")
        o["_prev"] = (_public(o), self.clock())
        o["price"] = round(price, 4)
        o["remaining"] = max(0.0, count - o["filled"])
        if expiration_ts:
            o["expiration_ts"] = expiration_ts
        return {"order_id": order_id, "remaining_count": o["remaining"]}

    def cancel_order(self, order_id, ticker=None):
        self.calls["cancel"] += 1
        o = self.orders.pop(order_id, None)
        if o is None:
            raise KalshiAPIError(404, "DELETE", f"/portfolio/events/orders/{order_id}",
                                 code="not_found", message="order not found")
        self._ghosts[order_id] = (_public(o), self.clock())
        return {"order_id": order_id, "reduced_by": o["remaining"]}


def _public(order: dict) -> dict:
    return {k: v for k, v in order.items() if not k.startswith("_")}


class FakeClock:
    def __init__(self, start=1_800_000_000.0):
        self.t = start

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += max(s, 0.0)

    def advance(self, s):
        self.t += s
