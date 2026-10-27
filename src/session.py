"""Live trading session: runs the Avellaneda-Stoikov strategy on several
markets at once against the exchange gateway.

The strategy math is NOT reimplemented here: each market gets an
AvellanedaMarketMaker (client=None) used purely for its quoting functions
(volatility estimator with the binary sqrt(p(1-p)) scaling, k estimation,
compute_quotes, desired_sizes), exactly as the shadow backtester does. This
module adds what live trading needs around it:

  * one loop for all markets, polling portfolio state in batch per cycle
    (1 orderbooks call for up to 100 markets, 1 orders, 1 positions,
    1 fills) so the read budget does not scale with the market count
  * own resting orders netted out of the book before the mid is computed
  * a post-only cross policy (clamp to one tick inside, or skip the side)
  * risk gating, order reconciliation via amend, fill/markout accounting
  * a journal entry for every cycle, decision, order write and fill

Single-threaded by design: deterministic, easy to test, and the exchange's
rate limits make parallel request fan-out pointless at this scale.
"""
import json
import logging
import math
import time
from typing import Callable, Dict, List, Optional

from helper import (AvellanedaMarketMaker, PerformanceTracker, TICK,
                    estimate_k_from_depth, resolution_risk_premium)
from src.execution import OrderManager
from src.orderbook import OrderBook
from src.risk import RiskManager

DEFAULT_STRATEGY = {
    "gamma": 1.0, "default_k": 40.0, "base_order_size": 2, "max_position": 10,
    "min_spread": 0.02, "premium_scale": 0.02, "min_time_to_resolution_h": 0.05,
    "min_quote_mid": 0.05, "max_quote_mid": 0.95, "max_horizon_h": 24.0,
    "gamma_mode": "constant", "calibration_constant": 0.04, "sigma_floor": 0.01,
    "vol_window": 120, "fair_value_adjustment": 0.0, "k_refresh_every": 10,
}

DEFAULT_EXECUTION = {
    "order_expiration_s": 300, "expiry_refresh_frac": 0.33, "replenish_threshold": 0.5,
    "cross_policy": "clamp",            # clamp | skip
    "aggressive_reprice": "replace",    # replace (cancel + post-only create) | amend
    "cancel_order_on_pause": True, "post_only": True,
    "flatten_on_exit": False,
    "listing_grace_s": 5.0,             # overlay our writes on the lagging order listing
}

DEFAULT_SESSION = {
    "cycle_s": 2.0, "meta_refresh_s": 30.0, "balance_refresh_s": 60.0,
    "trades_poll_s": 10.0, "api_stats_every_s": 60.0, "summary_every_cycles": 30,
    "markout_horizons": [10, 60, 300], "read_failures_before_pull": 3,
    "listing_settle_s": 2.0,            # wait before verifying cancels at shutdown
}


