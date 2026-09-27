"""Shadow trading / backtest engine.

Runs the AvellanedaMarketMaker quoting math against recorded market data (the
SQLite DB written by the recorder) and simulates fills locally — no exchange
account needed. Two modes share one engine:

  replay  - iterate a ticker's recorded history (backtest)
  follow  - tail the DB the live recorder is writing (live shadow trading)

Fill model (deliberately pessimistic — shadow fills should UNDERSTATE reality):
  * A resting bid fills only when the tape prints THROUGH it (trade price
    strictly below our bid), or prints AT our price after the displayed queue
    that was ahead of us at placement has been consumed by prints.
  * taker_side gates fills when present: only sell-pressure prints (taker
    bought NO) can fill bids, only buy-pressure prints can fill asks.
  * Repricing resets queue position, as it would on the real exchange.
  * 'strict' mode disables at-price queue fills entirely.

Remaining optimism this model cannot remove: our hypothetical quotes might
have changed other participants' behavior, and a resting quote tightening the
spread attracts flow that the recorded tape doesn't show. Treat results as an
upper bound on edge only after they survive the pessimistic settings.
"""
import json
import logging
import math
import sqlite3
import time
from datetime import datetime, timezone
from typing import Dict, Iterator, List, Optional, Tuple

from helper import AvellanedaMarketMaker, PerformanceTracker, estimate_k_from_depth, TICK
from src.orderbook import OrderBook

LAST_TRADE_MID_MAX_AGE_S = 1800  # one-sided-book fallback: trust last trade for 30 min


class SimOrder:
    def __init__(self, side: str, price: float, size: float, queue_ahead: float, ts_ms: int):
        self.side = side              # 'bid' | 'ask' (yes-axis)
        self.price = price
        self.remaining = size
        self.queue_ahead = queue_ahead
        self.ts_ms = ts_ms


class FillSimulator:
    """At most one simulated resting order per side, mirroring the MM."""

    def __init__(self, mode: str = "queue"):
        assert mode in ("queue", "strict")
        self.mode = mode
        self.orders: Dict[str, Optional[SimOrder]] = {"bid": None, "ask": None}

    def place(self, side: str, price: float, size: float, book: OrderBook, ts_ms: int):
        # queue ahead = everyone already displayed at our level (book excludes
        # us: our shadow orders are not really on the exchange)
        if side == "bid":
            queue = book.yes.get(round(price, 4), 0.0)
        else:
            queue = book.no.get(round(1.0 - price, 4), 0.0)
        self.orders[side] = SimOrder(side, price, size, queue, ts_ms)

    def cancel(self, side: str):
        self.orders[side] = None

    def on_snapshot(self, book: OrderBook):
        """Queue ahead can only shrink to the displayed size at our level:
        anyone remaining there might still be ahead of us, but no more than
        that many contracts can be."""
        bid = self.orders["bid"]
        if bid:
            bid.queue_ahead = min(bid.queue_ahead, book.yes.get(round(bid.price, 4), 0.0))
        ask = self.orders["ask"]
        if ask:
            ask.queue_ahead = min(ask.queue_ahead,
                                  book.no.get(round(1.0 - ask.price, 4), 0.0))

    def on_trade(self, trade: dict) -> List[Tuple[str, float, float]]:
        """Returns [(side, price, filled_qty), ...]. trade has yes_price
        (dollars), count, taker_side (may be None)."""
        price = trade.get("yes_price")
        count = trade.get("count") or 0.0
        if price is None or count <= 0:
            return []
        taker = trade.get("taker_side")  # side the taker BOUGHT
        fills = []
        # sell pressure (taker bought NO == sold YES) can hit our bid
        if taker in (None, "no"):
            fills += self._match("bid", price, count)
        # buy pressure (taker bought YES) can lift our ask
        if taker in (None, "yes"):
            fills += self._match("ask", price, count)
        return fills

    def _match(self, side: str, price: float, count: float) -> List[Tuple[str, float, float]]:
        order = self.orders[side]
        if order is None or order.remaining <= 0:
            return []
        through = price < order.price - 1e-9 if side == "bid" else price > order.price + 1e-9
        at_level = abs(price - order.price) < 1e-9
        qty = 0.0
        if through:
            # tape printed past our level: price priority says we were done first
            qty = order.remaining
        elif at_level and self.mode == "queue":
            available = count - order.queue_ahead
            order.queue_ahead = max(0.0, order.queue_ahead - count)
            if available > 0:
                qty = min(order.remaining, available)
        if qty <= 0:
            return []
        order.remaining -= qty
        fill_price = order.price
        if order.remaining <= 1e-9:
            self.orders[side] = None
        return [(side, fill_price, qty)]


