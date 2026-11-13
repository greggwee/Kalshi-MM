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
expanded to all open markets at startup and re-expanded periodically
(daily markets open and close while the recorder runs).
"""
import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import requests
import websockets

from helper import taker_outcome_of
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
        "taker_side": taker_outcome_of(t),
        "is_block": bool(t.get("is_block_trade", False)),
    }


# --------------------------------------------------------------------------
# Market list expansion (public REST, no auth)
# --------------------------------------------------------------------------

def expand_markets(config: dict, environment: str, logger: logging.Logger,
                   quiet: bool = False) -> List[str]:
    base = PUBLIC_REST_BASE[environment]
    mcfg = config.get("markets", {})
    tickers = list(mcfg.get("tickers") or [])
    max_markets = int(mcfg.get("max_markets", 50))
    session = requests.Session()

    for series in mcfg.get("series_tickers") or []:
        cursor = None
        while len(tickers) < max_markets:
            params = {"series_ticker": series, "status": "open", "limit": 1000}
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
    if not quiet:
        logger.info(f"Recording {len(tickers)} markets: "
                    f"{tickers[:10]}{'...' if len(tickers) > 10 else ''}")
    return tickers[:max_markets]


def store_market_metadata(db: RecorderDB, tickers: List[str], environment: str,
                          logger: logging.Logger, session=None):
    base = PUBLIC_REST_BASE[environment]
    session = session or requests.Session()
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

QUOTE_CHUNK = 100            # tickers per bulk /markets request
MAX_TRADE_PAGES = 50         # safety cap per market per poll
_QUOTE_KEYS = ("yes_bid_dollars", "yes_ask_dollars", "yes_bid_size_fp", "yes_ask_size_fp")
_TICKER_ROW_KEYS = _QUOTE_KEYS + ("last_price_dollars", "volume_fp", "open_interest_fp",
                                  "status")


def refresh_outcomes(db: RecorderDB, environment: str, logger: logging.Logger,
                     session=None, now: Optional[float] = None, limit: int = 200) -> int:
    """Fill in status/result for stored markets that have closed but have no
    outcome yet. Finalized markets leave GET /markets/{t} (404) and move to
    GET /historical/markets/{t}. Returns how many outcomes were stored."""
    base = PUBLIC_REST_BASE[environment]
    session = session or requests.Session()
    now_iso = datetime.fromtimestamp(now or time.time(), tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    stored = 0
    for ticker in db.markets_missing_outcome(now_iso, limit=limit):
        try:
            market = None
            for path in (f"/markets/{ticker}", f"/historical/markets/{ticker}"):
                r = session.get(f"{base}{path}", timeout=15)
                if r.status_code == 404:
                    continue
                r.raise_for_status()
                market = r.json().get("market")
                break
            if not market:
                continue
            db.update_outcome(ticker, market)
            if market.get("result") in ("yes", "no"):
                stored += 1
        except (requests.RequestException, ValueError) as e:
            logger.warning(f"outcome refresh failed for {ticker}: {e}")
    db.commit()
    return stored


class RestPoller:
    """Polls public REST market data.

    Each cycle:
      1. one bulk GET /markets?tickers=... per 100 markets gives every
         market's touch (bid/ask + sizes) and cumulative volume. A changed
         touch is stored as a `tickers` row and marks the market's book dirty;
         a changed volume marks its tape dirty.
      2. dirty markets get a full-depth book and/or an incremental tape poll.
      3. a rotating slice of all markets is polled regardless, sized so that
         every market is fully swept once per `full_sweep_interval` (catches
         depth changes behind the touch and anything the listing missed).

    `refresh` (optional callable returning the current ticker list) is called
    every `refresh_interval` seconds, so daily markets that open after startup
    get recorded and closed ones are retired after one last tape poll.
    """

    def __init__(self, environment: str, tickers: List[str], db: RecorderDB,
                 logger: logging.Logger, poll_interval: float = 2.0,
                 refresh=None, refresh_interval: float = 600.0,
                 full_sweep_interval: float = 120.0, quote_poll: bool = True,
                 max_rps: float = 4.0, session=None, clock=time.time,
                 sleep=time.sleep):
        self.environment = environment
        self.base = PUBLIC_REST_BASE[environment]
        self.tickers = list(tickers)
        self.db = db
        self.logger = logger
        self.poll_interval = poll_interval
        self.refresh = refresh
        self.refresh_interval = refresh_interval
        self.full_sweep_interval = full_sweep_interval
        self.quote_poll = quote_poll
        self._http = session or requests.Session()
        self.session = self            # helpers that take a session get the paced one
        self._min_gap = 1.0 / max_rps if max_rps and max_rps > 0 else 0.0
        self._next_request = 0.0
        self._clock = clock
        self._sleep = sleep
        self._last_book_state: Dict[str, str] = {}
        self._last_trade_ts: Dict[str, int] = {}   # unix seconds
        self._last_quote: Dict[str, tuple] = {}
        self._last_volume: Dict[str, str] = {}
        self._last_refresh = clock()
        self._sweep_pos = 0

    # ---------- paced HTTP ----------

    def get(self, url, params=None, timeout=15):
        """GET through the shared budget: at most max_rps requests per second
        across everything the poller does, and a 429 is retried after a
        growing pause instead of dropping the request."""
        r = None
        for attempt in range(4):
            wait = self._next_request - self._clock()
            if wait > 0:
                self._sleep(wait)
            self._next_request = max(self._clock(), self._next_request) + self._min_gap
            r = self._http.get(url, params=params, timeout=timeout)
            if r.status_code != 429:
                return r
            pause = 2.0 * 2 ** attempt
            self.logger.warning(f"rate limited (429); pausing {pause:.0f}s")
            self._sleep(pause)
            self._next_request = self._clock() + self._min_gap
        return r

    # ---------- main loop ----------

    def run(self, duration: Optional[float] = None):
        deadline = self._clock() + duration if duration else None
        cycle = 0
        while deadline is None or self._clock() < deadline:
            start = self._clock()
            self.run_cycle()
            cycle += 1
            if cycle % 50 == 0:
                self.logger.info(f"cycle {cycle}: {len(self.tickers)} markets, "
                                 f"rows={self.db.counts()}")
            self._sleep(max(0.0, self.poll_interval - (self._clock() - start)))
        self.db.commit()

    def run_cycle(self):
        if self.refresh and self._clock() - self._last_refresh >= self.refresh_interval:
            self.refresh_markets()
        dirty_books, dirty_tapes = self._poll_quotes()
        sweep = set(self._sweep_slice())
        for t in list(self.tickers):
            want_book = t in dirty_books or t in sweep
            want_tape = t in dirty_tapes or t in sweep
            if not (want_book or want_tape):
                continue
            try:
                if want_book:
                    self._poll_book(t)
                if want_tape:
                    self._poll_trades(t)
            except requests.HTTPError as e:
                status = e.response.status_code if e.response is not None else "?"
                self.logger.warning(f"{t}: HTTP {status}")
            except requests.RequestException as e:
                self.logger.warning(f"{t}: {e}")

    def _sweep_slice(self) -> List[str]:
        n = len(self.tickers)
        if n == 0:
            return []
        if not self.quote_poll or self.full_sweep_interval <= self.poll_interval:
            return list(self.tickers)
        k = max(1, -(-n * self.poll_interval // self.full_sweep_interval))
        k = int(min(n, k))
        start = self._sweep_pos % n
        self._sweep_pos = (start + k) % n
        return [self.tickers[(start + i) % n] for i in range(k)]

    # ---------- market list refresh ----------

    def refresh_markets(self):
        self._last_refresh = self._clock()
        try:
            new = list(self.refresh())
        except (requests.RequestException, ValueError, KeyError) as e:
            self.logger.warning(f"market refresh failed, keeping current list: {e}")
            return
        if not new:
            self.logger.warning("market refresh returned no markets; keeping current list")
            return
        current = set(self.tickers)
        added = [t for t in new if t not in current]
        removed = [t for t in self.tickers if t not in set(new)]
        for t in removed:               # last look at the tape before retiring
            try:
                self._poll_trades(t)
            except requests.RequestException as e:
                self.logger.warning(f"{t}: final tape poll failed: {e}")
            for cache in (self._last_book_state, self._last_trade_ts,
                          self._last_quote, self._last_volume):
                cache.pop(t, None)
        if added:
            store_market_metadata(self.db, added, self.environment, self.logger,
                                  self.session)
        self.tickers = new
        settled = refresh_outcomes(self.db, self.environment, self.logger, self.session,
                                   now=self._clock())
        if added or removed or settled:
            self.logger.info(f"market refresh: +{len(added)} -{len(removed)} "
                             f"-> {len(new)} markets; {settled} outcomes stored")

    # ---------- pollers ----------

    def _poll_quotes(self):
        """Returns (dirty_books, dirty_tapes). On any failure every market is
        treated as dirty for this cycle's sweep slice only (no data is lost:
        books dedupe and the tape is incremental)."""
        dirty_books, dirty_tapes = set(), set()
        if not self.quote_poll:
            return dirty_books, dirty_tapes
        for i in range(0, len(self.tickers), QUOTE_CHUNK):
            chunk = self.tickers[i:i + QUOTE_CHUNK]
            try:
                r = self.session.get(f"{self.base}/markets",
                                     params={"tickers": ",".join(chunk), "limit": 1000},
                                     timeout=15)
                r.raise_for_status()
                markets = r.json().get("markets", [])
            except (requests.RequestException, ValueError) as e:
                self.logger.warning(f"quote poll failed: {e}")
                continue
            ts = _now_ms()
            for m in markets:
                t = m.get("ticker")
                if not t:
                    continue
                quote = tuple(m.get(k) for k in _QUOTE_KEYS)
                volume = m.get("volume_fp")
                if quote != self._last_quote.get(t):
                    self._last_quote[t] = quote
                    self.db.insert_ticker(ts, t, {k: m.get(k) for k in _TICKER_ROW_KEYS})
                    dirty_books.add(t)
                if volume != self._last_volume.get(t):
                    self._last_volume[t] = volume
                    dirty_tapes.add(t)
                    dirty_books.add(t)
        return dirty_books, dirty_tapes

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
        """Incremental tape poll. Pages newest-first until it reaches a trade
        that is already stored, so a burst of more than one page between two
        polls is not truncated."""
        last = self._last_trade_ts.get(ticker)
        newest = last or 0
        cursor = None
        for _ in range(MAX_TRADE_PAGES):
            params = {"ticker": ticker, "limit": 100}
            if last:
                params["min_ts"] = last
            if cursor:
                params["cursor"] = cursor
            r = self.session.get(f"{self.base}/markets/trades", params=params, timeout=15)
            r.raise_for_status()
            data = r.json()
            batch = data.get("trades", [])
            seen_known = False
            for t in batch:
                f = _trade_fields(t)
                if f["trade_id"]:
                    is_new = self.db.insert_trade(
                        f["trade_id"], f["ts_ms"], f["ticker"] or ticker,
                        f["yes_price"], f["no_price"], f["count"],
                        f["taker_side"], f["is_block"], "rest")
                    seen_known = seen_known or not is_new
                newest = max(newest, f["ts_ms"] // 1000)
            cursor = data.get("cursor")
            if not cursor or len(batch) < 100 or seen_known:
                break
        if newest:
            self._last_trade_ts[ticker] = newest