class MarketWorker:
    """Per-market strategy state: estimators, P&L tracker, last decision."""

    def __init__(self, spec: dict, strategy: dict, journal, logger: logging.Logger,
                 markout_horizons):
        self.ticker = spec["ticker"]
        self.liquidity_class = spec.get("liquidity_class", "unclassified")
        self.spec = spec
        self.journal = journal
        self.logger = logger
        params = {k: strategy[k] for k in DEFAULT_STRATEGY if k in strategy}
        self.strategy = params
        self.mm = AvellanedaMarketMaker(logger=logger, client=None,
                                        market_ticker=self.ticker, perf_log_path="",
                                        **params)
        self.tracker = PerformanceTracker(logger, log_path=None,
                                          markout_horizons=tuple(markout_horizons),
                                          event_sink=self._tracker_event)
        self.meta: dict = {}
        self.position = 0.0             # exchange-reported, authoritative for quoting
        self.fees = 0.0
        self.fill_count = 0
        self.last_mid: Optional[float] = None
        self.last_decision: Optional[dict] = None
        self.quoting_cycles = 0
        self.mismatch_cycles = 0
        self._mark_pending = False      # carried-in inventory awaiting a first mark
        self._iteration = 0
        self.last_trade_poll = 0.0
        self.last_trade_ts = 0

    def _tracker_event(self, event: dict):
        if event.get("event") == "markout":
            self.journal.write("markout", ticker=self.ticker, horizon=event["horizon"],
                               markout=event["markout"], fill_price=event["fill_price"],
                               ts=event["ts"])

    def init_position(self, q: float):
        """Inventory carried in from before the session is marked at the first
        observed mid, so session P&L measures only what happens from now on."""
        self.position = q
        self.tracker.position = q
        self._mark_pending = q != 0

    def time_to_close_h(self, now: float) -> float:
        close = self.meta.get("close_ts")
        if not close:
            return 24.0
        return max((close - now) / 3600.0, 0.0)

    # ------------------------------------------------------------------

    def decide(self, raw_book: Optional[dict], own_orders: List[dict], now: float,
               cross_policy: str = "clamp", block: Optional[str] = None) -> dict:
        """Run the strategy for this cycle. Returns a decision dict holding
        the desired quotes plus every intermediate the diagnostics need."""
        mm = self.mm
        d: dict = {"ticker": self.ticker, "q": self.position, "state": "standing_down",
                   "reason": None, "bid": None, "ask": None, "bid_size": 0, "ask_size": 0}
        tth = self.time_to_close_h(now)
        d["tth_h"] = round(tth, 4)

        book = None
        if raw_book is not None:
            book = OrderBook(self.ticker)
            book.apply_snapshot(raw_book, ts_ms=int(now * 1000))
            for o in own_orders:
                if o.get("virtual"):
                    continue  # dry-run orders are not in the real book
                if o["side"] == "bid":
                    book.remove_liquidity("yes", o["price"], o["remaining"])
                else:  # our ask rests as a NO bid at 1 - price
                    book.remove_liquidity("no", round(1.0 - o["price"], 4), o["remaining"])
            d.update(best_bid=book.best_yes_bid, best_ask=book.best_yes_ask,
                     bid_depth_top=book.yes.get(book.best_yes_bid) if book.yes else None,
                     ask_depth_top=book.no.get(book.best_no_bid) if book.no else None,
                     book_levels=len(book.yes) + len(book.no))
            if book.best_yes_bid is not None and book.best_yes_ask is not None:
                d["market_spread"] = round(book.best_yes_ask - book.best_yes_bid, 4)

        mid, mid_source = (book.mid, "book") if book is not None else (None, None)
        if mid is None:
            lp = self.meta.get("last_price")
            if lp is not None and 0.0 < lp < 1.0:
                mid, mid_source = lp, "last_price"
        d["mid"], d["mid_source"] = mid, mid_source

        if block:  # risk halt, kill switch or exchange pause
            d["reason"] = block
        elif self.meta and not self.meta.get("tradable", True):
            d["reason"] = f"market_status:{self.meta.get('status')}"
        elif tth <= mm.min_time_to_resolution_h:
            d["reason"] = "near_resolution"
        elif book is None:
            d["reason"] = "no_book"
        elif mid is None:
            d["reason"] = "no_mid"
        elif not (mm.min_quote_mid <= mid <= mm.max_quote_mid):
            d["reason"] = "mid_out_of_band"
        if d["reason"]:
            self.last_decision = d
            if mid is not None:
                self.last_mid = mid
            return d

        if self._mark_pending:
            self.tracker.avg_entry = mid
            self._mark_pending = False
            self.journal.write("inventory_carried_in", ticker=self.ticker,
                               position=self.position, mark=mid)

        mm.vol.add(now, mid)
        self._iteration += 1
        if self._iteration % mm.k_refresh_every == 1:
            mm.k = estimate_k_from_depth(book.depth_profile(), mm.default_k)

        q = self.position
        sigma = mm.vol.sigma(mid)
        quotes = mm.compute_quotes(mid, q, tth, sigma)
        bid_size, ask_size = mm.desired_sizes(q)

        # spread decomposition: which A-S term actually sets the width?
        gamma, k = quotes["gamma"], quotes["k"]
        horizon = min(tth, mm.max_horizon_h)
        binary_scale = math.sqrt(max(mid * (1.0 - mid), 1e-6)) / 0.5
        inv_term = gamma * sigma ** 2 * horizon
        liq_term = (2.0 / gamma) * math.log(1.0 + gamma / k)
        prem_term = resolution_risk_premium(mid, tth, mm.premium_scale)
        d.update(
            state="quoting", sigma=round(sigma, 6), sigma_raw=round(sigma / binary_scale, 6),
            binary_scale=round(binary_scale, 4), sigma_from_data=len(mm.vol.observations) >= mm.vol.min_obs,
            k=round(k, 3), gamma=round(gamma, 4), reservation=round(quotes["reservation"], 5),
            spread=round(quotes["spread"], 5), spread_inv=round(inv_term, 6),
            spread_liq=round(liq_term, 6), spread_prem=round(prem_term, 6),
            min_spread_binding=(inv_term + liq_term + prem_term) < mm.min_spread,
            model_bid=quotes["bid"], model_ask=quotes["ask"])

        bid, ask = quotes["bid"], quotes["ask"]
        best_bid, best_ask = book.best_yes_bid, book.best_yes_ask
        if best_ask is not None and bid >= best_ask - 1e-9:
            d["bid_crossed"] = True
            bid = round(best_ask - TICK, 4) if cross_policy == "clamp" else None
        if best_bid is not None and ask <= best_bid + 1e-9:
            d["ask_crossed"] = True
            ask = round(best_bid + TICK, 4) if cross_policy == "clamp" else None
        if bid is not None and bid < TICK - 1e-9:
            bid = None
        if ask is not None and ask > 1.0 - TICK + 1e-9:
            ask = None

        d.update(bid=bid, ask=ask, bid_size=bid_size if bid is not None else 0,
                 ask_size=ask_size if ask is not None else 0)
        self.quoting_cycles += 1
        self.last_mid = mid
        self.last_decision = d
        return d

    def on_fill(self, fill: dict):
        tracker_fill = {"book_side": fill["side"], "count_fp": fill["qty"],
                        "yes_price_dollars": fill["price"], "is_taker": fill["is_taker"],
                        "created_time": None}
        before = self.tracker.position
        self.tracker.on_fill(tracker_fill, self.last_mid)
        # tracker stamps markout due-times from created_time; use exchange ts
        for m in self.tracker.pending_markouts[-len(self.tracker.markout_horizons):]:
            m["due_ts"] = fill["ts"] + m["horizon"]
        self.fees += fill["fee"]
        self.fill_count += 1
        d = self.last_decision or {}
        self.journal.write(
            "fill", ticker=self.ticker, fill_id=fill["fill_id"], order_id=fill["order_id"],
            side=fill["side"], qty=fill["qty"], price=fill["price"],
            is_taker=fill["is_taker"], fee=fill["fee"], fill_ts=fill["ts"],
            mid_before=self.last_mid, our_bid=d.get("bid"), our_ask=d.get("ask"),
            reservation=d.get("reservation"), position_before=before,
            position_after=self.tracker.position, liquidity_class=self.liquidity_class)
        self.logger.info(f"{self.ticker} FILL {fill['side']} {fill['qty']:g} @ "
                         f"{fill['price']:.2f} (taker={fill['is_taker']}, fee "
                         f"{fill['fee']:.4f}) -> tracked q {self.tracker.position:g}")

    def pnl(self, mark: Optional[float]) -> dict:
        s = self.tracker.summary(mark)
        s["fees"] = round(self.fees, 4)
        s["net_pnl"] = round(s["total_pnl"] - self.fees, 4)
        return s


