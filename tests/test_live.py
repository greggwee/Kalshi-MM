"""Tests for the live trading stack: rate limiting, credentials, the HTTP
client's retry/error handling, gateway normalization of V2 wire formats,
market selection, execution reconciliation, risk controls, the session
engine end to end (against tests/fake_exchange.py), and the diagnostics
report built from the session journal.

Run:  python -m unittest discover -s tests -v
"""
import base64
import json
import logging
import os
import shutil
import tempfile
import unittest
from unittest import mock

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

from helper import PerformanceTracker, book_side_of
from src.clients import Environment, KalshiAPIError, KalshiHttpClient, path_template
from src.credentials import CredentialsError, load_credentials, make_public_client, \
    mask_key_id
from src.diagnostics import build_report, render_markdown, write_report
from src.execution import OrderManager, classify_error
from src.gateway import (DryRunGateway, ExchangeGateway, fill_view, market_view, order_view,
                         position_view, trade_view)
from src.journal import Journal, read_journal
from src.ratelimit import RateLimiter, TokenBucket, endpoint_bucket
from src.risk import RiskManager
from src.selector import select_markets, session_specs
from src.session import TradingSession
from tests.fake_exchange import FakeClock, FakeExchange

logger = logging.getLogger("test_live")
logger.addHandler(logging.NullHandler())
logger.propagate = False

_RSA = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_RSA_PEM = _RSA.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()).decode()


class NullJournal:
    def __init__(self):
        self.events = []

    def write(self, event_type, **fields):
        self.events.append({"type": event_type, **fields})
        return self.events[-1]

    def of(self, event_type):
        return [e for e in self.events if e["type"] == event_type]


# --------------------------------------------------------------------------
# rate limiting
# --------------------------------------------------------------------------

class TestRateLimit(unittest.TestCase):
    def test_bucket_blocks_until_refilled(self):
        clock = FakeClock(0.0)
        b = TokenBucket(rate=10.0, capacity=20.0, clock=clock, sleep=clock.sleep)
        self.assertEqual(b.acquire(20), 0.0)            # burst up to capacity
        waited = b.acquire(10)                          # empty: needs 1s of refill
        self.assertAlmostEqual(waited, 1.0, places=6)
        self.assertAlmostEqual(clock(), 1.0, places=6)

    def test_cost_above_capacity_is_clamped(self):
        clock = FakeClock(0.0)
        b = TokenBucket(rate=10.0, capacity=5.0, clock=clock, sleep=clock.sleep)
        b.acquire(50)  # must not wait forever
        self.assertLessEqual(clock(), 1.0)

    def test_endpoint_costs(self):
        self.assertEqual(endpoint_bucket("GET", "/markets"), ("read", 10.0))
        self.assertEqual(endpoint_bucket("GET", "/portfolio/balance"), ("balance", 10.0))
        self.assertEqual(endpoint_bucket("POST", "/portfolio/events/orders"), ("write", 10.0))
        self.assertEqual(endpoint_bucket("DELETE", "/portfolio/events/orders/abc"), ("write", 2.0))
        self.assertEqual(endpoint_bucket("DELETE", "/portfolio/events/orders/batched", 25),
                         ("write", 50.0))

    def test_limiter_configure_applies_safety(self):
        lim = RateLimiter({"read": (100.0, 300.0)}, safety=0.5)
        self.assertAlmostEqual(lim.buckets["read"].rate, 50.0)
        self.assertAlmostEqual(lim.buckets["read"].capacity, 150.0)


# --------------------------------------------------------------------------
# credentials + signing
# --------------------------------------------------------------------------

