"""Harvest of Kalshi's publicly available history for chosen series.

Pulls, per series: every market the live API still lists (about the last 60
days) plus every archived market under /historical (older than the cutoff
reported by GET /historical/cutoff), each market's metadata + settlement
outcome, and its complete public trade tape. Everything lands in a SEPARATE
database from the live recorder (no writer contention with the running
recorder process).

    python backfill.py                         # series from recorder_config.yaml
    python backfill.py --series KXHIGHNY,KXHIGHCHI --db data/kalshi_backfill.db
    python backfill.py --since 2026-07-01      # only markets closing on/after

Idempotent: re-runs skip settled markets whose tape is already stored, and
trade inserts dedupe on trade_id.
"""
import argparse
import logging
import time

import requests
import yaml

from src.recorder import PUBLIC_REST_BASE, _trade_fields
from src.storage import RecorderDB

REQUEST_GAP_S = 0.25          # ~4 req/s: the keyless API answers 429 near 8 req/s,
                              # and a live recorder shares the same budget
TRADES_PAGE_LIMIT = 500


class Backfiller:
    def __init__(self, db: RecorderDB, logger: logging.Logger, environment: str = "prod",
                 request_gap: float = REQUEST_GAP_S):
        self.request_gap = request_gap
        self.db = db
        self.logger = logger
        self.base = PUBLIC_REST_BASE[environment]
        self.session = requests.Session()
        self.requests_made = 0
        self._trades_cutoff = None    # ISO ts: trades created before it are archived

    def _get(self, path: str, params: dict) -> dict:
        time.sleep(self.request_gap)
        self.requests_made += 1
        for attempt in range(5):
            r = self.session.get(f"{self.base}{path}", params=params, timeout=20)
            if r.status_code == 429:
                wait = 5 * (attempt + 1)
                self.logger.warning(f"429 rate limited; sleeping {wait}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"still rate-limited after retries: {path}")

    def trades_cutoff(self) -> str:
        """Trades created before this instant live under /historical/trades."""
        if self._trades_cutoff is None:
            try:
                self._trades_cutoff = self._get("/historical/cutoff", {}).get(
                    "trades_created_ts") or ""
            except requests.HTTPError:
                self._trades_cutoff = ""
        return self._trades_cutoff

    def list_markets(self, series_ticker: str, since: str = None) -> list:
        """All markets of a series: the live API window (the default listing
        omits settled markets, so each status is swept explicitly) plus the
        /historical archive. `since` (ISO date) keeps markets closing on or
        after it."""
        seen, markets = set(), []
        sweeps = [("/markets", {"status": s}) for s in ("settled", "closed", "open")]
        sweeps.append(("/historical/markets", {}))
        for path, extra in sweeps:
            cursor = None
            while True:
                params = {"series_ticker": series_ticker, "limit": 1000, **extra}
                if cursor:
                    params["cursor"] = cursor
                try:
                    data = self._get(path, params)
                except requests.HTTPError as e:
                    self.logger.warning(f"{series_ticker}: listing {path} failed: {e}")
                    break
                batch = data.get("markets", [])
                for m in batch:
                    if m["ticker"] in seen:
                        continue
                    if since and (m.get("close_time") or "9999") < since:
                        continue
                    seen.add(m["ticker"])
                    markets.append(m)
                cursor = data.get("cursor")
                if not cursor or not batch:
                    break
        return markets

    def fetch_series(self, series_ticker: str) -> dict:
        try:
            return self._get(f"/series/{series_ticker}", {}).get("series", {})
        except requests.HTTPError:
            return {}

    def market_has_tape(self, ticker: str) -> bool:
        row = self.db.conn.execute(
            "SELECT m.result, COUNT(t.trade_id) FROM markets m"
            " LEFT JOIN trades t ON t.market_ticker = m.ticker"
            " WHERE m.ticker = ? GROUP BY m.ticker", (ticker,)).fetchone()
        # settled markets report status 'finalized' (older API: 'settled'),
        # so key on the outcome instead of the status string
        return bool(row and row[0] in ("yes", "no") and row[1] > 0)

    def backfill_trades(self, ticker: str, open_time: str = None,
                        close_time: str = None) -> int:
        """Full tape of one market. A market that opened before the archive
        cutoff has (some of) its prints under /historical/trades; one that
        closed after it has (some) on the live tape. Inserts dedupe."""
        cutoff = self.trades_cutoff()
        new = 0
        if not cutoff or not close_time or close_time >= cutoff:
            new += self._backfill_trades_from("/markets/trades", ticker)
        if cutoff and (not open_time or open_time < cutoff):
            new += self._backfill_trades_from("/historical/trades", ticker)
        return new

    def _backfill_trades_from(self, path: str, ticker: str) -> int:
        new, cursor = 0, None
        while True:
            params = {"ticker": ticker, "limit": TRADES_PAGE_LIMIT}
            if cursor:
                params["cursor"] = cursor
            data = self._get(path, params)
            batch = data.get("trades", [])
            for t in batch:
                f = _trade_fields(t)
                if f["trade_id"] and self.db.insert_trade(
                        f["trade_id"], f["ts_ms"], f["ticker"] or ticker,
                        f["yes_price"], f["no_price"], f["count"],
                        f["taker_side"], f["is_block"], "backfill"):
                    new += 1
            cursor = data.get("cursor")
            if not cursor or not batch:
                return new

    def run(self, series_list: list, skip_existing: bool = True, since: str = None):
        totals = {"markets": 0, "trades": 0, "skipped": 0}
        for series_ticker in series_list:
            series = self.fetch_series(series_ticker)
            markets = self.list_markets(series_ticker, since)
            self.logger.info(f"{series_ticker}: {len(markets)} markets listed "
                             f"(fee_type={series.get('fee_type')})")
            for i, market in enumerate(markets, 1):
                ticker = market["ticker"]
                if skip_existing and self.market_has_tape(ticker):
                    totals["skipped"] += 1
                    continue
                self.db.upsert_market(market, series)
                n = self.backfill_trades(ticker, market.get("open_time"),
                                         market.get("close_time"))
                totals["markets"] += 1
                totals["trades"] += n
                if i % 25 == 0:
                    self.logger.info(f"  {series_ticker} {i}/{len(markets)} markets, "
                                     f"+{totals['trades']} trades so far "
                                     f"({self.requests_made} requests)")
            self.db.commit()
        self.logger.info(f"DONE: {totals} in {self.requests_made} requests")


def main():
    parser = argparse.ArgumentParser(description="Backfill Kalshi public history")
    parser.add_argument("--series", help="comma-separated series tickers "
                        "(default: series from recorder_config.yaml)")
    parser.add_argument("--db", default="data/kalshi_backfill.db")
    parser.add_argument("--max-markets", type=int, default=None,
                        help="cap markets per series (for quick tests)")
    parser.add_argument("--since", default=None,
                        help="ISO date; only markets closing on or after it")
    parser.add_argument("--rps", type=float, default=1.0 / REQUEST_GAP_S,
                        help="request rate; lower it while the recorder is running")
    parser.add_argument("--no-skip", action="store_true",
                        help="re-fetch even settled markets that already have tape")
    args = parser.parse_args()

    if args.series:
        series_list = args.series.split(",")
    else:
        with open("recorder_config.yaml") as f:
            series_list = yaml.safe_load(f)["markets"]["series_tickers"]

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler("backfill.log")])
    logger = logging.getLogger("backfill")

    db = RecorderDB(args.db)
    bf = Backfiller(db, logger, request_gap=1.0 / args.rps)
    if args.max_markets:  # test hook: monkey-limit the listing
        original = bf.list_markets
        bf.list_markets = lambda s, since=None: original(s, since)[:args.max_markets]
    try:
        bf.run(series_list, skip_existing=not args.no_skip, since=args.since)
    finally:
        db.commit()
        logger.info(f"row counts: {db.counts()}")
        db.close()


if __name__ == "__main__":
    main()
