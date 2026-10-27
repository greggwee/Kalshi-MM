"""Order execution: reconcile desired quotes with resting orders.

At most one resting order per (market, side). Each cycle, per side:

    no desired quote        -> cancel whatever rests
    nothing resting         -> create (post-only, with an expiration so a
                               crashed process cannot leave quotes forever)
    price moved away from
      the market            -> amend (one write; cannot cross)
    price moved toward the
      market                -> cancel + post-only create (aggressive_reprice
                               = "replace", default). A price amend forfeits
                               queue priority anyway, and an amend that
                               crosses can FILL as a taker; a post-only create
                               is rejected instead. The create is skipped if
                               the cancel failed (the order may have filled,
                               and a blind re-create could breach max_position)
    size drifted            -> amend down at once (keeps queue priority);
                               replenish up only once remaining falls below
                               replenish_threshold * desired, so a partial
                               fill does not immediately forfeit our place
    expiry approaching      -> expiry-only amend (queue priority preserved)
    otherwise               -> keep

Every write is journaled with its latency and, on failure, the exchange's
error code/message, so rejects (post-only crosses, balance, rate limits) are
visible in the diagnostics report.
"""
import logging
import time
from collections import defaultdict
from typing import Callable, Dict, List, Optional, Tuple

from helper import TICK
from src.clients import KalshiAPIError

Desired = Optional[Tuple[float, int]]  # (yes price, size) or None


def classify_error(exc: Exception) -> str:
    if isinstance(exc, KalshiAPIError):
        text = f"{exc.code or ''} {exc.message or ''}".lower()
        if "post" in text and ("only" in text or "cross" in text):
            return "post_only_cross"
        if "insufficient" in text or "balance" in text:
            return "insufficient_balance"
        if exc.status == 429:
            return "rate_limited"
        if exc.status == 404 or "not found" in text:
            return "not_found"
        if exc.status in (401, 403):
            return "auth"
        return f"http_{exc.status}" + (f"_{exc.code}" if exc.code else "")
    return type(exc).__name__


