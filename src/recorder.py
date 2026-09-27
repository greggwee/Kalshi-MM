"""Market data recorder for Kalshi.

Two modes:

  ws   - WebSocket subscription to orderbook_delta / trade / ticker channels.
         Full L2 event stream. Requires API keys for the chosen environment
         (Kalshi requires auth on the WS connection even for public channels).

  rest - Polls the PUBLIC REST endpoints (orderbook + trade tape) on an
         interval. No API keys needed, works against prod today. Coarser than
         ws (snapshot cadence = poll interval) but sufficient for illiquid
         markets where the book changes slowly.

Market list comes from config: explicit tickers and/or series tickers that are
expanded to all open markets at startup.
"""
import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import requests
import websockets

from src.clients import KalshiWebSocketClient, Environment
from src.orderbook import OrderBook
from src.storage import RecorderDB

PUBLIC_REST_BASE = {
    "prod": "https://api.elections.kalshi.com/trade-api/v2",
    "demo": "https://demo-api.kalshi.co/trade-api/v2",
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _iso_to_ms(s: Optional[str]) -> int:
    if not s:
        return _now_ms()
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return _now_ms()


def _trade_fields(t: dict) -> dict:
    """Normalize a trade from any of Kalshi's wire formats (cents ints,
    dollar strings, fp counts)."""
    def price(name):
        v = t.get(f"{name}_price_dollars")
        if v is not None:
            return float(v)
        v = t.get(f"{name}_price")
        return None if v is None else float(v) / 100.0

    count = t.get("count_fp", t.get("count"))
    return {
        "trade_id": t.get("trade_id"),
        "ts_ms": t.get("ts_ms") or _iso_to_ms(t.get("created_time")),
        "ticker": t.get("ticker") or t.get("market_ticker"),
        "yes_price": price("yes"),
        "no_price": price("no"),
        "count": None if count is None else float(count),
        "taker_side": t.get("taker_side"),
        "is_block": bool(t.get("is_block_trade", False)),
    }


# --------------------------------------------------------------------------
# Market list expansion (public REST, no auth)
# --------------------------------------------------------------------------

def expand_markets(config: dict, environment: str, logger: logging.Logger) -> List[str]:
    base = PUBLIC_REST_BASE[environment]
    mcfg = config.get("markets", {})
    tickers = list(mcfg.get("tickers") or [])
    max_markets = int(mcfg.get("max_markets", 50))
    session = requests.Session()

    for series in mcfg.get("series_tickers") or []:
        cursor = None
        while len(tickers) < max_markets:
            params = {"series_ticker": series, "status": "open", "limit": 100}
            if cursor:
                params["cursor"] = cursor
            r = session.get(f"{base}/markets", params=params, timeout=15)
            r.raise_for_status()
            data = r.json()
            for m in data.get("markets", []):
                if m["ticker"] not in tickers:
                    tickers.append(m["ticker"])
                if len(tickers) >= max_markets:
                    break
            cursor = data.get("cursor")
            if not cursor or not data.get("markets"):
                break
    logger.info(f"Recording {len(tickers)} markets: {tickers[:10]}{'...' if len(tickers) > 10 else ''}")
    return tickers[:max_markets]


def store_market_metadata(db: RecorderDB, tickers: List[str], environment: str,
                          logger: logging.Logger):
    base = PUBLIC_REST_BASE[environment]
    session = requests.Session()
    series_cache: Dict[str, dict] = {}
    for t in tickers:
        try:
            market = session.get(f"{base}/markets/{t}", timeout=15).json()["market"]
            st = market.get("series_ticker") or t.split("-")[0]
            if st not in series_cache:
                r = session.get(f"{base}/series/{st}", timeout=15)
                series_cache[st] = r.json().get("series", {}) if r.ok else {}
            db.upsert_market(market, series_cache[st])
        except Exception as e:
            logger.warning(f"metadata fetch failed for {t}: {e}")
    db.commit()


# --------------------------------------------------------------------------
# WebSocket recorder
# --------------------------------------------------------------------------

class WSRecorder:
    def __init__(self, key_id: str, private_key, environment: str,
                 tickers: List[str], db: RecorderDB, logger: logging.Logger,
                 snapshot_interval: float = 60.0):
        env = Environment.DEMO if environment == "demo" else Environment.PROD
        self._wsc = KalshiWebSocketClient(key_id, private_key, env)
        self.tickers = tickers
        self.db = db
        self.logger = logger
        self.snapshot_interval = snapshot_interval
        self.books: Dict[str, OrderBook] = {}
        self._last_snapshot_row: Dict[str, float] = {}

    async def run(self, duration: Optional[float] = None):
        deadline = time.time() + duration if duration else None
        backoff = 1.0
        while True:
            try:
                await self._session(deadline)
                backoff = 1.0
            except (websockets.ConnectionClosed, OSError, asyncio.TimeoutError) as e:
                if deadline and time.time() >= deadline:
                    return
                self.logger.warning(f"WS disconnected ({e!r}); reconnecting in {backoff:.0f}s")
            except _SeqGap as e:
                self.logger.warning(f"sequence gap on {e}; reconnecting for fresh snapshots")
            if deadline and time.time() >= deadline:
                return
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    async def _session(self, deadline: Optional[float]):
        url = self._wsc.WS_BASE_URL + self._wsc.url_suffix
        headers = self._wsc.request_headers("GET", self._wsc.url_suffix)
        async with websockets.connect(url, additional_headers=headers,
                                      open_timeout=15, ping_interval=10) as ws:
            await ws.send(json.dumps({
                "id": 1, "cmd": "subscribe",
                "params": {"channels": ["orderbook_delta", "trade", "ticker"],
                           "market_tickers": self.tickers},
            }))
            self.logger.info(f"WS connected, subscribed to {len(self.tickers)} markets")
            while True:
                timeout = None
                if deadline:
                    timeout = max(0.5, deadline - time.time())
                    if time.time() >= deadline:
                        return
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout) if timeout else await ws.recv()
                self._handle(json.loads(raw))

    def _book(self, ticker: str) -> OrderBook:
        if ticker not in self.books:
            self.books[ticker] = OrderBook(ticker)
        return self.books[ticker]

    def _handle(self, msg: dict):
        mtype = msg.get("type")
        payload = msg.get("msg", {})
        seq = msg.get("seq")
        sid = msg.get("sid")
        ts_ms = payload.get("ts_ms") or _now_ms()

        if mtype == "orderbook_snapshot":
            ticker = payload["market_ticker"]
            book = self._book(ticker)
            book.apply_snapshot(payload, seq=seq, ts_ms=ts_ms)
            yes, no = book.levels_json()
            self.db.insert_snapshot(ts_ms, ticker, "ws", yes, no, seq=seq)
            self._last_snapshot_row[ticker] = time.time()

        elif mtype == "orderbook_delta":
            ticker = payload["market_ticker"]
            book = self._book(ticker)
            ok = book.apply_delta(payload["side"], payload["price_dollars"],
                                  payload["delta_fp"], seq=seq, ts_ms=ts_ms)
            if not ok:
                raise _SeqGap(ticker)
            self.db.insert_delta(ts_ms, ticker, payload["side"],
                                 float(payload["price_dollars"]),
                                 float(payload["delta_fp"]), seq, sid)
            # periodic full-state row so replays don't need every delta from t0
            if time.time() - self._last_snapshot_row.get(ticker, 0) > self.snapshot_interval:
                yes, no = book.levels_json()
                self.db.insert_snapshot(ts_ms, ticker, "ws", yes, no, seq=seq)
                self._last_snapshot_row[ticker] = time.time()

        elif mtype == "trade":
            f = _trade_fields(payload)
            trade_id = f["trade_id"] or f"ws-{f['ticker']}-{ts_ms}-{f['count']}"
            self.db.insert_trade(trade_id, f["ts_ms"], f["ticker"], f["yes_price"],
                                 f["no_price"], f["count"], f["taker_side"],
                                 f["is_block"], "ws")

        elif mtype == "ticker":
            ticker = payload.get("market_ticker")
            if ticker:
                self.db.insert_ticker(ts_ms, ticker, payload)

        elif mtype == "error":
            self.logger.error(f"WS error message: {msg}")


