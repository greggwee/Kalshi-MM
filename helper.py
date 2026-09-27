"""Avellaneda-Stoikov market maker for Kalshi binary markets.

Model (A-S 2008, adapted for binaries):
    reservation price  r = (s + fair_value_adjustment) - q * gamma * sigma^2 * (T - t)
    optimal spread     delta = gamma * sigma^2 * (T - t)
                             + (2 / gamma) * ln(1 + gamma / k)
                             + resolution_risk_premium(p, T - t)
    bid = r - delta / 2,  ask = r + delta / 2

Units: prices in dollars [0, 1]; T - t in hours; sigma in dollars per sqrt(hour);
k in 1/dollars (fill intensity decay per dollar of distance from mid — note this
means sensible k values are ~20-60, not the paper's 1.5 which is in stock-price
units).

Orders go through the V2 API: side="bid"/"ask" on the yes-price axis. An ask
needs no inventory (it rests as a NO bid at 1 - price).
"""
import json
import logging
import math
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from src.clients import KalshiHttpClient
from src.orderbook import OrderBook

TICK = 0.01


def floor_to_tick(price: float, tick: float = TICK) -> float:
    return math.floor(price / tick + 1e-9) * tick


def ceil_to_tick(price: float, tick: float = TICK) -> float:
    return math.ceil(price / tick - 1e-9) * tick


def resolution_risk_premium(p: float, t_minus_t_hours: float, premium_scale: float) -> float:
    """Extra spread for binary contracts near resolution. A-S's gamma*sigma^2*(T-t)
    term vanishes as T-t -> 0, but binary risk CONCENTRATES at resolution (the
    price is about to jump to 0 or 1). Heuristic, not from a paper: proportional
    to Bernoulli variance p(1-p), inverse-sqrt in time remaining."""
    bernoulli_var = p * (1.0 - p)
    return premium_scale * bernoulli_var / math.sqrt(max(t_minus_t_hours, 1e-3))


def estimate_k_from_depth(depth_profile: List[Tuple[float, float]],
                          default_k: float = 40.0) -> float:
    """Fit cumulative book depth ~ exp(k * delta) by least squares on
    ln(depth) = a + k * delta. depth_profile is [(distance_from_mid,
    cumulative_contracts), ...] sorted by distance (see OrderBook.depth_profile).
    Returns k in 1/dollars, clamped to a sane range."""
    points = [(d, c) for d, c in depth_profile if d > 0 and c > 0]
    if len(points) < 3:
        return default_k
    xs = [d for d, _ in points]
    ys = [math.log(c) for _, c in points]
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    var_x = sum((x - mean_x) ** 2 for x in xs)
    if var_x <= 0:
        return default_k
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / var_x
    if not math.isfinite(slope) or slope <= 0:
        return default_k
    return min(max(slope, 5.0), 200.0)


class VolatilityEstimator:
    """Sigma for a binary contract: realized vol of the mid, scaled by the
    Bernoulli factor sqrt(p(1-p))/0.5 so quotes tighten automatically as the
    contract approaches 0 or 1. Falls back to calibration_constant * binary
    scaling until enough observations accumulate."""

    def __init__(self, window: int = 120, floor: float = 0.01,
                 calibration_constant: float = 0.04, min_obs: int = 10):
        self.observations: deque = deque(maxlen=window)  # (ts_seconds, mid)
        self.floor = floor
        self.calibration_constant = calibration_constant
        self.min_obs = min_obs

    def add(self, ts: float, mid: float):
        # ignore duplicate timestamps (e.g. retries within the same loop)
        if self.observations and ts <= self.observations[-1][0]:
            return
        self.observations.append((ts, mid))

    def sigma(self, p: float) -> float:
        """Dollars per sqrt(hour)."""
        binary_scale = math.sqrt(max(p * (1.0 - p), 1e-6)) / 0.5
        obs = list(self.observations)
        if len(obs) < self.min_obs:
            return self.calibration_constant * binary_scale
        diffs = [obs[i + 1][1] - obs[i][1] for i in range(len(obs) - 1)]
        n = len(diffs)
        mean = sum(diffs) / n
        var = sum((d - mean) ** 2 for d in diffs) / max(n - 1, 1)
        avg_dt_hours = (obs[-1][0] - obs[0][0]) / 3600.0 / max(len(obs) - 1, 1)
        if avg_dt_hours <= 0:
            return self.calibration_constant * binary_scale
        sigma_realized = math.sqrt(var / avg_dt_hours)
        return max(sigma_realized, self.floor) * binary_scale


