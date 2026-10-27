"""Exchange gateway: the session engine's only view of Kalshi.

Wraps KalshiHttpClient and normalizes the 2026 wire formats (fixed-point
"_fp" counts, "_dollars" prices, V2 book_side/outcome_side direction, cursor
pagination) into small plain dicts:

    order view    {order_id, client_order_id, ticker, side, price, remaining,
                   filled, initial, status, expiration_ts, created_ts}
    fill view     {fill_id, trade_id, order_id, ticker, side, qty, price,
                   is_taker, fee, ts}
    position view {ticker, position, realized_pnl, fees_paid, exposure}
    market view   {ticker, event_ticker, series_ticker, title, status,
                   tradable, close_ts, yes_bid, yes_ask, bid_size, ask_size,
                   last_price, volume, volume_24h, open_interest, max_tick}

`side` is always the yes-axis book side ('bid' = long YES, 'ask' = short YES)
and every price is a yes-axis dollar price. Tests substitute an in-memory
fake with the same methods.
"""
import itertools
import time
import uuid
from datetime import datetime
from typing import Dict, Iterable, List, Optional

from helper import book_side_of, taker_outcome_of

TRADABLE_STATUSES = {"active", "open"}


def fnum(value, default: Optional[float] = 0.0) -> Optional[float]:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def iso_ts(value) -> Optional[float]:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _dollars(record: dict, name: str) -> Optional[float]:
    """Prefer '<name>_dollars'; fall back to legacy integer cents '<name>'."""
    v = record.get(f"{name}_dollars")
    if v is not None:
        return fnum(v, None)
    v = record.get(name)
    return None if v is None else fnum(v, 0.0) / 100.0


def order_view(o: dict) -> dict:
    side = book_side_of(o)
    price = _dollars(o, "yes_price")
    if price is None:
        no = _dollars(o, "no_price")
        price = None if no is None else round(1.0 - no, 6)
    return {
        "order_id": o.get("order_id"),
        "client_order_id": o.get("client_order_id"),
        "ticker": o.get("ticker"),
        "side": side,
        "price": price,
        "remaining": fnum(o.get("remaining_count_fp", o.get("remaining_count"))),
        "filled": fnum(o.get("fill_count_fp", o.get("fill_count"))),
        "initial": fnum(o.get("initial_count_fp", o.get("initial_count"))),
        "status": o.get("status"),
        "expiration_ts": iso_ts(o.get("expiration_time")),
        "created_ts": iso_ts(o.get("created_time")),
    }


def fill_view(f: dict) -> dict:
    price = _dollars(f, "yes_price")
    if price is None:
        no = _dollars(f, "no_price")
        price = None if no is None else round(1.0 - no, 6)
    ts = f.get("ts")
    ts = float(ts) if ts is not None else iso_ts(f.get("created_time"))
    return {
        "fill_id": f.get("fill_id") or f"{f.get('trade_id')}:{f.get('order_id')}",
        "trade_id": f.get("trade_id"),
        "order_id": f.get("order_id"),
        "ticker": f.get("market_ticker") or f.get("ticker"),
        "side": book_side_of(f),
        "qty": fnum(f.get("count_fp", f.get("count"))),
        "price": price,
        "is_taker": bool(f.get("is_taker")),
        "fee": fnum(f.get("fee_cost")),
        "ts": ts if ts is not None else time.time(),
    }


def position_view(p: dict) -> dict:
    return {
        "ticker": p.get("ticker"),
        "position": fnum(p.get("position_fp", p.get("position"))),
        "realized_pnl": _dollars(p, "realized_pnl") or 0.0,
        "fees_paid": _dollars(p, "fees_paid") or 0.0,
        "exposure": _dollars(p, "market_exposure") or 0.0,
    }


def max_tick(price_ranges) -> Optional[float]:
    """Largest tick size across a market's price ranges, if published."""
    steps = []
    for r in price_ranges or []:
        if isinstance(r, dict):
            step = fnum(r.get("step") or r.get("tick_size") or r.get("step_dollars"), None)
            if step:
                steps.append(step)
    return max(steps) if steps else None


