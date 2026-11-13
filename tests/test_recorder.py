"""REST recorder: market-list refresh, quote-driven polling, tape paging.

Regression for the Jul 31 - Aug 19 2026 outage: the recorder expanded its
series once at startup, so after those daily markets closed it polled dead
tickers for three weeks and stored nothing.
"""
import logging
import os
import tempfile
import unittest

import requests

from backfill import Backfiller
from src.recorder import RestPoller, refresh_outcomes
from src.storage import RecorderDB


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.ok = status < 400

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


class FakeExchange:
    """Just enough of the public REST API, keyed by path suffix."""

    def __init__(self):
        self.markets = {}       # ticker -> market dict (listing fields)
        self.books = {}         # ticker -> orderbook_fp dict
        self.trades = {}        # ticker -> list of trade dicts, newest first
        self.historical_markets = {}
        self.historical_trades = {}
        self.calls = []

    def add_market(self, ticker, bid="0.4000", ask="0.5000", volume="0.00",
                   status="active", result="", close_time="2026-11-14T06:00:00Z"):
        self.markets[ticker] = {
            "ticker": ticker, "event_ticker": ticker.rsplit("-", 1)[0],
            "status": status, "result": result, "close_time": close_time,
            "open_time": "2026-11-12T15:00:00Z",
            "yes_bid_dollars": bid, "yes_ask_dollars": ask,
            "yes_bid_size_fp": "10.00", "yes_ask_size_fp": "10.00",
            "last_price_dollars": bid, "volume_fp": volume, "open_interest_fp": "0.00"}
        self.books[ticker] = {"yes_dollars": [[bid, "10.00"]],
                              "no_dollars": [[f"{1 - float(ask):.4f}", "10.00"]]}
        self.trades.setdefault(ticker, [])

    def add_trade(self, ticker, n, ts="2026-11-12T16:00:00Z", archive=False):
        t = {"trade_id": f"{ticker}-{n}", "ticker": ticker, "created_time": ts,
             "yes_price_dollars": "0.4500", "no_price_dollars": "0.5500",
             "count_fp": "1.00", "taker_side": "yes"}
        (self.historical_trades if archive else self.trades).setdefault(
            ticker, []).insert(0, t)

    def get(self, url, params=None, timeout=None):
        params = params or {}
        path = url.split("/trade-api/v2", 1)[1]
        self.calls.append((path, dict(params)))
        if path == "/markets":
            if "tickers" in params:
                wanted = params["tickers"].split(",")
                return FakeResponse({"markets": [self.markets[t] for t in wanted
                                                 if t in self.markets]})
            return FakeResponse({"markets": [], "cursor": ""})
        if path == "/historical/cutoff":
            return FakeResponse({"trades_created_ts": "2026-09-13T00:00:00Z"})
        if path in ("/markets/trades", "/historical/trades"):
            store = self.trades if path == "/markets/trades" else self.historical_trades
            tape = store.get(params["ticker"], [])
            start = int(params.get("cursor") or 0)
            limit = int(params.get("limit", 100))
            page = tape[start:start + limit]
            more = start + limit < len(tape)
            return FakeResponse({"trades": page,
                                 "cursor": str(start + limit) if more else ""})
        if path.endswith("/orderbook"):
            ticker = path.split("/")[2]
            return FakeResponse({"orderbook_fp": self.books.get(ticker, {})})
        if path.startswith("/historical/markets/"):
            m = self.historical_markets.get(path.rsplit("/", 1)[1])
            return FakeResponse({"market": m}) if m else FakeResponse({}, 404)
        if path.startswith("/markets/"):
            m = self.markets.get(path.rsplit("/", 1)[1])
            return FakeResponse({"market": m}) if m else FakeResponse({}, 404)
        if path.startswith("/series/"):
            return FakeResponse({"series": {"ticker": path.rsplit("/", 1)[1],
                                            "fee_type": "quadratic"}})
        return FakeResponse({}, 404)

    def count(self, suffix):
        return sum(1 for path, _ in self.calls if path.endswith(suffix))


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


class RecorderTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = RecorderDB(os.path.join(self.tmp.name, "rec.db"))
        self.addCleanup(self.db.close)
        self.ex = FakeExchange()
        self.clock = Clock()
        self.logger = logging.getLogger("test_recorder")
        self.logger.addHandler(logging.NullHandler())
        self.logger.propagate = False

    def poller(self, tickers, **kw):
        kw.setdefault("poll_interval", 2.0)
        kw.setdefault("full_sweep_interval", 1e9)   # isolate quote-driven polling
        kw.setdefault("max_rps", 0)                 # unpaced unless a test asks
        return RestPoller("prod", tickers, self.db, self.logger, session=self.ex,
                          clock=self.clock, sleep=lambda s: None, **kw)

    def rows(self, table):
        return self.db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class TestQuoteDrivenPolling(RecorderTestCase):
    def test_first_cycle_records_touch_book_and_tape(self):
        self.ex.add_market("S-D1-A", volume="1.00")
        self.ex.add_trade("S-D1-A", 1)
        self.poller(["S-D1-A"]).run_cycle()
        self.assertEqual(self.rows("tickers"), 1)
        self.assertEqual(self.rows("book_snapshots"), 1)
        self.assertEqual(self.rows("trades"), 1)

    def test_unchanged_market_costs_one_bulk_request(self):
        for t in ("S-D1-A", "S-D1-B"):
            self.ex.add_market(t)
        p = self.poller(["S-D1-A", "S-D1-B"])
        p.run_cycle()
        self.ex.calls.clear()
        p.run_cycle()   # the sweep slice still polls one market per cycle
        self.assertEqual(self.ex.count("/markets"), 1)
        self.assertLessEqual(self.ex.count("/orderbook"), 1)
        self.assertEqual(self.rows("tickers"), 2)

    def test_touch_change_stores_quote_and_book_only_for_that_market(self):
        for t in ("S-D1-A", "S-D1-B", "S-D1-C"):
            self.ex.add_market(t)
        p = self.poller(["S-D1-A", "S-D1-B", "S-D1-C"])
        p.run_cycle()
        self.ex.add_market("S-D1-B", bid="0.4200")
        before = self.rows("book_snapshots")
        p.run_cycle()
        self.assertEqual(self.rows("tickers"), 4)
        self.assertEqual(self.rows("book_snapshots"), before + 1)
        last = self.db.conn.execute(
            "SELECT market_ticker FROM book_snapshots ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(last[0], "S-D1-B")

    def test_volume_change_polls_the_tape(self):
        self.ex.add_market("S-D1-A")
        p = self.poller(["S-D1-A"])
        p.run_cycle()
        self.ex.add_trade("S-D1-A", 1)
        self.ex.markets["S-D1-A"]["volume_fp"] = "1.00"
        p.run_cycle()
        self.assertEqual(self.rows("trades"), 1)

    def test_full_sweep_covers_every_market_within_the_interval(self):
        tickers = [f"S-D1-{i}" for i in range(10)]
        for t in tickers:
            self.ex.add_market(t)
        p = self.poller(tickers, full_sweep_interval=10.0)   # 5 cycles per sweep
        p.run_cycle()
        self.ex.calls.clear()
        for _ in range(5):
            p.run_cycle()
        swept = {path.split("/")[2] for path, _ in self.ex.calls
                 if path.endswith("/orderbook")}
        self.assertEqual(swept, set(tickers))

    def test_quote_poll_failure_does_not_stop_the_cycle(self):
        self.ex.add_market("S-D1-A")
        p = self.poller(["S-D1-A"], full_sweep_interval=2.0)
        real_get = self.ex.get

        def flaky(url, params=None, timeout=None):
            if url.endswith("/markets"):
                raise requests.ConnectionError("boom")
            return real_get(url, params=params, timeout=timeout)
        self.ex.get = flaky
        p.run_cycle()
        self.assertEqual(self.rows("book_snapshots"), 1)   # the sweep still ran


class TestPacing(RecorderTestCase):
    def test_requests_are_spaced_by_the_budget(self):
        self.ex.add_market("S-D1-A")
        slept = []

        def sleep(s):
            slept.append(s)
            self.clock.t += s
        p = RestPoller("prod", ["S-D1-A"], self.db, self.logger, session=self.ex,
                       clock=self.clock, sleep=sleep, max_rps=4.0)
        for _ in range(5):
            p.get("https://x/trade-api/v2/markets/S-D1-A/orderbook")
        self.assertAlmostEqual(sum(slept), 1.0)        # 5 requests at 4/s

    def test_429_is_retried_not_dropped(self):
        self.ex.add_market("S-D1-A")
        real_get, answers = self.ex.get, [429, 429, 200]

        def limited(url, params=None, timeout=None):
            status = answers.pop(0)
            return FakeResponse({}, 429) if status == 429 else real_get(url, params, timeout)
        self.ex.get = limited
        p = self.poller(["S-D1-A"])
        p._poll_book("S-D1-A")
        self.assertEqual(self.rows("book_snapshots"), 1)


class TestTapePaging(RecorderTestCase):
    def test_burst_larger_than_one_page_is_fully_stored(self):
        self.ex.add_market("S-D1-A")
        p = self.poller(["S-D1-A"])
        self.ex.add_trade("S-D1-A", 0)
        p._poll_trades("S-D1-A")
        for n in range(1, 251):
            self.ex.add_trade("S-D1-A", n)
        p._poll_trades("S-D1-A")
        self.assertEqual(self.rows("trades"), 251)

    def test_paging_stops_at_first_known_trade(self):
        self.ex.add_market("S-D1-A")
        p = self.poller(["S-D1-A"])
        for n in range(300):
            self.ex.add_trade("S-D1-A", n)
        p._poll_trades("S-D1-A")
        self.ex.calls.clear()
        self.ex.add_trade("S-D1-A", 300)
        p._poll_trades("S-D1-A")
        self.assertEqual(self.ex.count("/markets/trades"), 1)
        self.assertEqual(self.rows("trades"), 301)


class TestMarketRefresh(RecorderTestCase):
    def test_new_markets_are_picked_up_and_closed_ones_retired(self):
        self.ex.add_market("S-D1-A")
        self.ex.add_market("S-D2-A")
        listing = {"now": ["S-D1-A"]}
        p = self.poller(["S-D1-A"], refresh=lambda: listing["now"], refresh_interval=600.0)
        p.run_cycle()
        self.assertEqual(p.tickers, ["S-D1-A"])

        # D1 closes with one last print; D2 opens
        self.ex.add_trade("S-D1-A", 1)
        listing["now"] = ["S-D2-A"]
        self.clock.t += 601
        p.run_cycle()
        self.assertEqual(p.tickers, ["S-D2-A"])
        self.assertEqual(self.rows("trades"), 1)                      # final tape poll
        stored = {r[0] for r in self.db.conn.execute("SELECT ticker FROM markets")}
        self.assertIn("S-D2-A", stored)                               # metadata stored
        polled = {path.split("/")[2] for path, _ in self.ex.calls[-3:]
                  if path.endswith("/orderbook")}
        self.assertEqual(polled, {"S-D2-A"})                          # and recorded

    def test_refresh_not_called_before_interval(self):
        self.ex.add_market("S-D1-A")
        calls = []
        p = self.poller(["S-D1-A"], refresh=lambda: calls.append(1) or ["S-D1-A"],
                        refresh_interval=600.0)
        p.run_cycle()
        self.clock.t += 599
        p.run_cycle()
        self.assertEqual(calls, [])

    def test_failed_or_empty_refresh_keeps_the_current_list(self):
        self.ex.add_market("S-D1-A")

        def broken():
            raise requests.ConnectionError("dns")
        p = self.poller(["S-D1-A"], refresh=broken, refresh_interval=1.0)
        self.clock.t += 2
        p.run_cycle()
        self.assertEqual(p.tickers, ["S-D1-A"])
        p.refresh = lambda: []
        self.clock.t += 2
        p.run_cycle()
        self.assertEqual(p.tickers, ["S-D1-A"])


class TestOutcomes(RecorderTestCase):
    def test_finalized_market_outcome_comes_from_the_archive(self):
        self.ex.add_market("S-D1-A", close_time="2026-11-01T06:00:00Z")
        self.db.upsert_market(self.ex.markets["S-D1-A"],
                              {"ticker": "S", "fee_type": "quadratic"})
        # finalized markets leave the live endpoint
        archived = dict(self.ex.markets.pop("S-D1-A"), status="finalized", result="yes")
        self.ex.historical_markets["S-D1-A"] = archived
        n = refresh_outcomes(self.db, "prod", self.logger, session=self.ex,
                             now=1_800_000_000)
        self.assertEqual(n, 1)
        row = self.db.conn.execute(
            "SELECT status, result, fee_type FROM markets WHERE ticker='S-D1-A'").fetchone()
        self.assertEqual(row, ("finalized", "yes", "quadratic"))   # fee field kept

    def test_open_markets_are_not_queried(self):
        self.ex.add_market("S-D1-A", close_time="2099-01-01T00:00:00Z")
        self.db.upsert_market(self.ex.markets["S-D1-A"], {})
        refresh_outcomes(self.db, "prod", self.logger, session=self.ex, now=1_800_000_000)
        self.assertEqual(self.ex.calls, [])


class TestBackfillArchive(RecorderTestCase):
    def backfiller(self):
        bf = Backfiller(self.db, self.logger)
        bf.session = self.ex
        return bf

    def test_finalized_market_with_tape_is_skipped(self):
        """Settled markets report status 'finalized'; the old check compared
        against 'settled' and so re-downloaded every tape on every run."""
        self.ex.add_market("S-D1-A", status="finalized", result="no")
        self.db.upsert_market(self.ex.markets["S-D1-A"], {})
        self.db.insert_trade("x", 1, "S-D1-A", 0.5, 0.5, 1, "yes", False, "backfill")
        self.assertTrue(self.backfiller().market_has_tape("S-D1-A"))

    def test_market_straddling_the_cutoff_reads_both_tapes(self):
        self.ex.add_trade("S-D1-A", 1, archive=True)
        self.ex.add_trade("S-D1-A", 2)
        n = self.backfiller().backfill_trades(
            "S-D1-A", open_time="2026-09-12T15:00:00Z", close_time="2026-09-14T05:00:00Z")
        self.assertEqual(n, 2)

    def test_archived_market_skips_the_live_tape(self):
        self.ex.add_trade("S-D1-A", 1, archive=True)
        self.backfiller().backfill_trades(
            "S-D1-A", open_time="2026-08-01T15:00:00Z", close_time="2026-08-03T05:00:00Z")
        self.assertEqual(self.ex.count("/markets/trades"), 0)
        self.assertEqual(self.rows("trades"), 1)


if __name__ == "__main__":
    unittest.main()