class PerformanceTracker:
    """P&L and adverse-selection accounting. All prices in yes-axis dollars,
    position signed (+ long yes exposure, - long no exposure)."""

    def __init__(self, logger: logging.Logger, log_path: Optional[str] = None,
                 markout_horizons: Tuple[int, ...] = (60, 300),
                 event_sink=None):
        self.logger = logger
        self.log_path = log_path
        self.event_sink = event_sink  # optional callable(event_dict), e.g. dashboards
        self.markout_horizons = markout_horizons
        self.position = 0.0
        self.avg_entry = 0.0
        self.realized_pnl = 0.0
        self.fill_count = 0
        self.pending_markouts: List[dict] = []  # {due_ts, horizon, signed_qty, price}
        self.markout_sums: Dict[int, float] = {h: 0.0 for h in markout_horizons}
        self.markout_counts: Dict[int, int] = {h: 0 for h in markout_horizons}

    @staticmethod
    def normalize_fill(fill: dict) -> Tuple[float, float, float]:
        """Returns (signed_qty, yes_price, ts_seconds). buy-yes/sell-no are long
        (+), sell-yes/buy-no are short (-)."""
        count = float(fill.get("count_fp", fill.get("count", 0)))
        long_side = (fill.get("action"), fill.get("side")) in (("buy", "yes"), ("sell", "no"))
        signed_qty = count if long_side else -count
        if fill.get("yes_price_dollars") is not None:
            price = float(fill["yes_price_dollars"])
        elif fill.get("yes_price") is not None:
            price = float(fill["yes_price"]) / 100.0
        else:
            price = 1.0 - float(fill.get("no_price", 0)) / 100.0
        created = fill.get("created_time")
        ts = (datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
              if created else time.time())
        return signed_qty, price, ts

    def on_fill(self, fill: dict, mid_at_fill: Optional[float]):
        signed_qty, price, ts = self.normalize_fill(fill)
        if signed_qty == 0:
            return
        self.fill_count += 1

        # average-cost P&L
        if self.position * signed_qty >= 0:  # extending (or from flat)
            total = self.position + signed_qty
            if total != 0:
                self.avg_entry = ((self.avg_entry * self.position + price * signed_qty)
                                  / total)
            self.position = total
        else:  # reducing / flipping
            closed = min(abs(signed_qty), abs(self.position))
            direction = 1.0 if self.position > 0 else -1.0
            self.realized_pnl += (price - self.avg_entry) * closed * direction
            self.position += signed_qty
            if self.position * direction < 0:  # flipped through zero
                self.avg_entry = price

        for h in self.markout_horizons:
            self.pending_markouts.append(
                {"due_ts": ts + h, "horizon": h, "signed_qty": signed_qty, "price": price})

        self._log_event({"event": "fill", "ts": ts, "signed_qty": signed_qty,
                         "price": price, "mid_at_fill": mid_at_fill,
                         "is_taker": fill.get("is_taker"),
                         "position_after": self.position,
                         "avg_entry_after": self.avg_entry,
                         "realized_after": self.realized_pnl})

    def check_markouts(self, now_ts: float, current_mid: Optional[float]):
        if current_mid is None:
            return
        due = [m for m in self.pending_markouts if m["due_ts"] <= now_ts]
        self.pending_markouts = [m for m in self.pending_markouts if m["due_ts"] > now_ts]
        for m in due:
            # positive = price moved WITH us after the fill; negative = adverse
            direction = 1.0 if m["signed_qty"] > 0 else -1.0
            markout = (current_mid - m["price"]) * direction
            h = m["horizon"]
            self.markout_sums[h] += markout
            self.markout_counts[h] += 1
            self._log_event({"event": "markout", "ts": now_ts, "horizon": h,
                             "markout": markout, "fill_price": m["price"]})

    def summary(self, current_mid: Optional[float]) -> dict:
        unrealized = 0.0
        if current_mid is not None and self.position != 0:
            unrealized = (current_mid - self.avg_entry) * self.position
        out = {
            "position": self.position,
            "avg_entry": round(self.avg_entry, 4),
            "realized_pnl": round(self.realized_pnl, 4),
            "unrealized_pnl": round(unrealized, 4),
            "total_pnl": round(self.realized_pnl + unrealized, 4),
            "fills": self.fill_count,
        }
        for h in self.markout_horizons:
            n = self.markout_counts[h]
            out[f"avg_markout_{h}s"] = round(self.markout_sums[h] / n, 4) if n else None
        return out

    def _log_event(self, event: dict):
        if self.event_sink is not None:
            self.event_sink(event)
        if self.log_path:
            try:
                with open(self.log_path, "a") as f:
                    f.write(json.dumps(event) + "\n")
            except OSError as e:
                self.logger.warning(f"could not write perf log: {e}")