def market_view(m: dict) -> dict:
    event = m.get("event_ticker") or ""
    return {
        "ticker": m.get("ticker"),
        "event_ticker": event,
        "series_ticker": m.get("series_ticker") or (event.split("-")[0] if event else
                                                     (m.get("ticker") or "").split("-")[0]),
        "title": m.get("title") or m.get("yes_sub_title"),
        "status": m.get("status"),
        "tradable": m.get("status") in TRADABLE_STATUSES,
        "close_ts": iso_ts(m.get("close_time")),
        "yes_bid": _dollars(m, "yes_bid"),
        "yes_ask": _dollars(m, "yes_ask"),
        "bid_size": fnum(m.get("yes_bid_size_fp")),
        "ask_size": fnum(m.get("yes_ask_size_fp")),
        "last_price": _dollars(m, "last_price"),
        "volume": fnum(m.get("volume_fp", m.get("volume"))),
        "volume_24h": fnum(m.get("volume_24h_fp", m.get("volume_24h"))),
        "open_interest": fnum(m.get("open_interest_fp", m.get("open_interest"))),
        "max_tick": max_tick(m.get("price_ranges")),
        "mve": bool(m.get("mve_collection_ticker")),
    }


def trade_view(t: dict) -> dict:
    ts = iso_ts(t.get("created_time"))
    return {
        "trade_id": t.get("trade_id"),
        "ticker": t.get("ticker") or t.get("market_ticker"),
        "price": _dollars(t, "yes_price"),
        "qty": fnum(t.get("count_fp", t.get("count"))),
        "taker_side": taker_outcome_of(t),
        "ts": ts if ts is not None else time.time(),
    }


class ExchangeGateway:
    def __init__(self, client):
        self.client = client

    # ---------------- reads ----------------

    def exchange_status(self) -> dict:
        return self.client.get_exchange_status()

    def balance(self) -> dict:
        b = self.client.get_balance()
        bal = _dollars(b, "balance")
        pv = b.get("portfolio_value_dollars")
        pv = fnum(pv, None) if pv is not None else (
            None if b.get("portfolio_value") is None else fnum(b["portfolio_value"]) / 100.0)
        return {"balance": bal, "portfolio_value": pv}

    def account_limits(self) -> dict:
        return self.client.get_account_limits()

    def series_info(self, series_ticker: str) -> dict:
        s = self.client.get_series(series_ticker).get("series", {})
        return {"fee_type": s.get("fee_type"), "fee_multiplier": s.get("fee_multiplier")}

    def markets(self, tickers: List[str]) -> Dict[str, dict]:
        out: Dict[str, dict] = {}
        for i in range(0, len(tickers), 100):
            data = self.client.get_markets(tickers=",".join(tickers[i:i + 100]),
                                           limit=1000)
            for m in data.get("markets", []):
                out[m["ticker"]] = market_view(m)
        return out

    def list_open_markets(self, min_close_ts: Optional[int] = None,
                          max_close_ts: Optional[int] = None, max_pages: int = 10,
                          exclude_multivariate: bool = True) -> List[dict]:
        params = {"status": "open", "limit": 1000}
        if min_close_ts:
            params["min_close_ts"] = int(min_close_ts)
        if max_close_ts:
            params["max_close_ts"] = int(max_close_ts)
        if exclude_multivariate:
            params["mve_filter"] = "exclude"
        raw = self.client.paginate(self.client.markets_url, "markets", params,
                                   max_pages=max_pages)
        return [market_view(m) for m in raw]

    def orderbooks(self, tickers: List[str]) -> Dict[str, dict]:
        return {ob["ticker"]: ob.get("orderbook_fp") or ob.get("orderbook") or {}
                for ob in self.client.get_orderbooks(tickers)}

    def resting_orders(self, tickers: List[str]) -> Dict[str, List[dict]]:
        out: Dict[str, List[dict]] = {t: [] for t in tickers}
        for o in self.client.get_all_orders(tickers=tickers, status="resting"):
            v = order_view(o)
            if v["ticker"] in out and v["remaining"] > 0:
                out[v["ticker"]].append(v)
        return out

    def positions(self, tickers: Iterable[str]) -> Dict[str, dict]:
        wanted = set(tickers)
        return {p["ticker"]: position_view(p)
                for p in self.client.get_all_positions(count_filter=None)
                if p.get("ticker") in wanted}

    def fills_since(self, min_ts: int) -> List[dict]:
        return [fill_view(f) for f in self.client.get_all_fills(min_ts=min_ts)]

    def trades_since(self, ticker: str, min_ts: int, limit: int = 1000) -> List[dict]:
        data = self.client.get_trades(ticker=ticker, min_ts=int(min_ts), limit=limit)
        return [trade_view(t) for t in data.get("trades", [])]

    def api_stats(self) -> dict:
        stats = getattr(self.client, "stats", None)
        return stats.snapshot() if stats else {}

    # ---------------- writes ----------------

    def create_order(self, ticker: str, side: str, count: float, price: float,
                     expiration_ts: Optional[int] = None, post_only: bool = True,
                     cancel_order_on_pause: bool = True,
                     time_in_force: str = "good_till_canceled",
                     reduce_only: bool = False,
                     client_order_id: Optional[str] = None) -> dict:
        resp = self.client.create_order_v2(
            ticker=ticker, side=side, count=count, price=price,
            client_order_id=client_order_id or str(uuid.uuid4()),
            time_in_force=time_in_force, post_only=post_only,
            expiration_ts=expiration_ts, reduce_only=reduce_only,
            cancel_order_on_pause=cancel_order_on_pause)
        return {"order_id": resp.get("order_id"),
                "fill_count": fnum(resp.get("fill_count")),
                "remaining_count": fnum(resp.get("remaining_count"), count),
                "average_fill_price": fnum(resp.get("average_fill_price"), None)}

    def amend_order(self, order_id: str, ticker: str, side: str, count: float,
                    price: float, expiration_ts: Optional[int] = None) -> dict:
        resp = self.client.amend_order_v2(order_id, ticker=ticker, side=side,
                                          count=count, price=price,
                                          expiration_ts=expiration_ts)
        return {"order_id": resp.get("order_id", order_id),
                "fill_count": fnum(resp.get("fill_count")),
                "remaining_count": fnum(resp.get("remaining_count"), None)}

    def cancel_order(self, order_id: str, ticker: Optional[str] = None) -> dict:
        resp = self.client.cancel_order_v2(order_id, market_ticker=ticker)
        return {"order_id": order_id, "reduced_by": fnum(resp.get("reduced_by"))}