class ShadowEngine:
    """One market. Feed events in timestamp order via on_event()."""

    def __init__(self, ticker: str, close_time: Optional[str], fee_type: str,
                 fee_multiplier: float, mm_params: dict, logger: logging.Logger,
                 fill_mode: str = "queue", fills_log_path: Optional[str] = None,
                 event_sink=None):
        self.ticker = ticker
        self.logger = logger
        self.close_time = (datetime.fromisoformat(close_time.replace("Z", "+00:00"))
                           if close_time else None)
        self.maker_fee_rate = 0.25 if "maker" in (fee_type or "") else 0.0
        self.fee_multiplier = fee_multiplier or 1.0
        # client=None: we only use the pure quoting math + estimators
        self.mm = AvellanedaMarketMaker(logger=logger, client=None,
                                        market_ticker=ticker, perf_log_path="",
                                        **mm_params)
        self.tracker = PerformanceTracker(logger, log_path=fills_log_path,
                                          event_sink=event_sink)
        self.sim = FillSimulator(fill_mode)
        self.book = OrderBook(ticker)
        self.position = 0.0
        self.fees_paid = 0.0
        self.quote_updates = 0
        self.events_seen = 0
        self.last_trade_price: Optional[float] = None
        self.last_trade_ts_ms: int = 0
        self.last_ts_ms: int = 0
        self._requotes_since_k = 0

    # -------------------- event handling --------------------

    def on_event(self, kind: str, ts_ms: int, payload):
        self.events_seen += 1
        self.last_ts_ms = ts_ms
        if kind == "snapshot":
            yes_levels, no_levels = payload
            self.book.apply_snapshot({"yes_dollars": yes_levels,
                                      "no_dollars": no_levels}, ts_ms=ts_ms)
            self.sim.on_snapshot(self.book)
        elif kind == "trade":
            if payload.get("yes_price") is not None:
                self.last_trade_price = payload["yes_price"]
                self.last_trade_ts_ms = ts_ms
            for side, price, qty in self.sim.on_trade(payload):
                self._apply_fill(side, price, qty, ts_ms)
        self._requote(ts_ms)
        self.tracker.check_markouts(ts_ms / 1000.0, self.mid(ts_ms))

    def mid(self, ts_ms: int) -> Optional[float]:
        if self.book.mid is not None:
            return self.book.mid
        if (self.last_trade_price is not None
                and ts_ms - self.last_trade_ts_ms < LAST_TRADE_MID_MAX_AGE_S * 1000):
            return self.last_trade_price
        return None

    def time_to_close_h(self, ts_ms: int) -> float:
        if self.close_time is None:
            return 24.0
        return max((self.close_time.timestamp() - ts_ms / 1000.0) / 3600.0, 0.0)

    def _requote(self, ts_ms: int):
        tth = self.time_to_close_h(ts_ms)
        mid = self.mid(ts_ms)
        if (tth <= self.mm.min_time_to_resolution_h or mid is None
                or not (self.mm.min_quote_mid <= mid <= self.mm.max_quote_mid)):
            self.sim.cancel("bid")
            self.sim.cancel("ask")
            return
        self.mm.vol.add(ts_ms / 1000.0, mid)
        self._requotes_since_k += 1
        if self._requotes_since_k >= self.mm.k_refresh_every:
            self._requotes_since_k = 0
            self.mm.k = estimate_k_from_depth(self.book.depth_profile(),
                                              self.mm.default_k)
        sigma = self.mm.vol.sigma(mid)
        quotes = self.mm.compute_quotes(mid, self.position, tth, sigma)
        bid_size, ask_size = self.mm.desired_sizes(self.position)

        for side, price, size in (("bid", quotes["bid"], bid_size),
                                  ("ask", quotes["ask"], ask_size)):
            if size <= 0 or self._would_cross(side, price):
                self.sim.cancel(side)
                continue
            existing = self.sim.orders[side]
            if (existing and abs(existing.price - price) < TICK / 2
                    and abs(existing.remaining - size) < 0.5):
                continue  # unchanged: keep our queue position
            self.sim.place(side, price, size, self.book, ts_ms)
            self.quote_updates += 1

    def _would_cross(self, side: str, price: float) -> bool:
        """post_only equivalent: never model a fill that takes liquidity."""
        if side == "bid":
            ask = self.book.best_yes_ask
            return ask is not None and price >= ask
        bid = self.book.best_yes_bid
        return bid is not None and price <= bid

    def _apply_fill(self, side: str, price: float, qty: float, ts_ms: int):
        signed = qty if side == "bid" else -qty
        self.position += signed
        fee = (self.maker_fee_rate * 0.07 * self.fee_multiplier
               * price * (1.0 - price) * qty)
        self.fees_paid += fee
        created = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
        self.tracker.on_fill({
            "action": "buy" if side == "bid" else "sell",
            "side": "yes",
            "count": qty,
            "yes_price_dollars": price,
            "created_time": created.isoformat().replace("+00:00", "Z"),
            "is_taker": False,
        }, mid_at_fill=self.mid(ts_ms))
        self.logger.info(f"{self.ticker}: SHADOW FILL {side} {qty:g} @ {price:.2f} "
                         f"-> position {self.position:g}")

    # -------------------- finish --------------------

    def finalize(self, settle_price: Optional[float]) -> dict:
        """Settle remaining inventory at the known outcome (1.0/0.0) via a
        synthetic closing fill, or mark at the last usable mid."""
        mark = self.mid(self.last_ts_ms)
        settled = False
        if self.position != 0 and settle_price is not None:
            side = "sell" if self.position > 0 else "buy"
            created = datetime.fromtimestamp(self.last_ts_ms / 1000.0, tz=timezone.utc)
            self.tracker.on_fill({
                "action": side, "side": "yes", "count": abs(self.position),
                "yes_price_dollars": settle_price,
                "created_time": created.isoformat().replace("+00:00", "Z"),
            }, mid_at_fill=settle_price)
            self.position = 0.0
            mark = settle_price
            settled = True
        summary = self.tracker.summary(mark)
        summary.update({
            "ticker": self.ticker,
            "fees": round(self.fees_paid, 4),
            "net_pnl": round(summary["total_pnl"] - self.fees_paid, 4),
            "quote_updates": self.quote_updates,
            "events": self.events_seen,
            "settled": settled,
        })
        return summary