class OrderManager:
    def __init__(self, gateway, journal, logger: logging.Logger,
                 order_expiration_s: int = 300, expiry_refresh_frac: float = 0.33,
                 replenish_threshold: float = 0.5, cancel_order_on_pause: bool = True,
                 post_only: bool = True, on_reject: Optional[Callable] = None,
                 aggressive_reprice: str = "replace",
                 clock: Callable[[], float] = time.time):
        if aggressive_reprice not in ("replace", "amend"):
            raise ValueError("aggressive_reprice must be 'replace' or 'amend'")
        self.aggressive_reprice = aggressive_reprice
        self.gw = gateway
        self.journal = journal
        self.logger = logger
        self.order_expiration_s = order_expiration_s
        self.expiry_refresh_frac = expiry_refresh_frac
        self.replenish_threshold = replenish_threshold
        self.cancel_order_on_pause = cancel_order_on_pause
        self.post_only = post_only
        self.on_reject = on_reject
        self.clock = clock
        self.counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        # our best knowledge of what rests on the exchange: synced from each
        # successful read, then updated by every successful write, so a later
        # read outage or shutdown can still pull orders created this cycle
        self.live: Dict[str, Dict[str, dict]] = defaultdict(dict)

    def sync(self, ticker: str, orders: List[dict]):
        self.live[ticker] = {o["order_id"]: dict(o) for o in orders}

    def known_orders(self, ticker: str) -> List[dict]:
        return [dict(o) for o in self.live.get(ticker, {}).values()]

    # ------------------------------------------------------------------

    def reconcile(self, ticker: str, desired: Dict[str, Desired], orders: List[dict]):
        self.sync(ticker, orders)
        by_side: Dict[str, List[dict]] = {"bid": [], "ask": []}
        for o in orders:
            by_side[o["side"]].append(o)
        # cancels before creates: free risk first, and keep the write budget
        # for the cheap side of the work if we are being throttled
        for side in ("bid", "ask"):
            if desired.get(side) is None:
                for o in by_side[side]:
                    self._cancel(ticker, o, "no_quote")
        # move the side that gets out of the way first: if the new bid would
        # reach our own resting ask (market moving up), shift the ask before
        # the bid, else a post-only bid is rejected for crossing ourselves
        order = ("bid", "ask")
        want_bid = desired.get("bid")
        if want_bid is not None and any(o["price"] <= want_bid[0] + 1e-9
                                        for o in by_side["ask"]):
            order = ("ask", "bid")
        for side in order:
            if desired.get(side) is not None:
                self._reconcile_side(ticker, side, desired[side], by_side[side])

    def cancel_all(self, ticker: str, orders: List[dict], reason: str):
        for o in orders:
            self._cancel(ticker, o, reason)

    # ------------------------------------------------------------------

    def _reconcile_side(self, ticker: str, side: str, want: Tuple[float, int],
                        orders: List[dict]):
        price, size = want
        if size <= 0:
            for o in orders:
                self._cancel(ticker, o, "zero_size")
            return
        # keep the order closest to what we want; cancel duplicates
        orders = sorted(orders, key=lambda o: (abs(o["price"] - price), -o["remaining"]))
        keep, extras = (orders[0], orders[1:]) if orders else (None, [])
        for o in extras:
            self._cancel(ticker, o, "duplicate")
        if keep is None:
            self._create(ticker, side, price, size)
            return

        same_price = abs(keep["price"] - price) < TICK / 2
        remaining = keep["remaining"]
        if not same_price:
            toward = price > keep["price"] if side == "bid" else price < keep["price"]
            if toward and self.aggressive_reprice == "replace":
                if self._cancel(ticker, keep, "reprice_toward"):
                    self._create(ticker, side, price, size)
            else:
                self._amend(ticker, keep, price, size, "reprice")
        elif remaining > size + 1e-9:
            self._amend(ticker, keep, price, size, "size_down")
        elif remaining < self.replenish_threshold * size - 1e-9:
            self._amend(ticker, keep, price, size, "replenish")
        elif self._expiring(keep):
            self._amend(ticker, keep, price, remaining, "refresh_expiry")
        else:
            self.counts[ticker]["keep"] += 1

    def _expiring(self, order: dict) -> bool:
        exp = order.get("expiration_ts")
        if not exp or not self.order_expiration_s:
            return False
        return exp - self.clock() < self.order_expiration_s * self.expiry_refresh_frac

    def _new_expiration(self) -> Optional[int]:
        return int(self.clock() + self.order_expiration_s) if self.order_expiration_s else None

    # ---------------- writes ----------------

    def _create(self, ticker: str, side: str, price: float, size: int):
        t0 = time.perf_counter()
        try:
            expiration = self._new_expiration()
            resp = self.gw.create_order(
                ticker=ticker, side=side, count=size, price=price,
                expiration_ts=expiration, post_only=self.post_only,
                cancel_order_on_pause=self.cancel_order_on_pause)
            if resp.get("order_id"):
                self.live[ticker][resp["order_id"]] = {
                    "order_id": resp["order_id"], "ticker": ticker, "side": side,
                    "price": price, "remaining": resp.get("remaining_count", size),
                    "filled": resp.get("fill_count") or 0.0, "expiration_ts": expiration}
            self._log("create", ticker, side, price=price, size=size, ok=True, t0=t0,
                      order_id=resp.get("order_id"),
                      immediate_fill=resp.get("fill_count") or 0.0)
        except Exception as e:
            self._fail("create", ticker, side, e, t0, price=price, size=size)

    def _amend(self, ticker: str, order: dict, price: float, size: float, why: str):
        t0 = time.perf_counter()
        total = order["filled"] + size  # V2 amend count = filled + desired remaining
        try:
            expiration = self._new_expiration()
            self.gw.amend_order(order["order_id"], ticker=ticker, side=order["side"],
                                count=total, price=price, expiration_ts=expiration)
            live = self.live[ticker].get(order["order_id"])
            if live is not None:
                live.update(price=price, remaining=size, expiration_ts=expiration)
            self._log("amend", ticker, order["side"], price=price, size=size, ok=True,
                      t0=t0, order_id=order["order_id"], why=why,
                      prev_price=order["price"], prev_remaining=order["remaining"])
        except Exception as e:
            self._fail("amend", ticker, order["side"], e, t0, price=price, size=size,
                       order_id=order["order_id"], why=why)

    def _cancel(self, ticker: str, order: dict, why: str) -> bool:
        t0 = time.perf_counter()
        try:
            self.gw.cancel_order(order["order_id"], ticker)
            self.live[ticker].pop(order["order_id"], None)
            self._log("cancel", ticker, order["side"], price=order["price"],
                      size=order["remaining"], ok=True, t0=t0,
                      order_id=order["order_id"], why=why)
            return True
        except Exception as e:
            if classify_error(e) == "not_found":  # already filled / expired / cancelled
                self.live[ticker].pop(order["order_id"], None)
            self._fail("cancel", ticker, order["side"], e, t0, price=order["price"],
                       size=order["remaining"], order_id=order["order_id"], why=why)
            return False

    def _log(self, action: str, ticker: str, side: str, ok: bool, t0: float, **fields):
        latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        self.counts[ticker][action] += 1
        self.journal.write("order", action=action, ticker=ticker, side=side, ok=ok,
                           latency_ms=latency_ms, **fields)
        self.logger.info(f"{ticker} {action} {side} "
                         f"{fields.get('size', '')}@{fields.get('price', 0):.2f} "
                         f"{fields.get('why', '')} ({latency_ms:.0f}ms)")

    def _fail(self, action: str, ticker: str, side: str, exc: Exception, t0: float,
              **fields):
        latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        reason = classify_error(exc)
        self.counts[ticker][f"{action}_failed"] += 1
        self.counts[ticker][f"reject:{reason}"] += 1
        self.journal.write("order", action=action, ticker=ticker, side=side, ok=False,
                           latency_ms=latency_ms, reject_reason=reason,
                           error_code=getattr(exc, "code", None),
                           error=str(exc)[:300], **fields)
        self.logger.warning(f"{ticker} {action} {side} failed ({reason}): {exc}")
        # a cancel of an order that already filled/expired is not a strategy reject
        if self.on_reject is not None and not (action == "cancel" and reason == "not_found"):
            self.on_reject(ticker)