class TradingSession:
    def __init__(self, gateway, specs: List[dict], config: dict, journal,
                 logger: logging.Logger, dry_run: bool = False,
                 clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep):
        self.gw = gateway
        self.journal = journal
        self.logger = logger
        self.dry_run = dry_run
        self.clock = clock
        self.sleep = sleep
        self.cfg = {**DEFAULT_SESSION, **(config.get("session") or {})}
        self.exec_cfg = {**DEFAULT_EXECUTION, **(config.get("execution") or {})}
        base_strategy = {**DEFAULT_STRATEGY, **(config.get("strategy") or {})}
        class_overrides = config.get("strategy_overrides") or {}
        self.risk = RiskManager(config.get("risk"), journal, logger)
        self.orders = OrderManager(
            gateway, journal, logger,
            order_expiration_s=self.exec_cfg["order_expiration_s"],
            expiry_refresh_frac=self.exec_cfg["expiry_refresh_frac"],
            replenish_threshold=self.exec_cfg["replenish_threshold"],
            cancel_order_on_pause=self.exec_cfg["cancel_order_on_pause"],
            post_only=self.exec_cfg["post_only"],
            aggressive_reprice=self.exec_cfg["aggressive_reprice"],
            listing_grace_s=self.exec_cfg["listing_grace_s"],
            on_reject=lambda t: self.risk.record_reject(t, self.clock()), clock=clock)
        self.workers: Dict[str, MarketWorker] = {}
        for spec in specs:
            strategy = {**base_strategy, **class_overrides.get(
                spec.get("liquidity_class"), {}), **(spec.get("strategy") or {})}
            self.workers[spec["ticker"]] = MarketWorker(
                spec, strategy, journal, logger, self.cfg["markout_horizons"])
        self.config = config
        self.tickers = list(self.workers)
        self.cycle_no = 0
        self.stop_reason: Optional[str] = None
        self.start_ts = 0.0
        self.balance: Optional[float] = None
        self.start_balance: Optional[dict] = None
        self.exchange_ok = True
        self._seen_fills: set = set()
        self._seen_trades: set = set()
        self._fill_cursor = 0
        self._last_meta = self._last_balance = self._last_stats = 0.0
        self._read_failures = 0

    def request_stop(self, reason: str = "requested"):
        if self.stop_reason is None:
            self.stop_reason = reason

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def start(self):
        now = self.clock()
        self.start_ts = now
        self._fill_cursor = int(now) - 1
        status = self._safe(self.gw.exchange_status, {}) or {}
        self.exchange_ok = bool(status.get("trading_active", True))
        self.start_balance = self._safe(self.gw.balance, None)
        self.balance = (self.start_balance or {}).get("balance")
        self.journal.write(
            "session_start", tickers=self.tickers, dry_run=self.dry_run,
            exchange_status=status, balance=self.start_balance,
            config=self.config,
            strategy_effective={t: w.strategy for t, w in self.workers.items()},
            execution_effective=self.exec_cfg, risk_effective=self.risk.cfg,
            session_effective=self.cfg)
        metas = self.gw.markets(self.tickers)
        positions = self.gw.positions(self.tickers)
        resting = self.gw.resting_orders(self.tickers)
        for t, w in self.workers.items():
            w.meta = metas.get(t, {})
            w.init_position(positions.get(t, {}).get("position", 0.0))
            self.journal.write("market_meta", ticker=t, liquidity_class=w.liquidity_class,
                               meta=w.meta, selection=w.spec.get("selection"),
                               start_position=w.position,
                               start_exchange_position=positions.get(t),
                               preexisting_orders=len(resting.get(t, [])))
            self.logger.info(f"{t} [{w.liquidity_class}] status={w.meta.get('status')} "
                             f"close_in={w.time_to_close_h(now):.1f}h q0={w.position:g} "
                             f"preexisting_orders={len(resting.get(t, []))}")
        self._last_meta = self._last_balance = self._last_stats = now

    def run(self, duration_s: Optional[float]) -> dict:
        try:
            self.start()
            deadline = self.start_ts + duration_s if duration_s else None
            while self.stop_reason is None:
                if deadline and self.clock() >= deadline:
                    self.request_stop("duration_reached")
                    break
                if self.risk.kill_reason:
                    self.request_stop(f"kill:{self.risk.kill_reason}")
                    break
                t0 = self.clock()
                self.run_cycle()
                elapsed = self.clock() - t0
                self.sleep(max(0.0, self.cfg["cycle_s"] - elapsed))
        except KeyboardInterrupt:
            self.request_stop("keyboard_interrupt")
        except Exception as e:
            self.logger.exception("session crashed")
            self.journal.write("cycle_error", fatal=True, error=repr(e))
            self.request_stop(f"crash:{type(e).__name__}")
        finally:
            summary = self.shutdown()
        return summary

    # ------------------------------------------------------------------
    # one cycle
    # ------------------------------------------------------------------

    def run_cycle(self):
        now = self.clock()
        self.cycle_no += 1
        t_start = time.perf_counter()
        self._periodic_refresh(now)
        try:
            books = self.gw.orderbooks(self.tickers)
            resting = self.gw.resting_orders(self.tickers)
            positions = self.gw.positions(self.tickers)
            fills = self.gw.fills_since(self._fill_cursor)
        except Exception as e:
            self._read_failures += 1
            self.risk.record_error(repr(e))
            self.journal.write("cycle_error", cycle=self.cycle_no, phase="read",
                               error=repr(e)[:300])
            self.logger.warning(f"cycle {self.cycle_no}: read phase failed: {e}")
            if self._read_failures == self.cfg["read_failures_before_pull"]:
                self.logger.warning("pulling known orders: cannot see the book")
                for t in self.tickers:
                    self.orders.cancel_all(t, self.orders.known_orders(t), "blind")
            return
        self._read_failures = 0
        self.risk.record_ok()

        self._process_fills(fills)
        self._poll_trades(now)

        gross = sum(abs(w.position) for w in self.workers.values())
        headroom = self.risk.cfg["max_gross_position"] - gross
        total_net = 0.0
        quoting = 0
        for t, w in self.workers.items():
            exch = positions.get(t, {})
            w.position = exch.get("position", 0.0)
            self._check_position(w, exch)
            if t in self.risk.halted:
                block = f"halted:{self.risk.halted[t]}"
            elif self.risk.kill_reason:
                block = f"kill:{self.risk.kill_reason}"
            elif not self.exchange_ok:
                block = "trading_paused"
            else:
                block = None
            own = self.orders.view(t, resting.get(t, []))
            d = w.decide(books.get(t), own, now,
                         cross_policy=self.exec_cfg["cross_policy"], block=block)
            if d["state"] == "quoting":
                bid_size, ask_size, gate = self.risk.gate_sizes(
                    w.position, d["bid_size"], d["ask_size"], headroom)
                if gate:
                    d["risk_gate"] = gate
                # worst-case gross increase if our quotes fill
                if w.position > 0:
                    headroom -= bid_size
                elif w.position < 0:
                    headroom -= ask_size
                else:
                    headroom -= max(bid_size, ask_size)
                d["bid_size"], d["ask_size"] = bid_size, ask_size
                quoting += 1
            desired = {"bid": (d["bid"], d["bid_size"]) if d["bid"] is not None
                       and d["bid_size"] > 0 else None,
                       "ask": (d["ask"], d["ask_size"]) if d["ask"] is not None
                       and d["ask_size"] > 0 else None}
            self.orders.reconcile(t, desired, own)

            mark = d.get("mid") if d.get("mid") is not None else w.last_mid
            w.tracker.check_markouts(now, mark)
            pnl = w.pnl(mark)
            total_net += pnl["net_pnl"]
            self.risk.check_market(t, pnl["net_pnl"])
            d.update(net_pnl=pnl["net_pnl"], tracked_q=w.tracker.position,
                     resting=[(o["side"], o["price"], o["remaining"]) for o in own])
            self.journal.write("tick", cycle=self.cycle_no, ts=now, **d)

        self.risk.check_session(total_net, self.balance)
        cycle_ms = (time.perf_counter() - t_start) * 1000
        self.journal.write("cycle", cycle=self.cycle_no, ms=round(cycle_ms, 1),
                           quoting_markets=quoting, gross_position=gross,
                           net_pnl=round(total_net, 4), fills=len(fills))
        if self.cycle_no % self.cfg["summary_every_cycles"] == 0:
            self.logger.info(f"cycle {self.cycle_no} ({cycle_ms:.0f}ms) net_pnl="
                             f"{total_net:+.4f} gross={gross:g} quoting={quoting}/"
                             f"{len(self.workers)}")
            for t, w in self.workers.items():
                self.logger.info(f"  PERF {t}: {json.dumps(w.pnl(w.last_mid))}")

    def _periodic_refresh(self, now: float):
        if now - self._last_meta >= self.cfg["meta_refresh_s"]:
            self._last_meta = now
            status = self._safe(self.gw.exchange_status, None)
            if status is not None:
                ok = bool(status.get("trading_active", True))
                if ok != self.exchange_ok:
                    self.journal.write("risk", scope="exchange",
                                       reason="trading_active" if ok else "trading_paused",
                                       status=status)
                self.exchange_ok = ok
            metas = self._safe(lambda: self.gw.markets(self.tickers), None)
            for t, m in (metas or {}).items():
                if t in self.workers:
                    if m.get("status") != self.workers[t].meta.get("status"):
                        self.journal.write("market_status", ticker=t, status=m.get("status"))
                    self.workers[t].meta = m
        if now - self._last_balance >= self.cfg["balance_refresh_s"]:
            self._last_balance = now
            b = self._safe(self.gw.balance, None)
            if b is not None:
                self.balance = b.get("balance")
        if now - self._last_stats >= self.cfg["api_stats_every_s"]:
            self._last_stats = now
            self.journal.write("api_stats", stats=self.gw.api_stats())

    def _process_fills(self, fills: List[dict]):
        newest = self._fill_cursor
        for f in sorted(fills, key=lambda f: f["ts"]):
            if f["fill_id"] in self._seen_fills:
                continue
            self._seen_fills.add(f["fill_id"])
            newest = max(newest, int(f["ts"]))
            self.orders.on_fill(f)
            w = self.workers.get(f["ticker"])
            if w is not None:
                w.on_fill(f)
        # re-query the last couple of seconds each cycle (dedupe above), so a
        # fill landing in the same second as the previous poll is not missed
        self._fill_cursor = max(self._fill_cursor, newest - 2)

    def _poll_trades(self, now: float):
        """Public tape for our markets: lets the report compare our fills to
        total market flow (market share, prints through our quotes)."""
        for t, w in self.workers.items():
            if now - w.last_trade_poll < self.cfg["trades_poll_s"]:
                continue
            w.last_trade_poll = now
            since = w.last_trade_ts or int(self.start_ts)
            trades = self._safe(lambda: self.gw.trades_since(t, since), [])
            for tr in sorted(trades or [], key=lambda x: x["ts"]):
                if tr["ts"] < self.start_ts:
                    continue
                key = tr.get("trade_id") or f"{t}:{tr['ts']}:{tr['price']}:{tr['qty']}"
                if key in self._seen_trades:
                    continue
                self._seen_trades.add(key)
                self.journal.write("trade", ticker=t, trade_id=key, price=tr["price"],
                                   qty=tr["qty"], taker_side=tr["taker_side"],
                                   trade_ts=tr["ts"])
                w.last_trade_ts = max(w.last_trade_ts, int(tr["ts"]))

    def _check_position(self, w: MarketWorker, exch: dict):
        tracked = w.tracker.position
        actual = exch.get("position", 0.0)
        if abs(tracked - actual) > 1e-6:
            w.mismatch_cycles += 1
            if w.mismatch_cycles == 3:  # fills can lag positions by a poll or two
                self.journal.write("position_mismatch", ticker=w.ticker,
                                   tracked=tracked, exchange=actual)
                self.logger.warning(f"{w.ticker}: tracked position {tracked:g} != "
                                    f"exchange {actual:g}")
        else:
            w.mismatch_cycles = 0

    # ------------------------------------------------------------------
    # shutdown
    # ------------------------------------------------------------------

    def shutdown(self) -> dict:
        reason = self.stop_reason or "unknown"
        self.logger.info(f"shutting down ({reason}): cancelling resting orders")
        remaining_orders, verified = self._cancel_everything()

        if self.exec_cfg.get("flatten_on_exit") and not self.dry_run:
            self._flatten()

        books = self._safe(lambda: self.gw.orderbooks(self.tickers), {}) or {}
        positions = self._safe(lambda: self.gw.positions(self.tickers), {}) or {}
        fills = self._safe(lambda: self.gw.fills_since(self._fill_cursor), []) or []
        self._process_fills(fills)
        markets = {}
        total = {"realized_pnl": 0.0, "unrealized_pnl": 0.0, "fees": 0.0, "net_pnl": 0.0,
                 "fills": 0}
        for t, w in self.workers.items():
            mark = w.last_mid
            if t in books:
                book = OrderBook(t)
                book.apply_snapshot(books[t])
                mark = book.mid if book.mid is not None else mark
            pnl = w.pnl(mark)
            pnl.update(liquidity_class=w.liquidity_class, final_mark=mark,
                       exchange_position=positions.get(t, {}).get("position"),
                       exchange_realized_pnl=positions.get(t, {}).get("realized_pnl"),
                       exchange_fees_paid=positions.get(t, {}).get("fees_paid"),
                       quoting_cycles=w.quoting_cycles,
                       order_counts=dict(self.orders.counts.get(t, {})),
                       halted=self.risk.halted.get(t))
            markets[t] = pnl
            for k in total:
                total[k] += pnl[k] if k != "fills" else pnl["fills"]
        end_balance = self._safe(self.gw.balance, None)
        summary = {
            "reason": reason, "dry_run": self.dry_run, "cycles": self.cycle_no,
            "duration_s": round(self.clock() - self.start_ts, 1),
            "orders_left_resting": remaining_orders,
            "orders_left_verified": verified, "markets": markets,
            "total": {k: round(v, 4) for k, v in total.items()},
            "start_balance": self.start_balance, "end_balance": end_balance,
            "kill_reason": self.risk.kill_reason, "halted": dict(self.risk.halted),
            "api_stats": self.gw.api_stats(),
        }
        self.journal.write("session_end", **summary)
        if remaining_orders or not verified:
            self.logger.error(f"{remaining_orders} orders may still be resting "
                              f"({'verified' if verified else 'listing unavailable'}); "
                              f"check the account (they expire after "
                              f"{self.exec_cfg['order_expiration_s']}s)")
        self.logger.info(f"FINAL {json.dumps(summary['total'])} reason={reason}")
        return summary

    def _cancel_everything(self):
        """Cancel every order we may have resting: the listing plus what we
        placed that it may not show yet. Then wait for the listing to catch
        up with the cancels and check the raw listing (not our view of it).
        Returns (orders still listed, whether that count was verified)."""
        listed = self._safe(lambda: self.gw.resting_orders(self.tickers), None) or {}
        targets = {}
        for t in self.tickers:
            orders = self.orders.view(t, listed.get(t, []))
            ids = {o["order_id"] for o in orders}
            targets[t] = orders + [o for o in self.orders.known_orders(t)
                                   if o["order_id"] not in ids]
        remaining, verified = 0, False
        for attempt in range(3):
            for t, orders in targets.items():
                self.orders.cancel_all(t, orders, "shutdown")
            self.sleep(self.cfg["listing_settle_s"])
            listed = self._safe(lambda: self.gw.resting_orders(self.tickers), None)
            verified = listed is not None
            if listed is None:  # cannot read: retry what we know we placed
                targets = {t: self.orders.known_orders(t) for t in self.tickers}
            else:
                targets = {t: o for t, o in listed.items() if o}
            remaining = sum(len(o) for o in targets.values())
            self.journal.write("shutdown_check", attempt=attempt, verified=verified,
                               remaining=remaining)
            if verified and not remaining:
                break
        return remaining, verified

    def _flatten(self):
        books = self._safe(lambda: self.gw.orderbooks(self.tickers), {}) or {}
        for t, w in self.workers.items():
            q = w.position
            if abs(q) < 1e-9 or t not in books:
                continue
            book = OrderBook(t)
            book.apply_snapshot(books[t])
            side = "ask" if q > 0 else "bid"
            price = book.best_yes_bid if q > 0 else book.best_yes_ask
            if price is None:
                continue
            t0 = time.perf_counter()
            try:
                self.gw.create_order(ticker=t, side=side, count=abs(q), price=price,
                                     post_only=False, time_in_force="immediate_or_cancel",
                                     reduce_only=True, cancel_order_on_pause=False)
                ok, err = True, None
            except Exception as e:
                ok, err = False, str(e)[:300]
            self.journal.write("order", action="flatten", ticker=t, side=side, price=price,
                               size=abs(q), ok=ok, error=err,
                               latency_ms=round((time.perf_counter() - t0) * 1000, 1))

    def _safe(self, fn, default):
        try:
            return fn()
        except Exception as e:
            self.journal.write("cycle_error", cycle=self.cycle_no, phase="aux",
                               error=repr(e)[:300])
            self.logger.warning(f"auxiliary call failed: {e}")
            return default
