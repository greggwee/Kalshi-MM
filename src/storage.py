"""SQLite persistence for recorded market data.

One database holds everything; tables are append-only event logs plus a
market-metadata table. Prices/counts are stored as REAL dollars/contracts
(source strings are exact 2-4dp decimals, safe in double precision).
"""
import json
import os
import sqlite3
import time
from typing import Iterable, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS markets (
    ticker TEXT PRIMARY KEY,
    series_ticker TEXT,
    title TEXT,
    close_time TEXT,
    fee_type TEXT,
    fee_multiplier REAL,
    price_level_structure TEXT,
    first_seen_ts INTEGER,
    raw_json TEXT,
    status TEXT,
    result TEXT
);
CREATE TABLE IF NOT EXISTS book_snapshots (
    id INTEGER PRIMARY KEY,
    ts_ms INTEGER NOT NULL,
    market_ticker TEXT NOT NULL,
    source TEXT NOT NULL,           -- 'ws' | 'rest'
    seq INTEGER,
    yes_levels TEXT NOT NULL,       -- JSON [[price, count], ...] best-first
    no_levels TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snap_mkt_ts ON book_snapshots (market_ticker, ts_ms);
CREATE TABLE IF NOT EXISTS book_deltas (
    id INTEGER PRIMARY KEY,
    ts_ms INTEGER NOT NULL,
    market_ticker TEXT NOT NULL,
    side TEXT NOT NULL,             -- 'yes' | 'no'
    price REAL NOT NULL,
    delta REAL NOT NULL,
    seq INTEGER,
    sid INTEGER
);
CREATE INDEX IF NOT EXISTS idx_delta_mkt_ts ON book_deltas (market_ticker, ts_ms);
CREATE TABLE IF NOT EXISTS trades (
    trade_id TEXT PRIMARY KEY,
    ts_ms INTEGER NOT NULL,
    market_ticker TEXT NOT NULL,
    yes_price REAL,
    no_price REAL,
    count REAL,
    taker_side TEXT,
    is_block INTEGER DEFAULT 0,
    source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trade_mkt_ts ON trades (market_ticker, ts_ms);
CREATE TABLE IF NOT EXISTS tickers (
    id INTEGER PRIMARY KEY,
    ts_ms INTEGER NOT NULL,
    market_ticker TEXT NOT NULL,
    raw_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ticker_mkt_ts ON tickers (market_ticker, ts_ms);
"""


class RecorderDB:
    def __init__(self, path: str, commit_every: int = 200, commit_secs: float = 5.0):
        dirname = os.path.dirname(path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.executescript(SCHEMA)
        # migrate databases created before status/result existed
        for col in ("status", "result"):
            try:
                self.conn.execute(f"ALTER TABLE markets ADD COLUMN {col} TEXT")
            except sqlite3.OperationalError:
                pass  # already present
        self.conn.commit()
        self._pending = 0
        self._last_commit = time.time()
        self._commit_every = commit_every
        self._commit_secs = commit_secs

    def _maybe_commit(self):
        self._pending += 1
        if (self._pending >= self._commit_every
                or time.time() - self._last_commit > self._commit_secs):
            self.commit()

    def commit(self):
        self.conn.commit()
        self._pending = 0
        self._last_commit = time.time()

    def close(self):
        self.commit()
        self.conn.close()

    # ---------- writers ----------

    def upsert_market(self, market: dict, series: Optional[dict] = None):
        series = series or {}
        self.conn.execute(
            """INSERT INTO markets (ticker, series_ticker, title, close_time, fee_type,
                                    fee_multiplier, price_level_structure, first_seen_ts,
                                    raw_json, status, result)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(ticker) DO UPDATE SET
                 close_time=excluded.close_time,
                 fee_type=excluded.fee_type,
                 fee_multiplier=excluded.fee_multiplier,
                 raw_json=excluded.raw_json,
                 status=excluded.status,
                 result=excluded.result""",
            (market.get("ticker"), series.get("ticker"), market.get("title"),
             market.get("close_time"), series.get("fee_type"),
             series.get("fee_multiplier"), market.get("price_level_structure"),
             int(time.time()), json.dumps(market),
             market.get("status"), market.get("result")))
        self._maybe_commit()

    def insert_snapshot(self, ts_ms: int, ticker: str, source: str,
                        yes_levels, no_levels, seq: Optional[int] = None):
        self.conn.execute(
            "INSERT INTO book_snapshots (ts_ms, market_ticker, source, seq, yes_levels, no_levels)"
            " VALUES (?,?,?,?,?,?)",
            (ts_ms, ticker, source, seq, json.dumps(yes_levels), json.dumps(no_levels)))
        self._maybe_commit()

    def insert_delta(self, ts_ms: int, ticker: str, side: str, price: float,
                     delta: float, seq: Optional[int], sid: Optional[int]):
        self.conn.execute(
            "INSERT INTO book_deltas (ts_ms, market_ticker, side, price, delta, seq, sid)"
            " VALUES (?,?,?,?,?,?,?)",
            (ts_ms, ticker, side, price, delta, seq, sid))
        self._maybe_commit()

    def insert_trade(self, trade_id: str, ts_ms: int, ticker: str, yes_price, no_price,
                     count, taker_side: Optional[str], is_block: bool, source: str) -> bool:
        """Returns True if the trade was new (dedupes on trade_id)."""
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO trades (trade_id, ts_ms, market_ticker, yes_price,"
            " no_price, count, taker_side, is_block, source) VALUES (?,?,?,?,?,?,?,?,?)",
            (trade_id, ts_ms, ticker,
             None if yes_price is None else float(yes_price),
             None if no_price is None else float(no_price),
             None if count is None else float(count),
             taker_side, int(is_block), source))
        self._maybe_commit()
        return cur.rowcount > 0

    def insert_ticker(self, ts_ms: int, ticker: str, raw: dict):
        self.conn.execute(
            "INSERT INTO tickers (ts_ms, market_ticker, raw_json) VALUES (?,?,?)",
            (ts_ms, ticker, json.dumps(raw)))
        self._maybe_commit()

    # ---------- small readers (for smoke checks / scripts) ----------

    def counts(self) -> dict:
        out = {}
        for table in ("markets", "book_snapshots", "book_deltas", "trades", "tickers"):
            out[table] = self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return out