class AvellanedaMarketMaker:
    def __init__(
        self,
        logger: logging.Logger,
        client: KalshiHttpClient,
        market_ticker: str,
        gamma: float = 1.0,
        default_k: float = 40.0,
        base_order_size: int = 5,
        max_position: int = 20,
        order_expiration: int = 300,
        min_spread: float = 0.02,
        premium_scale: float = 0.02,
        min_time_to_resolution_h: float = 0.05,
        min_quote_mid: float = 0.05,           # stand down outside this mid range:
        max_quote_mid: float = 0.95,           # at the extremes one tick IS the spread
        max_horizon_h: float = 24.0,           # cap on effective T-t (see below)
        gamma_mode: str = "constant",          # "constant" | "quadratic"
        calibration_constant: float = 0.04,
        sigma_floor: float = 0.01,
        vol_window: int = 120,
        fair_value_adjustment: float = 0.0,    # directional signal, shifts s
        k_refresh_every: int = 10,
        summary_every: int = 30,
        max_runtime: Optional[float] = None,   # seconds; None = until resolution
        perf_log_path: Optional[str] = None,
    ):
        self.logger = logger
        self.client = client
        self.market_ticker = market_ticker
        self.base_gamma = gamma
        self.k = default_k
        self.default_k = default_k
        self.base_order_size = base_order_size
        self.max_position = max_position
        self.order_expiration = order_expiration
        self.min_spread = min_spread
        self.premium_scale = premium_scale
        self.min_time_to_resolution_h = min_time_to_resolution_h
        self.min_quote_mid = min_quote_mid
        self.max_quote_mid = max_quote_mid
        self.max_horizon_h = max_horizon_h
        self.gamma_mode = gamma_mode
        self.fair_value_adjustment = fair_value_adjustment
        self.k_refresh_every = k_refresh_every
        self.summary_every = summary_every
        self.max_runtime = max_runtime

        self.vol = VolatilityEstimator(window=vol_window, floor=sigma_floor,
                                       calibration_constant=calibration_constant)
        if perf_log_path is None:
            perf_log_path = f"{market_ticker}_perf.jsonl"
        self.perf = PerformanceTracker(logger, log_path=perf_log_path or None)
        self.close_time: Optional[datetime] = None
        self._last_fill_ts: int = int(time.time())

    # ------------------------------------------------------------------
    # market state
    # ------------------------------------------------------------------

    def setup(self):
        market = self.client.get_market(self.market_ticker)["market"]
        close = market.get("close_time") or market.get("expiration_time")
        if close:
            self.close_time = datetime.fromisoformat(close.replace("Z", "+00:00"))
        self.logger.info(f"{self.market_ticker}: close_time={self.close_time}")

        series_ticker = market.get("series_ticker") or self.market_ticker.split("-")[0]
        try:
            series = self.client.get_series(series_ticker)["series"]
            fee_type = series.get("fee_type", "")
            self.logger.info(f"series {series_ticker}: fee_type={fee_type}, "
                             f"fee_multiplier={series.get('fee_multiplier')}")
            if "maker" in fee_type:
                self.logger.warning(
                    f"series {series_ticker} CHARGES MAKER FEES - spread capture must "
                    f"clear the fee; consider a different series")
        except Exception as e:
            self.logger.warning(f"could not fetch series fee info: {e}")

    def time_to_resolution_h(self) -> float:
        if self.close_time is None:
            return 24.0  # conservative default if the market has no close time
        remaining = (self.close_time - datetime.now(timezone.utc)).total_seconds() / 3600.0
        return max(remaining, 0.0)

    def get_book_and_mid(self, own_orders: Optional[List[dict]] = None
                         ) -> Tuple[Optional[OrderBook], Optional[float]]:
        """Mid from the order book when two-sided; falls back to market
        bid/ask fields, then last price. Our own resting orders are netted out
        first so we never mark to (and chase) our own quotes. Returns
        (book, mid)."""
        book = OrderBook(self.market_ticker)
        try:
            ob = self.client.get_orderbook(self.market_ticker)
            book.apply_snapshot(ob.get("orderbook_fp") or ob.get("orderbook") or {},
                                ts_ms=int(time.time() * 1000))
        except Exception as e:
            self.logger.warning(f"orderbook fetch failed: {e}")
            return None, None
        for order in own_orders or []:
            yes_price = self._order_yes_price(order)
            remaining = float(order.get("remaining_count_fp",
                                        order.get("remaining_count", 0)))
            if self._order_book_side(order) == "bid":
                book.remove_liquidity("yes", yes_price, remaining)
            else:  # our ask rests as a NO bid at 1 - yes_price
                book.remove_liquidity("no", round(1.0 - yes_price, 4), remaining)
        if book.mid is not None:
            return book, book.mid

        try:
            market = self.client.get_market(self.market_ticker)["market"]
        except Exception as e:
            self.logger.warning(f"market fetch failed: {e}")
            return book, None
        bid = market.get("yes_bid_dollars")
        ask = market.get("yes_ask_dollars")
        if bid is not None and ask is not None:
            bid, ask = float(bid), float(ask)
        else:
            bid = float(market.get("yes_bid", 0)) / 100.0
            ask = float(market.get("yes_ask", 100)) / 100.0
        if 0.0 < bid and ask < 1.0 and bid <= ask:
            return book, round((bid + ask) / 2.0, 4)
        last = market.get("last_price_dollars")
        last = float(last) if last is not None else float(market.get("last_price", 0)) / 100.0
        if 0.0 < last < 1.0:
            return book, last
        return book, None

    def get_current_position(self) -> float:
        response = self.client.get_positions(ticker=self.market_ticker,
                                             settlement_status="unsettled")
        total = 0.0
        for pos in response.get("market_positions", []):
            if pos["ticker"] == self.market_ticker:
                total += float(pos.get("position_fp", pos.get("position", 0)))
        return total

    # ------------------------------------------------------------------
    # quoting math (pure; unit-tested)
    # ------------------------------------------------------------------

    def effective_gamma(self, q: float) -> float:
        if self.gamma_mode == "quadratic":
            ratio = abs(q) / max(self.max_position, 1)
            return self.base_gamma * (1.0 + ratio ** 2)
        return self.base_gamma

    def compute_quotes(self, mid: float, q: float, tth: float, sigma: float,
                       k: Optional[float] = None) -> dict:
        """All model math in one place. Returns raw and tick-rounded quotes."""
        k = k or self.k
        gamma = self.effective_gamma(q)
        s = mid + self.fair_value_adjustment
        # A-S's T is the forced-liquidation time. For a market that resolves in
        # a week we do NOT intend to carry inventory that long, so cap the
        # effective horizon; otherwise gamma*sigma^2*(T-t) blows the spread out
        # on long-dated markets. The resolution premium below still uses the
        # TRUE time to close, since it prices the settlement jump itself.
        horizon = min(tth, self.max_horizon_h)
        reservation = s - q * gamma * sigma ** 2 * horizon
        spread = (gamma * sigma ** 2 * horizon
                  + (2.0 / gamma) * math.log(1.0 + gamma / k)
                  + resolution_risk_premium(mid, tth, self.premium_scale))
        spread = max(spread, self.min_spread)
        raw_bid = reservation - spread / 2.0
        raw_ask = reservation + spread / 2.0
        # bid floors, ask ceils: rounding must never tighten the quote
        bid = floor_to_tick(raw_bid)
        ask = ceil_to_tick(raw_ask)
        if ask <= bid:
            bid = floor_to_tick(reservation - TICK)
            ask = ceil_to_tick(reservation + TICK)
        # hard invariant: both quotes inside (0, 1) with at least a tick between
        # them, no matter how extreme the inventory term makes the reservation
        bid = min(max(bid, TICK), 1.0 - 2.0 * TICK)
        ask = min(max(ask, bid + TICK), 1.0 - TICK)
        return {"reservation": reservation, "spread": spread, "sigma": sigma,
                "gamma": gamma, "k": k, "raw_bid": raw_bid, "raw_ask": raw_ask,
                "bid": round(bid, 4), "ask": round(ask, 4)}

    def desired_sizes(self, q: float) -> Tuple[int, int]:
        """A fill must never push |position| past max_position."""
        bid_size = int(max(0, min(self.base_order_size, self.max_position - q)))
        ask_size = int(max(0, min(self.base_order_size, self.max_position + q)))
        return bid_size, ask_size

    # ------------------------------------------------------------------
    # order management (V2 API)
    # ------------------------------------------------------------------

    @staticmethod
    def _order_book_side(order: dict) -> str:
        """Map legacy (action, side) to yes-axis book side."""
        return "bid" if (order.get("action"), order.get("side")) in (
            ("buy", "yes"), ("sell", "no")) else "ask"

    @staticmethod
    def _order_yes_price(order: dict) -> float:
        if order.get("yes_price_dollars") is not None:
            return float(order["yes_price_dollars"])
        if order.get("yes_price") is not None:
            return float(order["yes_price"]) / 100.0
        return 1.0 - float(order.get("no_price", 0)) / 100.0

    def get_resting_orders(self) -> List[Dict]:
        return self.client.get_orders(ticker=self.market_ticker,
                                      status="resting").get("orders", [])

    def manage_orders(self, bid: float, ask: float, bid_size: int, ask_size: int,
                      resting: Optional[List[dict]] = None):
        if resting is None:
            resting = self.get_resting_orders()
        by_side: Dict[str, List[dict]] = {"bid": [], "ask": []}
        for order in resting:
            by_side[self._order_book_side(order)].append(order)

        for side, price, size in (("bid", bid, bid_size), ("ask", ask, ask_size)):
            self._reconcile_side(side, price, size, by_side[side])

    def _reconcile_side(self, side: str, price: float, size: int, orders: List[dict]):
        keep = None
        for order in orders:
            current = self._order_yes_price(order)
            remaining = float(order.get("remaining_count_fp",
                                        order.get("remaining_count", 0)))
            if (keep is None and size > 0
                    and abs(current - price) < TICK / 2
                    and abs(remaining - size) < 0.5):
                keep = order
                continue
            try:
                self.client.cancel_order_v2(order["order_id"])
                self.logger.info(f"cancelled {side} {order['order_id']} @ {current:.2f}")
            except Exception as e:
                self.logger.error(f"cancel failed for {order['order_id']}: {e}")

        if keep is None and size > 0:
            try:
                resp = self.client.create_order_v2(
                    ticker=self.market_ticker,
                    side=side,
                    count=size,
                    price=price,
                    client_order_id=str(uuid.uuid4()),
                    expiration_ts=int(time.time()) + self.order_expiration,
                    post_only=True,
                )
                self.logger.info(f"placed {side} {size} @ {price:.2f} "
                                 f"(order {resp.get('order_id')})")
            except Exception as e:
                # post_only rejects instead of crossing the book; that's fine,
                # we just stand down this cycle
                self.logger.warning(f"create {side} @ {price:.2f} failed: {e}")

    def cancel_all(self):
        for order in self.get_resting_orders():
            try:
                self.client.cancel_order_v2(order["order_id"])
            except Exception as e:
                self.logger.error(f"cancel failed for {order['order_id']}: {e}")

    def poll_fills(self, current_mid: Optional[float]):
        try:
            fills = self.client.get_fills(ticker=self.market_ticker,
                                          min_ts=self._last_fill_ts).get("fills", [])
        except Exception as e:
            self.logger.warning(f"fills poll failed: {e}")
            return
        for fill in fills:
            self.perf.on_fill(fill, current_mid)
            created = fill.get("created_time")
            if created:
                ts = int(datetime.fromisoformat(
                    created.replace("Z", "+00:00")).timestamp())
                self._last_fill_ts = max(self._last_fill_ts, ts + 1)

    # ------------------------------------------------------------------
    # main loop
    # ------------------------------------------------------------------

    def run(self, dt: float):
        self.setup()
        start = time.time()
        iteration = 0
        try:
            while True:
                if self.max_runtime and time.time() - start > self.max_runtime:
                    self.logger.info("max_runtime reached")
                    break
                tth = self.time_to_resolution_h()
                if tth <= self.min_time_to_resolution_h:
                    self.logger.info(f"within {self.min_time_to_resolution_h}h of "
                                     f"resolution; pulling quotes and stopping")
                    break

                resting = self.get_resting_orders()
                book, mid = self.get_book_and_mid(own_orders=resting)
                now = time.time()
                if mid is None:
                    self.logger.info("no usable mid price; standing down this cycle")
                    time.sleep(dt)
                    continue
                if not (self.min_quote_mid <= mid <= self.max_quote_mid):
                    self.logger.info(f"mid {mid:.2f} outside quotable range "
                                     f"[{self.min_quote_mid}, {self.max_quote_mid}]; "
                                     f"standing down")
                    if resting:
                        self.cancel_all()
                    time.sleep(dt)
                    continue
                self.vol.add(now, mid)

                iteration += 1
                if book is not None and iteration % self.k_refresh_every == 1:
                    self.k = estimate_k_from_depth(book.depth_profile(), self.default_k)

                q = self.get_current_position()
                sigma = self.vol.sigma(mid)
                quotes = self.compute_quotes(mid, q, tth, sigma)
                bid_size, ask_size = self.desired_sizes(q)

                self.logger.info(
                    f"mid={mid:.3f} q={q:g} sigma={sigma:.4f} k={quotes['k']:.1f} "
                    f"gamma={quotes['gamma']:.2f} tth={tth:.2f}h | "
                    f"r={quotes['reservation']:.4f} spread={quotes['spread']:.4f} | "
                    f"bid {bid_size}@{quotes['bid']:.2f} ask {ask_size}@{quotes['ask']:.2f}")

                self.manage_orders(quotes["bid"], quotes["ask"], bid_size, ask_size,
                                   resting=resting)
                self.poll_fills(mid)
                self.perf.check_markouts(now, mid)

                if iteration % self.summary_every == 0:
                    self.logger.info(f"PERF {json.dumps(self.perf.summary(mid))}")

                time.sleep(dt)
        finally:
            self.logger.info("cancelling all resting orders")
            self.cancel_all()
            _, mid = self.get_book_and_mid()
            self.logger.info(f"FINAL PERF {json.dumps(self.perf.summary(mid))}")