class TestCredentials(unittest.TestCase):
    def test_inline_pem_with_escaped_newlines(self):
        env = {"DEMO_KEYID": "11111111-2222-4333-8444-555555555555",
               "DEMO_PRIVATE_KEY": _RSA_PEM.replace("\n", "\\n")}
        key_id, key = load_credentials(Environment.DEMO, env)
        self.assertEqual(key_id, env["DEMO_KEYID"])
        self.assertIsInstance(key, rsa.RSAPrivateKey)

    def test_base64_wrapped_pem(self):
        env = {"DEMO_KEYID": "k", "DEMO_PRIVATE_KEY": base64.b64encode(_RSA_PEM.encode()).decode()}
        _, key = load_credentials(Environment.DEMO, env)
        self.assertIsInstance(key, rsa.RSAPrivateKey)

    def test_keyfile(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        path = os.path.join(d, "k.pem")
        with open(path, "w") as f:
            f.write(_RSA_PEM)
        _, key = load_credentials(Environment.PROD, {"PROD_KEYID": "k", "PROD_KEYFILE": path})
        self.assertIsInstance(key, rsa.RSAPrivateKey)

    def test_missing_names_the_variables_without_values(self):
        with self.assertRaises(CredentialsError) as cm:
            load_credentials(Environment.DEMO, {"DEMO_KEYID": "secret-id"})
        self.assertIn("DEMO_PRIVATE_KEY", str(cm.exception))
        self.assertNotIn("secret-id", str(cm.exception))

    def test_mask(self):
        self.assertEqual(mask_key_id("abcd1234-0000-4000-8000-00000000ef56"), "abcd...ef56")

    def test_rsa_signature_verifies(self):
        c = KalshiHttpClient("k", _RSA, Environment.DEMO)
        h = c.request_headers("GET", "/portfolio/balance")
        msg = (h["KALSHI-ACCESS-TIMESTAMP"] + "GET/trade-api/v2/portfolio/balance").encode()
        _RSA.public_key().verify(base64.b64decode(h["KALSHI-ACCESS-SIGNATURE"]), msg,
                                 padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                                             salt_length=padding.PSS.DIGEST_LENGTH),
                                 hashes.SHA256())

    def test_ed25519_signature_verifies(self):
        key = ed25519.Ed25519PrivateKey.generate()
        c = KalshiHttpClient("k", key, Environment.DEMO)
        h = c.request_headers("POST", "/portfolio/events/orders?x=1")
        msg = (h["KALSHI-ACCESS-TIMESTAMP"] + "POST/trade-api/v2/portfolio/events/orders").encode()
        key.public_key().verify(base64.b64decode(h["KALSHI-ACCESS-SIGNATURE"]), msg)

    def test_public_client_sends_unsigned_requests(self):
        c = make_public_client(Environment.DEMO)
        self.assertFalse(c.authenticated)
        h = c.request_headers("GET", "/markets")
        self.assertFalse(any(k.startswith("KALSHI-ACCESS") for k in h))
        # a keyless dry run never calls the private balance endpoint
        stub = StubSession([])
        c.session = stub
        self.assertEqual(DryRunGateway(ExchangeGateway(c)).balance(),
                         {"balance": None, "portfolio_value": None})
        self.assertEqual(stub.calls, [])

    def test_demo_and_prod_hosts(self):
        self.assertEqual(KalshiHttpClient("k", _RSA, Environment.DEMO).host,
                         "https://external-api.demo.kalshi.co/trade-api/v2")
        self.assertEqual(KalshiHttpClient("k", _RSA, Environment.PROD).host,
                         "https://external-api.kalshi.com/trade-api/v2")


# --------------------------------------------------------------------------
# HTTP client behaviour
# --------------------------------------------------------------------------

def _resp(status, body=None):
    r = requests.Response()
    r.status_code = status
    r._content = b"" if body is None else json.dumps(body).encode()
    r.headers["Date"] = "Tue, 27 Oct 2026 15:00:00 GMT"
    return r


class StubSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class OrdersEndpoint:
    """GET /portfolio/orders as it behaves on demo (Oct 2026): `ticker`
    matches exactly one market, so a comma-joined list matches nothing, and
    `tickers` is ignored."""

    def __init__(self, orders):
        self.orders = orders
        self.calls = []

    def request(self, method, url, **kw):
        params = kw.get("params") or {}
        self.calls.append((method, url, kw))
        rows = [o for o in self.orders if params.get("status") in (None, o["status"])]
        if "ticker" in params:
            rows = [o for o in rows if o["ticker"] == params["ticker"]]
        return _resp(200, {"orders": rows, "cursor": ""})


