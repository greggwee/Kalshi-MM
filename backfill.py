"""One-time harvest of Kalshi's publicly available history for chosen series.

Pulls, per series: every market the live API still lists (the listing window is
~60-90 days before archival), each market's metadata + settlement outcome, and
its complete public trade tape. Everything lands in a SEPARATE database from
the live recorder (no writer contention with the running recorder process).

    python backfill.py                         # series from recorder_config.yaml
    python backfill.py --series KXHIGHNY,KXHIGHCHI --db data/kalshi_backfill.db

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

REQUEST_GAP_S = 0.12          # ~8 req/s against the public API: be a good citizen
TRADES_PAGE_LIMIT = 500


class Backfiller:
    def __init__(self, db: RecorderDB, logger: logging.Logger, environment: str = "prod"):
        self.db = db
        self.logger = logger
        self.base = PUBLIC_REST_BASE[environment]
        self.session = requests.Session()
        self.requests_made = 0

    def _get(self, path: str, params: dict) -> dict:
        time.sleep(REQUEST_GAP_S)
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

    def list_markets(self, series_ticker: str) -> list:
        """All markets of a series still in the live API window. The default
        listing omits settled markets, so sweep each status explicitly."""
        seen, markets = set(), []
        for status in ("settled", "closed", "open"):
            cursor = None
            while True:
                params = {"series_ticker": series_ticker, "status": status, "limit": 100}
                if cursor:
                    params["cursor"] = cursor
                data = self._get("/markets", params)
                batch = data.get("markets", [])
                for m in batch:
                    if m["ticker"] not in seen:
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
            "SELECT m.status, COUNT(t.trade_id) FROM markets m"
            " LEFT JOIN trades t ON t.market_ticker = m.ticker"
            " WHERE m.ticker = ? GROUP BY m.ticker", (ticker,)).fetchone()
        return bool(row and row[0] == "settled" and row[1] > 0)

    def backfill_trades(self, ticker: str) -> int:
        new, cursor = 0, None
        while True:
            params = {"ticker": ticker, "limit": TRADES_PAGE_LIMIT}
            if cursor:
                params["cursor"] = cursor
            data = self._get("/markets/trades", params)
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

    def run(self, series_list: list, skip_existing: bool = True):
        totals = {"markets": 0, "trades": 0, "skipped": 0}
        for series_ticker in series_list:
            series = self.fetch_series(series_ticker)
            markets = self.list_markets(series_ticker)
            self.logger.info(f"{series_ticker}: {len(markets)} markets listed "
                             f"(fee_type={series.get('fee_type')})")
            for i, market in enumerate(markets, 1):
                ticker = market["ticker"]
                if skip_existing and self.market_has_tape(ticker):
                    totals["skipped"] += 1
                    continue
                self.db.upsert_market(market, series)
                n = self.backfill_trades(ticker)
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
    bf = Backfiller(db, logger)
    if args.max_markets:  # test hook: monkey-limit the listing
        original = bf.list_markets
        bf.list_markets = lambda s: original(s)[:args.max_markets]
    try:
        bf.run(series_list, skip_existing=not args.no_skip)
    finally:
        db.commit()
        logger.info(f"row counts: {db.counts()}")
        db.close()


if __name__ == "__main__":
    main()