class DryRunGateway:
    """Real market data, simulated order writes. Orders are kept locally and
    reported back as resting (never filled), so the execution layer's
    keep/amend/cancel decisions can be observed against live books without
    touching the exchange. Virtual orders are flagged so the engine does not
    net them out of the real book."""

    def __init__(self, inner: ExchangeGateway):
        self.inner = inner
        self.orders: Dict[str, dict] = {}
        self._ids = itertools.count(1)

    def __getattr__(self, name):  # pass every read straight through
        return getattr(self.inner, name)

    def balance(self) -> dict:
        # keyless dry runs use the public endpoints only; balance is private
        if not getattr(getattr(self.inner, "client", None), "authenticated", True):
            return {"balance": None, "portfolio_value": None}
        return self.inner.balance()

    def resting_orders(self, tickers: List[str]) -> Dict[str, List[dict]]:
        out: Dict[str, List[dict]] = {t: [] for t in tickers}
        for o in self.orders.values():
            if o["ticker"] in out:
                out[o["ticker"]].append(dict(o))
        return out

    def positions(self, tickers):
        return {}

    def fills_since(self, min_ts):
        return []

    def create_order(self, ticker, side, count, price, expiration_ts=None, **_):
        oid = f"dry-{next(self._ids)}"
        self.orders[oid] = {"order_id": oid, "client_order_id": None, "ticker": ticker,
                            "side": side, "price": round(price, 4), "remaining": count,
                            "filled": 0.0, "initial": count, "status": "resting",
                            "expiration_ts": expiration_ts, "created_ts": time.time(),
                            "virtual": True}
        return {"order_id": oid, "fill_count": 0.0, "remaining_count": count}

    def amend_order(self, order_id, ticker, side, count, price, expiration_ts=None):
        o = self.orders[order_id]
        o.update(price=round(price, 4), remaining=count - o["filled"])
        if expiration_ts:
            o["expiration_ts"] = expiration_ts
        return {"order_id": order_id, "fill_count": 0.0, "remaining_count": o["remaining"]}

    def cancel_order(self, order_id, ticker=None):
        o = self.orders.pop(order_id, None)
        return {"order_id": order_id, "reduced_by": o["remaining"] if o else 0.0}