class TestHttpClient(unittest.TestCase):
    def make(self, responses):
        c = KalshiHttpClient("k", _RSA, Environment.DEMO,
                             limiter=RateLimiter({"read": (1e6, 1e6), "write": (1e6, 1e6),
                                                  "balance": (1e6, 1e6)}))
        c.session = StubSession(responses)
        return c

    @mock.patch("src.clients.time.sleep")
    def test_429_is_retried_even_for_post(self, _sleep):
        c = self.make([_resp(429, {"error": "too many requests"}), _resp(201, {"order_id": "x"})])
        self.assertEqual(c.create_order_v2("T", "bid", 1, 0.5)["order_id"], "x")
        self.assertEqual(c.stats.retries, 1)

    @mock.patch("src.clients.time.sleep")
    def test_5xx_retried_for_get_not_for_create(self, _sleep):
        c = self.make([_resp(503), _resp(200, {"markets": []})])
        self.assertEqual(c.get_markets(), {"markets": []})
        c = self.make([_resp(503, {"error": {"code": "unavailable"}})])
        with self.assertRaises(KalshiAPIError):
            c.create_order_v2("T", "bid", 1, 0.5)
        self.assertEqual(len(c.session.calls), 1)  # never blindly re-sent

    @mock.patch("src.clients.time.sleep")
    def test_connection_error_not_retried_for_create(self, _sleep):
        c = self.make([requests.ConnectionError("boom")])
        with self.assertRaises(requests.ConnectionError):
            c.create_order_v2("T", "bid", 1, 0.5)

    def test_error_body_is_parsed(self):
        c = self.make([_resp(400, {"error": {"code": "post_only_cross",
                                             "message": "order would cross"}})])
        with self.assertRaises(KalshiAPIError) as cm:
            c.create_order_v2("T", "bid", 1, 0.5)
        self.assertEqual(cm.exception.code, "post_only_cross")
        self.assertEqual(classify_error(cm.exception), "post_only_cross")
        self.assertIsInstance(cm.exception, requests.HTTPError)  # backward compatible

    def test_204_returns_empty_dict(self):
        self.assertEqual(self.make([_resp(204)]).cancel_all_orders_v2(), {})

    def test_v2_bodies_and_routing(self):
        c = self.make([_resp(201, {"order_id": "x"}), _resp(200, {"order_id": "x"}),
                       _resp(200, {"order_id": "x", "reduced_by": "2.00"})])
        c.create_order_v2("T", "ask", 2, 0.57, expiration_ts=123.9, cancel_order_on_pause=True)
        c.amend_order_v2("x", "T", "ask", 2, 0.58, expiration_ts=456)
        c.cancel_order_v2("x", market_ticker="T")
        create, amend, cancel = c.session.calls
        self.assertTrue(create[1].endswith("/trade-api/v2/portfolio/events/orders"))
        self.assertEqual(create[2]["json"]["count"], "2.00")
        self.assertEqual(create[2]["json"]["price"], "0.5700")
        self.assertEqual(create[2]["json"]["expiration_time"], 123)
        self.assertTrue(create[2]["json"]["post_only"])
        self.assertTrue(create[2]["json"]["cancel_order_on_pause"])
        self.assertEqual(amend[2]["json"]["expiration_time"], 456)
        self.assertEqual(cancel[2]["params"], {"market_ticker": "T"})

    def test_pagination(self):
        c = self.make([_resp(200, {"orders": [{"order_id": 1, "ticker": "A"},
                                              {"order_id": 9, "ticker": "Z"}], "cursor": "c1"}),
                       _resp(200, {"orders": [{"order_id": 2, "ticker": "B"}], "cursor": ""})])
        self.assertEqual([o["order_id"] for o in c.get_all_orders(tickers=["A", "B"])], [1, 2])
        self.assertNotIn("ticker", c.session.calls[0][2]["params"])
        self.assertEqual(c.session.calls[1][2]["params"]["cursor"], "c1")

    def test_order_listing_matches_live_endpoint(self):
        """Regression: `ticker=A,B` matched nothing on demo, so the session
        never saw its own orders and re-created them every cycle."""
        orders = [{"order_id": "a", "ticker": "A", "status": "resting", "book_side": "bid",
                   "yes_price_dollars": "0.4000", "remaining_count_fp": "2.00",
                   "fill_count_fp": "0.00"},
                  {"order_id": "b", "ticker": "B", "status": "resting", "book_side": "ask",
                   "yes_price_dollars": "0.6000", "remaining_count_fp": "2.00",
                   "fill_count_fp": "0.00"},
                  {"order_id": "z", "ticker": "Z", "status": "resting", "book_side": "bid",
                   "yes_price_dollars": "0.1000", "remaining_count_fp": "1.00",
                   "fill_count_fp": "0.00"}]
        c = self.make([])
        c.session = OrdersEndpoint(orders)
        resting = ExchangeGateway(c).resting_orders(["A", "B"])
        self.assertEqual({t: [o["order_id"] for o in v] for t, v in resting.items()},
                         {"A": ["a"], "B": ["b"]})
        self.assertEqual(ExchangeGateway(c).resting_orders(["B"])["B"][0]["side"], "ask")

    def test_stats_and_templates(self):
        self.assertEqual(path_template("/markets/KXFOO-26/orderbook"), "/markets/{x}/orderbook")
        self.assertEqual(path_template("/portfolio/events/orders/abc-123/amend"),
                         "/portfolio/events/orders/{x}/amend")
        c = self.make([_resp(200, {"orderbook_fp": {}}), _resp(404, {"error": "nope"})])
        c.get_orderbook("KX-1")
        with self.assertRaises(KalshiAPIError):
            c.get_orderbook("KX-2")
        snap = c.stats.snapshot()
        ep = snap["endpoints"]["GET /markets/{x}/orderbook"]
        self.assertEqual(ep["count"], 2)
        self.assertEqual(ep["errors"], {"404": 1})


# --------------------------------------------------------------------------
# wire-format normalization
# --------------------------------------------------------------------------

