"""Tests for the shadow fill simulator — the piece whose correctness decides
whether backtest results mean anything. The model must be pessimistic: when in
doubt, no fill."""
import logging
import unittest

from src.orderbook import OrderBook
from src.shadow import FillSimulator, ShadowEngine

logger = logging.getLogger("test")
logger.addHandler(logging.NullHandler())


def book_with(yes_levels=None, no_levels=None) -> OrderBook:
    b = OrderBook("T")
    b.apply_snapshot({"yes_dollars": yes_levels or [],
                      "no_dollars": no_levels or []})
    return b


def trade(yes_price, count, taker_side=None):
    return {"yes_price": yes_price, "count": count, "taker_side": taker_side}


class TestFillSimulator(unittest.TestCase):
    def test_through_print_fills_bid_fully(self):
        sim = FillSimulator("queue")
        sim.place("bid", 0.40, 5, book_with(), ts_ms=0)
        fills = sim.on_trade(trade(0.38, 2, "no"))  # printed below our bid
        self.assertEqual(fills, [("bid", 0.40, 5)])
        self.assertIsNone(sim.orders["bid"])

    def test_at_price_respects_queue_ahead(self):
        sim = FillSimulator("queue")
        # 10 contracts already displayed at 0.40 -> they are ahead of us
        sim.place("bid", 0.40, 5, book_with(yes_levels=[["0.40", "10"]]), ts_ms=0)
        self.assertEqual(sim.on_trade(trade(0.40, 6, "no")), [])   # queue eats it
        self.assertEqual(sim.orders["bid"].queue_ahead, 4)
        fills = sim.on_trade(trade(0.40, 7, "no"))                 # 4 queue + 3 us
        self.assertEqual(fills, [("bid", 0.40, 3)])
        self.assertEqual(sim.orders["bid"].remaining, 2)

    def test_taker_side_gates_fills(self):
        sim = FillSimulator("queue")
        sim.place("bid", 0.40, 5, book_with(), ts_ms=0)
        # taker BOUGHT yes = buy pressure = hits asks, never our bid
        self.assertEqual(sim.on_trade(trade(0.38, 5, "yes")), [])
        # unknown taker side: allowed (conservative in P&L terms? no - but
        # symmetric; queue/strict pessimism is the guard)
        self.assertTrue(sim.on_trade(trade(0.38, 5, None)))

    def test_ask_side_symmetric(self):
        sim = FillSimulator("queue")
        # our ask at 0.60 rests as NO bid at 0.40; 8 contracts displayed there
        sim.place("ask", 0.60, 5, book_with(no_levels=[["0.40", "8"]]), ts_ms=0)
        self.assertEqual(sim.orders["ask"].queue_ahead, 8)
        fills = sim.on_trade(trade(0.63, 4, "yes"))  # printed above our ask
        self.assertEqual(fills, [("ask", 0.60, 5)])
        # price below our ask never touches us
        sim.place("ask", 0.60, 5, book_with(), ts_ms=0)
        self.assertEqual(sim.on_trade(trade(0.55, 4, "yes")), [])

    def test_strict_mode_disables_at_price_fills(self):
        sim = FillSimulator("strict")
        sim.place("bid", 0.40, 5, book_with(), ts_ms=0)
        self.assertEqual(sim.on_trade(trade(0.40, 100, "no")), [])
        self.assertEqual(sim.on_trade(trade(0.39, 1, "no")),
                         [("bid", 0.40, 5)])

    def test_snapshot_shrinks_queue_ahead(self):
        sim = FillSimulator("queue")
        sim.place("bid", 0.40, 5, book_with(yes_levels=[["0.40", "10"]]), ts_ms=0)
        # level shrank to 3: at most 3 contracts can still be ahead of us
        sim.on_snapshot(book_with(yes_levels=[["0.40", "3"]]))
        self.assertEqual(sim.orders["bid"].queue_ahead, 3)
        # level grew again (new orders BEHIND us): queue ahead must not grow
        sim.on_snapshot(book_with(yes_levels=[["0.40", "50"]]))
        self.assertEqual(sim.orders["bid"].queue_ahead, 3)


class TestShadowEngine(unittest.TestCase):
    def make_engine(self, **overrides):
        params = dict(gamma=1.0, default_k=40.0, base_order_size=5,
                      max_position=20, min_spread=0.02)
        params.update(overrides)
        return ShadowEngine("T", "2030-01-01T00:00:00Z", "quadratic", 1.0,
                            params, logger, fill_mode="queue")

    def test_quotes_placed_and_filled_end_to_end(self):
        eng = self.make_engine()
        eng.on_event("snapshot", 1_000, ([["0.48", "10"]], [["0.48", "10"]]))  # mid 0.50
        self.assertIsNotNone(eng.sim.orders["bid"])
        self.assertIsNotNone(eng.sim.orders["ask"])
        bid_price = eng.sim.orders["bid"].price
        # sweep prints through our bid
        eng.on_event("trade", 2_000, {"yes_price": round(bid_price - 0.02, 2),
                                      "count": 3, "taker_side": "no"})
        self.assertGreater(eng.position, 0)
        self.assertEqual(eng.tracker.position, eng.position)

    def test_no_maker_fee_on_quadratic_series(self):
        eng = self.make_engine()
        eng._apply_fill("bid", 0.40, 5, ts_ms=1_000)
        self.assertEqual(eng.fees_paid, 0.0)

    def test_maker_fee_charged_when_series_has_them(self):
        eng = ShadowEngine("T", None, "quadratic_with_maker_fees", 1.0,
                           dict(gamma=1.0), logger)
        eng._apply_fill("bid", 0.50, 4, ts_ms=1_000)
        # 0.25 * 0.07 * 0.5 * 0.5 * 4 = 0.0175
        self.assertAlmostEqual(eng.fees_paid, 0.0175)

    def test_settlement_flattens_position(self):
        eng = self.make_engine()
        eng._apply_fill("bid", 0.40, 5, ts_ms=1_000)
        eng.last_ts_ms = 2_000
        report = eng.finalize(settle_price=1.0)   # resolved YES
        self.assertTrue(report["settled"])
        self.assertEqual(report["position"], 0)
        self.assertAlmostEqual(report["realized_pnl"], 3.0)  # 5 * (1.0 - 0.40)

    def test_stands_down_outside_quotable_mid_range(self):
        eng = self.make_engine()
        # penny market: mid 0.015 — one tick is the whole spread, don't quote
        eng.on_event("snapshot", 1_000, ([["0.01", "50"]], [["0.98", "50"]]))
        self.assertIsNone(eng.sim.orders["bid"])
        self.assertIsNone(eng.sim.orders["ask"])

    def test_post_only_never_crosses(self):
        eng = self.make_engine(min_spread=0.0)
        # absurdly tight book: any sane quote would cross
        eng.on_event("snapshot", 1_000, ([["0.50", "10"]], [["0.49", "10"]]))
        for side in ("bid", "ask"):
            order = eng.sim.orders[side]
            if order is not None:
                if side == "bid":
                    self.assertLess(order.price, eng.book.best_yes_ask)
                else:
                    self.assertGreater(order.price, eng.book.best_yes_bid)


if __name__ == "__main__":
    unittest.main()