# --------------------------------------------------------------------------
# event sources
# --------------------------------------------------------------------------

def replay_events(conn: sqlite3.Connection, ticker: str,
                  start_ms: Optional[int] = None, end_ms: Optional[int] = None
                  ) -> Iterator[Tuple[str, int, object]]:
    """Snapshots and trades for one market, merged in timestamp order."""
    cond, params = "market_ticker = ?", [ticker]
    if start_ms:
        cond += " AND ts_ms >= ?"
        params.append(start_ms)
    if end_ms:
        cond += " AND ts_ms <= ?"
        params.append(end_ms)
    snaps = conn.execute(
        f"SELECT ts_ms, yes_levels, no_levels FROM book_snapshots WHERE {cond}"
        f" ORDER BY ts_ms", params).fetchall()
    trades = conn.execute(
        f"SELECT ts_ms, yes_price, no_price, count, taker_side FROM trades"
        f" WHERE {cond} ORDER BY ts_ms", params).fetchall()
    si = ti = 0
    while si < len(snaps) or ti < len(trades):
        # ties: snapshot first, so the trade sees the book state that produced it
        if ti >= len(trades) or (si < len(snaps) and snaps[si][0] <= trades[ti][0]):
            ts, yes, no = snaps[si]
            yield "snapshot", ts, (json.loads(yes), json.loads(no))
            si += 1
        else:
            ts, yp, np_, cnt, taker = trades[ti]
            yield "trade", ts, {"yes_price": yp, "no_price": np_,
                                "count": cnt, "taker_side": taker}
            ti += 1


def follow_events(conn: sqlite3.Connection, tickers: List[str],
                  poll_seconds: float = 3.0, duration: Optional[float] = None
                  ) -> Iterator[Tuple[str, str, int, object]]:
    """Tail the DB the live recorder is writing. Yields (ticker, kind, ts, payload).
    Starts from 'now' so history does not replay into a live session."""
    marks = {"snap": int(time.time() * 1000), "trade": int(time.time() * 1000)}
    q = ",".join("?" * len(tickers))
    deadline = time.time() + duration if duration else None
    while deadline is None or time.time() < deadline:
        rows = conn.execute(
            f"SELECT market_ticker, ts_ms, yes_levels, no_levels FROM book_snapshots"
            f" WHERE ts_ms > ? AND market_ticker IN ({q}) ORDER BY ts_ms",
            [marks["snap"], *tickers]).fetchall()
        for ticker, ts, yes, no in rows:
            yield ticker, "snapshot", ts, (json.loads(yes), json.loads(no))
            marks["snap"] = max(marks["snap"], ts)
        rows = conn.execute(
            f"SELECT market_ticker, ts_ms, yes_price, no_price, count, taker_side"
            f" FROM trades WHERE ts_ms > ? AND market_ticker IN ({q}) ORDER BY ts_ms",
            [marks["trade"], *tickers]).fetchall()
        for ticker, ts, yp, np_, cnt, taker in rows:
            yield ticker, "trade", ts, {"yes_price": yp, "no_price": np_,
                                        "count": cnt, "taker_side": taker}
            marks["trade"] = max(marks["trade"], ts)
        time.sleep(poll_seconds)


def fetch_settle_price(ticker: str, environment: str = "prod") -> Optional[float]:
    """Public REST lookup of a market's settlement outcome, if settled."""
    import requests
    base = ("https://api.elections.kalshi.com/trade-api/v2" if environment == "prod"
            else "https://demo-api.kalshi.co/trade-api/v2")
    try:
        market = requests.get(f"{base}/markets/{ticker}", timeout=15).json()["market"]
    except Exception:
        return None
    if market.get("status") == "settled":
        result = market.get("result")
        if result in ("yes", "no"):
            return 1.0 if result == "yes" else 0.0
    return None