class TestNormalization(unittest.TestCase):
    def test_v2_order_shape(self):
        o = order_view({"order_id": "o1", "ticker": "T", "book_side": "ask",
                        "outcome_side": "no", "yes_price_dollars": "0.5700",
                        "no_price_dollars": "0.4300", "remaining_count_fp": "3.00",
                        "fill_count_fp": "1.00", "initial_count_fp": "4.00",
                        "status": "resting", "expiration_time": "2026-10-27T16:00:00Z"})
        self.assertEqual((o["side"], o["price"], o["remaining"], o["filled"]), ("ask", 0.57, 3.0, 1.0))
        self.assertIsNotNone(o["expiration_ts"])

    def test_legacy_order_shape_still_works(self):
        o = order_view({"order_id": "o1", "ticker": "T", "action": "buy", "side": "no",
                        "no_price": 40, "remaining_count": 2})
        self.assertEqual((o["side"], o["price"], o["remaining"]), ("ask", 0.6, 2.0))

    def test_fill_direction_without_legacy_fields(self):
        f = fill_view({"fill_id": "f1", "trade_id": "t1", "order_id": "o1", "market_ticker": "T",
                       "ticker": "T", "outcome_side": "no", "book_side": "ask",
                       "count_fp": "2.00", "yes_price_dollars": "0.6100",
                       "no_price_dollars": "0.3900", "is_taker": False,
                       "fee_cost": "0.0100", "ts": 1800000000})
        self.assertEqual((f["side"], f["qty"], f["price"], f["fee"]), ("ask", 2.0, 0.61, 0.01))
        # regression: before book_side support this fill was booked as a LONG
        t = PerformanceTracker(logger, log_path=None)
        t.on_fill({"book_side": "ask", "count_fp": "2.00", "yes_price_dollars": "0.61"}, 0.6)
        self.assertEqual(t.position, -2.0)

    def test_book_side_precedence(self):
        self.assertEqual(book_side_of({"book_side": "bid", "action": "sell", "side": "yes"}), "bid")
        self.assertEqual(book_side_of({"outcome_side": "yes"}), "bid")
        self.assertEqual(book_side_of({"action": "sell", "side": "no"}), "bid")

    def test_trade_taker_direction_prefers_canonical_fields(self):
        base = {"trade_id": "t", "ticker": "T", "count_fp": "3.00",
                "yes_price_dollars": "0.4400", "created_time": "2026-10-27T15:00:00Z"}
        self.assertEqual(trade_view({**base, "taker_outcome_side": "no",
                                     "taker_side": "yes"})["taker_side"], "no")
        self.assertEqual(trade_view({**base, "taker_book_side": "bid"})["taker_side"], "yes")
        self.assertEqual(trade_view({**base, "taker_side": "no"})["taker_side"], "no")
        self.assertIsNone(trade_view(base)["taker_side"])
        from src.recorder import _trade_fields  # research recorder shares the parsing
        self.assertEqual(_trade_fields({**base, "taker_book_side": "ask"})["taker_side"], "no")

    def test_position_and_market_views(self):
        p = position_view({"ticker": "T", "position_fp": "-3.00", "realized_pnl_dollars": "1.25",
                           "fees_paid_dollars": "0.07", "market_exposure_dollars": "1.80"})
        self.assertEqual((p["position"], p["realized_pnl"], p["fees_paid"]), (-3.0, 1.25, 0.07))
        m = market_view({"ticker": "KXA-26-B1", "event_ticker": "KXA-26", "status": "active",
                         "close_time": "2026-10-30T00:00:00Z", "yes_bid_dollars": "0.4100",
                         "yes_ask_dollars": "0.4400", "volume_24h_fp": "120.00",
                         "open_interest_fp": "900.00", "last_price_dollars": "0.4200",
                         "price_ranges": [{"start": "0.00", "end": "1.00", "step": "0.01"}]})
        self.assertTrue(m["tradable"])
        self.assertEqual((m["series_ticker"], m["yes_bid"], m["volume_24h"], m["max_tick"]),
                         ("KXA", 0.41, 120.0, 0.01))


# --------------------------------------------------------------------------
# selection
# --------------------------------------------------------------------------