class _SeqGap(Exception):
    pass


# --------------------------------------------------------------------------
# REST poller (public endpoints, no auth required)
# --------------------------------------------------------------------------

class RestPoller:
    def __init__(self, environment: str, tickers: List[str], db: RecorderDB,
                 logger: logging.Logger, poll_interval: float = 2.0):
        self.base = PUBLIC_REST_BASE[environment]
        self.tickers = tickers
        self.db = db
        self.logger = logger
        self.poll_interval = poll_interval
        self.session = requests.Session()
        self._last_book_state: Dict[str, str] = {}
        self._last_trade_ts: Dict[str, int] = {}   # unix seconds

    def run(self, duration: Optional[float] = None):
        deadline = time.time() + duration if duration else None
        cycle = 0
        while deadline is None or time.time() < deadline:
            start = time.time()
            for t in self.tickers:
                try:
                    self._poll_book(t)
                    self._poll_trades(t)
                except requests.HTTPError as e:
                    status = e.response.status_code if e.response is not None else "?"
                    if status == 429:
                        self.logger.warning("rate limited (429); backing off 10s")
                        time.sleep(10)
                    else:
                        self.logger.warning(f"{t}: HTTP {status}")
                except requests.RequestException as e:
                    self.logger.warning(f"{t}: {e}")
            cycle += 1
            if cycle % 50 == 0:
                self.logger.info(f"cycle {cycle}: rows={self.db.counts()}")
            time.sleep(max(0.0, self.poll_interval - (time.time() - start)))
        self.db.commit()

    def _poll_book(self, ticker: str):
        r = self.session.get(f"{self.base}/markets/{ticker}/orderbook", timeout=15)
        r.raise_for_status()
        ob = r.json().get("orderbook_fp") or r.json().get("orderbook") or {}
        state = json.dumps(ob, sort_keys=True)
        if state == self._last_book_state.get(ticker):
            return  # unchanged; don't duplicate rows
        self._last_book_state[ticker] = state
        book = OrderBook(ticker)
        book.apply_snapshot(ob, ts_ms=_now_ms())
        yes, no = book.levels_json()
        self.db.insert_snapshot(_now_ms(), ticker, "rest", yes, no)

    def _poll_trades(self, ticker: str):
        params = {"ticker": ticker, "limit": 100}
        last = self._last_trade_ts.get(ticker)
        if last:
            params["min_ts"] = last
        r = self.session.get(f"{self.base}/markets/trades", params=params, timeout=15)
        r.raise_for_status()
        newest = last or 0
        for t in r.json().get("trades", []):
            f = _trade_fields(t)
            if f["trade_id"]:
                self.db.insert_trade(f["trade_id"], f["ts_ms"], f["ticker"] or ticker,
                                     f["yes_price"], f["no_price"], f["count"],
                                     f["taker_side"], f["is_block"], "rest")
            newest = max(newest, f["ts_ms"] // 1000)
        if newest:
            self._last_trade_ts[ticker] = newest