class TestSelector(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        ex = self.ex = FakeExchange(self.clock)
        deep = {0.49: 200, 0.48: 300, 0.46: 500}
        ex.add_market("LIQ1", deep, {0.50: 200, 0.49: 300}, event="E1", volume_24h=5000, open_interest=9000)
        ex.add_market("LIQ1b", deep, {0.50: 200, 0.49: 300}, event="E1", volume_24h=4000, open_interest=8000)
        ex.add_market("LIQ2", {0.30: 100, 0.29: 100}, {0.69: 100}, event="E2", volume_24h=900, open_interest=2000)
        ex.add_market("ILQ1", {0.20: 3}, {0.70: 2}, event="E3", volume_24h=0, open_interest=10)
        ex.add_market("ILQ2", {0.40: 1}, {0.45: 1}, event="E4", volume_24h=2, open_interest=0)
        ex.add_market("DEAD", {}, {}, event="E5")
        ex.add_market("TAIL", {0.02: 10}, {0.97: 10}, event="E6", volume_24h=10000)

    def test_picks_by_class_one_per_event(self):
        sel = select_markets(self.ex, {"liquid_count": 2, "illiquid_count": 2,
                                       "min_hours_to_close": 1}, logger, now=self.clock())
        liquid = [c["ticker"] for c in sel["liquid"]]
        illiquid = [c["ticker"] for c in sel["illiquid"]]
        self.assertEqual(liquid, ["LIQ1", "LIQ2"])          # LIQ1b: same event as LIQ1
        self.assertEqual(sorted(illiquid), ["ILQ1", "ILQ2"])
        self.assertEqual(sel["rejected"].get("no_mid"), 1)          # DEAD
        self.assertEqual(sel["rejected"].get("mid_out_of_band"), 1)  # TAIL
        specs = session_specs(sel)
        self.assertEqual({s["liquidity_class"] for s in specs}, {"liquid", "illiquid"})
        self.assertEqual(sel["liquid"][0]["fee_type"], "quadratic")

    def test_illiquid_skips_void_books_and_finds_thin_live_ones(self):
        # a 1c bid under a 97c ask: in the mid band, but there is no price there
        self.ex.add_market("VOID", {0.02: 5}, {0.03: 5}, event="E7")
        self.ex.add_market("THIN", {0.40: 4}, {0.48: 3}, event="E8", volume_24h=3,
                           open_interest=40)
        sel = select_markets(self.ex, {"liquid_count": 1, "illiquid_count": 9,
                                       "min_hours_to_close": 1, "book_check_top_n": 1},
                             logger, now=self.clock())
        picks = [c["ticker"] for c in sel["illiquid"]]
        self.assertNotIn("VOID", picks)
        self.assertEqual(sorted(picks[:2]), ["ILQ2", "THIN"])   # traded in the last 24h
        self.assertEqual(picks[2:], ["ILQ1"])                   # history only

    def test_manual_override(self):
        sel = select_markets(self.ex, {"tickers": {"liquid": ["LIQ2"], "illiquid": ["ILQ1"]}},
                             logger, now=self.clock())
        self.assertEqual([c["ticker"] for c in sel["liquid"]], ["LIQ2"])
        self.assertEqual([c["ticker"] for c in sel["illiquid"]], ["ILQ1"])


# --------------------------------------------------------------------------
# execution + risk units
# --------------------------------------------------------------------------

class TestOrderManager(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.ex = FakeExchange(self.clock)
        self.ex.add_market("T", {0.40: 10}, {0.50: 10})   # others: 0.40 / 0.50
        self.j = NullJournal()
        self.om = OrderManager(self.ex, self.j, logger, order_expiration_s=300, clock=self.clock)

    def reconcile(self, bid, ask):
        orders = self.ex.resting_orders(["T"])["T"]
        self.om.reconcile("T", {"bid": bid, "ask": ask}, orders)

    def test_create_keep_amend_cancel(self):
        self.reconcile((0.42, 2), (0.48, 2))
        self.assertEqual(self.ex.calls["create"], 2)
        self.reconcile((0.42, 2), (0.48, 2))
        self.assertEqual(self.om.counts["T"]["keep"], 2)
        self.reconcile((0.41, 2), (0.48, 2))          # bid backs off: amend (can't cross)
        self.assertEqual(self.ex.calls["amend"], 1)
        self.assertEqual(self.ex.calls["create"], 2)
        self.reconcile((0.43, 2), (0.48, 2))          # bid steps toward: cancel + post-only
        self.assertEqual((self.ex.calls["cancel"], self.ex.calls["create"]), (1, 3))
        self.assertEqual(self.ex.calls["amend"], 1)
        self.reconcile(None, (0.48, 2))
        self.assertEqual(self.ex.calls["cancel"], 2)
        self.assertEqual([o["side"] for o in self.ex.our_orders()], ["ask"])

    def test_market_moving_up_shifts_ask_before_bid(self):
        self.reconcile((0.42, 2), (0.48, 2))
        self.reconcile((0.49, 2), (0.52, 2))   # new bid would cross our OWN 0.48 ask
        self.assertFalse([e for e in self.j.of("order") if not e["ok"]])
        prices = {o["side"]: o["price"] for o in self.ex.our_orders()}
        self.assertEqual(prices, {"bid": 0.49, "ask": 0.52})

    def test_amend_policy_opt_out(self):
        om = OrderManager(self.ex, self.j, logger, aggressive_reprice="amend", clock=self.clock)
        om.reconcile("T", {"bid": (0.42, 2), "ask": None}, [])
        om.reconcile("T", {"bid": (0.45, 2), "ask": None}, self.ex.resting_orders(["T"])["T"])
        self.assertEqual((self.ex.calls["amend"], self.ex.calls["cancel"]), (1, 0))

    def test_failed_cancel_blocks_recreate(self):
        self.reconcile((0.42, 2), None)
        stale = self.ex.resting_orders(["T"])["T"]
        self.ex.taker_hits("T", "bid", 2)              # fills before our cancel lands
        self.om.reconcile("T", {"bid": (0.44, 2), "ask": None}, stale)
        self.assertEqual(self.ex.calls["create"], 1)    # no blind re-create
        self.assertEqual(self.ex.our_orders(), [])

    def test_partial_fill_replenish_threshold(self):
        self.reconcile((0.42, 4), None)
        self.ex.taker_hits("T", "bid", 1)          # 3 left >= 0.5 * 4: keep queue spot
        self.reconcile((0.42, 4), None)
        self.assertEqual(self.ex.calls["amend"], 0)
        self.ex.taker_hits("T", "bid", 2)          # 1 left < 2: top back up
        self.reconcile((0.42, 4), None)
        self.assertEqual(self.ex.calls["amend"], 1)
        o = self.ex.our_orders()[0]
        self.assertEqual((o["remaining"], o["filled"]), (4.0, 3.0))  # count = filled + desired

    def test_size_down_and_expiry_refresh(self):
        self.reconcile((0.42, 4), None)
        self.reconcile((0.42, 2), None)
        self.assertEqual(self.ex.our_orders()[0]["remaining"], 2.0)
        self.clock.advance(250)                    # 50s of 300s left < 1/3 -> refresh
        self.reconcile((0.42, 2), None)
        amends = [e for e in self.j.of("order") if e["action"] == "amend"]
        self.assertEqual([a["why"] for a in amends], ["size_down", "refresh_expiry"])

    def test_duplicates_cancelled(self):
        self.ex.create_order("T", "bid", 2, 0.41)
        self.ex.create_order("T", "bid", 2, 0.30)
        self.reconcile((0.41, 2), None)
        self.assertEqual(len(self.ex.our_orders()), 1)

    def test_view_covers_listing_lag(self):
        self.ex.listing_lag_s = 1.0
        self.reconcile((0.42, 2), (0.48, 2))
        self.assertEqual(self.ex.resting_orders(["T"])["T"], [])   # listing lags the creates
        self.reconcile((0.42, 2), (0.48, 2))                       # ... nothing is re-created
        self.assertEqual(self.ex.calls["create"], 2)
        view = self.om.view("T", self.ex.resting_orders(["T"])["T"])
        self.assertEqual(sorted(o["side"] for o in view), ["ask", "bid"])
        self.assertTrue(all(o["unconfirmed"] for o in view))
        self.assertEqual(self.om.view("T", view), view)            # idempotent
        self.clock.advance(1.5)                                    # listing catches up
        self.reconcile((0.41, 2), (0.48, 2))                       # bid backs off: amend
        self.reconcile((0.41, 2), (0.48, 2))                       # old price still listed
        self.assertEqual(self.ex.calls["amend"], 1)
        self.reconcile((0.41, 2), None)
        self.reconcile((0.41, 2), None)                            # cancelled ask still listed
        self.assertEqual(self.ex.calls["cancel"], 1)
        self.clock.advance(10)                                     # memory expires
        self.reconcile((0.41, 2), None)
        self.assertEqual((self.ex.calls["create"], self.ex.calls["amend"],
                          self.ex.calls["cancel"]), (2, 1, 1))
        self.assertFalse([e for e in self.j.of("order") if not e["ok"]])

    def test_fill_before_listing_leaves_no_phantom(self):
        self.ex.listing_lag_s = 1.0
        self.reconcile((0.42, 2), None)
        oid = self.ex.our_orders()[0]["order_id"]
        self.ex.taker_hits("T", "bid", 2)
        self.om.on_fill({"order_id": oid, "ticker": "T", "qty": 2.0})
        self.assertEqual(self.om.view("T", self.ex.resting_orders(["T"])["T"]), [])

    def test_amend_of_vanished_order_is_not_a_reject(self):
        rejects = []
        self.om.on_reject = rejects.append
        self.reconcile((0.42, 2), None)
        stale = self.ex.resting_orders(["T"])["T"]
        self.ex.taker_hits("T", "bid", 2)
        self.om.reconcile("T", {"bid": (0.41, 2), "ask": None}, stale)  # amend -> 404
        self.assertEqual(self.j.of("order")[-1]["reject_reason"], "not_found")
        self.assertEqual(rejects, [])
        self.assertEqual(self.om.view("T", stale), [])

    def test_reject_is_journaled_and_counted(self):
        rejects = []
        self.om.on_reject = rejects.append
        self.reconcile((0.55, 2), None)            # crosses the 0.50 ask
        e = self.j.of("order")[-1]
        self.assertFalse(e["ok"])
        self.assertEqual(e["reject_reason"], "post_only_cross")
        self.assertEqual(rejects, ["T"])


class TestRisk(unittest.TestCase):
    def test_gross_cap_only_blocks_increasing_side(self):
        r = RiskManager({}, NullJournal(), logger)
        self.assertEqual(r.gate_sizes(5, 2, 2, 0)[:2], (0, 2))     # long: bids add risk
        self.assertEqual(r.gate_sizes(-5, 2, 2, 0)[:2], (2, 0))    # short: asks add risk
        self.assertEqual(r.gate_sizes(0, 2, 2, 1)[:2], (1, 1))
        self.assertEqual(r.gate_sizes(0, 2, 2, 10)[:2], (2, 2))

    def test_kill_and_halt_events(self):
        j = NullJournal()
        r = RiskManager({"max_session_loss": 5, "max_market_loss": 2,
                         "max_consecutive_errors": 2}, j, logger)
        r.check_market("A", -1.0)
        self.assertNotIn("A", r.halted)
        r.check_market("A", -2.5)
        self.assertEqual(r.halted["A"], "max_market_loss")
        r.record_error("x")
        self.assertIsNone(r.kill_reason)
        r.record_error("y")
        self.assertEqual(r.kill_reason, "consecutive_errors")
        self.assertEqual(len(j.of("risk")), 2)


# --------------------------------------------------------------------------
# session engine end to end
# --------------------------------------------------------------------------

STRATEGY = {"gamma": 1.0, "default_k": 40.0, "base_order_size": 2, "max_position": 10,
            "min_spread": 0.02, "premium_scale": 0.02, "max_horizon_h": 24.0}


class TestSession(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.clock = FakeClock()
        ex = self.ex = FakeExchange(self.clock)
        ex.add_market("LIQ", {0.48: 50, 0.47: 100, 0.45: 200}, {0.50: 50, 0.49: 100, 0.47: 200})
        ex.add_market("ILQ", {0.30: 5}, {0.60: 5})
        self.journal = Journal(os.path.join(self.dir, "events.jsonl"))
        self.addCleanup(self.journal.close)

    def session(self, risk=None, execution=None, specs=None, gw=None, **kw):
        cfg = {"environment": "demo", "strategy": STRATEGY, "risk": risk or {},
               "execution": execution or {},
               "session": {"cycle_s": 2.0, "trades_poll_s": 0, "summary_every_cycles": 1000}}
        specs = specs or [{"ticker": "LIQ", "liquidity_class": "liquid",
                           "selection": {"mid": 0.49}},
                          {"ticker": "ILQ", "liquidity_class": "illiquid"}]
        return TradingSession(gw or self.ex, specs, cfg, self.journal, logger,
                              clock=self.clock, sleep=self.clock.sleep, **kw)

    def ticks(self, ticker):
        return [e for e in read_journal(os.path.join(self.dir, "events.jsonl"))
                if e["type"] == "tick" and e["ticker"] == ticker]

    def test_full_lifecycle_and_report(self):
        s = self.session(risk={"max_market_loss": 0.5})
        s.start()
        s.run_cycle()
        for t in ("LIQ", "ILQ"):
            sides = sorted(o["side"] for o in self.ex.our_orders(t))
            self.assertEqual(sides, ["ask", "bid"], t)
        liq = {o["side"]: o for o in self.ex.our_orders("LIQ")}
        self.assertLess(liq["bid"]["price"], 0.50)      # post-only: never crosses
        self.assertGreater(liq["ask"]["price"], 0.48)
        self.assertEqual(liq["bid"]["remaining"], 2.0)

        # market moves up 3c -> the ask backs off via amend first, then the bid
        # steps up via cancel + post-only create; nothing is rejected
        self.clock.advance(2)
        self.ex.set_book("LIQ", {0.51: 50, 0.50: 100}, {0.47: 50, 0.46: 100})
        creates, amends = self.ex.calls["create"], self.ex.calls["amend"]
        s.run_cycle()
        self.assertEqual(self.ex.calls["amend"], amends + 1)
        self.assertEqual(self.ex.calls["create"], creates + 1)
        moved = {o["side"]: o["price"] for o in self.ex.our_orders("LIQ")}
        self.assertGreater(moved["bid"], liq["bid"]["price"])
        self.assertGreater(moved["ask"], liq["ask"]["price"])

        # a taker sells into our bid -> long 2, quotes skew down
        self.ex.taker_hits("LIQ", "bid", 2)
        self.clock.advance(2)
        s.run_cycle()
        tick = self.ticks("LIQ")[-1]
        self.assertEqual(tick["q"], 2.0)
        self.assertLess(tick["reservation"], tick["mid"])
        self.assertEqual(s.workers["LIQ"].fill_count, 1)
        s.run_cycle()                                   # same fill polled again: deduped
        self.assertEqual(s.workers["LIQ"].fill_count, 1)

        # price collapses: mark-to-market loss > $0.50 halts the market
        self.clock.advance(2)
        self.ex.set_book("LIQ", {0.20: 50}, {0.78: 50})
        s.run_cycle()
        self.assertIn("LIQ", s.risk.halted)
        self.clock.advance(2)
        s.run_cycle()
        self.assertEqual(self.ex.our_orders("LIQ"), [])
        self.assertTrue(self.ex.our_orders("ILQ"))      # other market unaffected

        self.clock.advance(70)                          # let the 10s/60s markouts mature
        s.run_cycle()
        s.request_stop("test")
        summary = s.shutdown()
        self.assertEqual(self.ex.our_orders(), [])
        self.assertEqual(summary["orders_left_resting"], 0)
        self.assertEqual(summary["markets"]["LIQ"]["fills"], 1)
        self.assertLess(summary["markets"]["LIQ"]["net_pnl"], 0)
        self.journal.close()

        report = write_report(self.dir)
        liq_r = next(m for m in report["markets"] if m["ticker"] == "LIQ")
        self.assertEqual(liq_r["fills"]["count"], 1)
        self.assertEqual(liq_r["fills"]["bought"], 2.0)
        self.assertIsNotNone(liq_r["fills"]["markout_10s_c"])
        self.assertIsNotNone(liq_r["fills"]["markout_60s_c"])
        self.assertLess(liq_r["fills"]["markout_60s_c"], 0)         # it went against us
        self.assertEqual(liq_r["market"]["public_trades"], 1)
        self.assertEqual(liq_r["fills"]["market_share_pct"], 100.0)
        self.assertIn("halted:max_market_loss", liq_r["stand_down_pct"])
        self.assertEqual(set(report["classes"]), {"liquid", "illiquid"})
        self.assertTrue(any(f["area"] == "LIQ" for f in report["findings"]))
        with open(os.path.join(self.dir, "report.md")) as f:
            md = f.read()
        self.assertIn("## Liquid vs illiquid", md)
        self.assertIn("### LIQ (liquid)", md)

    def test_cross_policy_clamp_and_skip(self):
        self.ex.pos["LIQ"] = -9.0     # heavy short: reservation far above mid
        s = self.session(specs=[{"ticker": "LIQ", "liquidity_class": "liquid"}])
        s.start()
        s.run_cycle()
        t = self.ticks("LIQ")[-1]
        self.assertTrue(t["bid_crossed"])
        self.assertAlmostEqual(t["bid"], 0.49)          # one tick inside the 0.50 ask
        self.assertEqual(self.ex.calls["create"], 2)    # no post-only reject

        ex2 = FakeExchange(self.clock)
        ex2.add_market("LIQ", {0.48: 50}, {0.50: 50})
        ex2.pos["LIQ"] = -9.0
        s2 = self.session(specs=[{"ticker": "LIQ", "liquidity_class": "liquid"}], gw=ex2,
                          execution={"cross_policy": "skip"})
        s2.start()
        s2.run_cycle()
        self.assertEqual([o["side"] for o in ex2.our_orders()], ["ask"])

    def test_read_outage_pulls_quotes_then_kills(self):
        s = self.session(risk={"max_consecutive_errors": 5})
        s.start()
        s.run_cycle()
        self.assertEqual(len(self.ex.our_orders()), 4)   # all created THIS cycle
        self.ex.fail_reads = 100
        s.run_cycle()
        s.run_cycle()
        self.assertEqual(len(self.ex.our_orders()), 4)   # tolerate a short blip
        s.run_cycle()                                    # 3rd failed read: pull blind
        self.assertEqual(self.ex.our_orders(), [])
        self.assertIsNone(s.risk.kill_reason)
        s.run_cycle()
        s.run_cycle()
        self.assertEqual(s.risk.kill_reason, "consecutive_errors")
        summary = s.shutdown()                           # reads still failing
        self.assertEqual(summary["orders_left_resting"], 0)
        self.assertFalse(summary["orders_left_verified"])

    def test_listing_lag_no_duplicates_and_clean_shutdown(self):
        self.ex.listing_lag_s = 1.0
        s = self.session()
        s.start()
        for n in range(8):                       # cycles faster than the listing catches up
            if n == 4:
                self.ex.set_book("LIQ", {0.51: 50, 0.50: 100}, {0.47: 50, 0.46: 100})
            s.run_cycle()
            for t in ("LIQ", "ILQ"):
                sides = [o["side"] for o in self.ex.our_orders(t)]
                self.assertEqual(sorted(sides), ["ask", "bid"], f"cycle {n} {t}")
            self.clock.advance(0.5)
        self.assertLessEqual(self.ex.calls["create"], 6)   # 4 + at most one replace per side
        summary = s.shutdown()
        self.assertEqual(self.ex.our_orders(), [])
        self.assertEqual((summary["orders_left_resting"], summary["orders_left_verified"]),
                         (0, True))
        failed = [e for e in read_journal(os.path.join(self.dir, "events.jsonl"))
                  if e["type"] == "order" and not e["ok"]]
        self.assertEqual(failed, [])

    def test_startup_failure_still_shuts_down_cleanly(self):
        self.ex.fail_reads = 100
        summary = self.session().run(duration_s=60)
        self.assertTrue(summary["reason"].startswith("crash:"))
        self.assertEqual(self.ex.calls["create"], 0)

    def test_session_loss_kill_switch_via_run(self):
        self.ex.pos["LIQ"] = 0.0
        s = self.session(risk={"max_session_loss": 0.10})
        orig = s.run_cycle
        state = {"n": 0}

        def cycle():
            state["n"] += 1
            if state["n"] == 2:
                self.ex.taker_hits("LIQ", "bid", 2)
            if state["n"] == 3:
                self.ex.set_book("LIQ", {0.30: 50}, {0.68: 50})
            orig()
        s.run_cycle = cycle
        summary = s.run(duration_s=120)
        self.assertEqual(summary["kill_reason"], "max_session_loss")
        self.assertEqual(self.ex.our_orders(), [])

    def test_stand_down_conditions(self):
        self.ex.trading_active = False
        s = self.session(specs=[{"ticker": "LIQ", "liquidity_class": "liquid"}])
        s.start()
        s.run_cycle()
        self.assertEqual(self.ticks("LIQ")[-1]["reason"], "trading_paused")
        self.assertEqual(self.ex.our_orders(), [])

        self.ex.trading_active = True
        self.ex.mkts["LIQ"]["close_ts"] = self.clock() + 60   # 1 minute to close
        s._last_meta = 0
        self.clock.advance(31)
        s.run_cycle()
        self.assertEqual(self.ticks("LIQ")[-1]["reason"], "near_resolution")

    def test_one_sided_book_falls_back_to_last_price(self):
        self.ex.add_market("ONE", {0.40: 5}, {}, last_price=0.45)
        s = self.session(specs=[{"ticker": "ONE", "liquidity_class": "illiquid"}])
        s.start()
        s.run_cycle()
        t = self.ticks("ONE")[-1]
        self.assertEqual((t["mid_source"], t["mid"]), ("last_price", 0.45))
        self.assertEqual(t["state"], "quoting")

    def test_own_orders_netted_out_of_mid(self):
        s = self.session(specs=[{"ticker": "ILQ", "liquidity_class": "illiquid"}])
        s.start()
        s.run_cycle()
        mid0 = self.ticks("ILQ")[-1]["mid"]
        self.clock.advance(2)
        s.run_cycle()   # our quotes are now inside the 0.30/0.40 book
        self.assertEqual(self.ticks("ILQ")[-1]["mid"], mid0)
        self.assertEqual(self.ticks("ILQ")[-1]["best_bid"], 0.30)

    def test_dry_run_never_touches_exchange(self):
        dry = DryRunGateway(self.ex)
        s = self.session(gw=dry, dry_run=True)
        s.start()
        s.run_cycle()
        self.clock.advance(2)
        s.run_cycle()
        self.assertEqual(self.ex.calls["create"], 0)
        self.assertEqual(len(dry.orders), 4)
        self.assertGreaterEqual(s.orders.counts["LIQ"]["keep"], 2)
        s.request_stop("test")
        s.shutdown()
        self.assertEqual(dry.orders, {})
        self.journal.close()
        report = build_report(self.dir)
        self.assertTrue(report["session"]["dry_run"])
        self.assertTrue(any(f["area"] == "session" and f["text"].startswith("DRY RUN")
                            for f in report["findings"]))
        render_markdown(report)

    def test_carried_in_inventory_marked_at_first_mid(self):
        self.ex.pos["LIQ"] = 3.0
        s = self.session(specs=[{"ticker": "LIQ", "liquidity_class": "liquid"}])
        s.start()
        s.run_cycle()
        pnl = s.workers["LIQ"].pnl(0.49)
        self.assertAlmostEqual(pnl["total_pnl"], 0.0)   # no fake P&L from old inventory
        self.assertEqual(s.workers["LIQ"].tracker.position, 3.0)


if __name__ == "__main__":
    unittest.main()
