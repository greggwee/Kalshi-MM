"""Session risk controls, layered on top of the strategy's own position cap.

    per market   max_position (strategy) - a fill can never breach it
                 max_market_loss         - halt the market, pull its quotes
                 max_rejects_per_min     - halt a market that keeps erroring
    session      max_gross_position      - sum |q| across markets; only
                                           position-reducing quotes past it
                 max_session_loss        - kill switch: pull everything, stop
                 max_consecutive_errors  - kill switch on a dead connection
                 min_balance             - kill switch on cash
                 exchange/trading inactive -> stand down (not a kill)

Losses are mark-to-market: realized + unrealized at the current mid - fees.
"""
import logging
from collections import deque
from typing import Dict, Optional, Tuple

DEFAULT_RISK = {
    "max_session_loss": 25.0,
    "max_market_loss": 10.0,
    "max_gross_position": 40,
    "max_consecutive_errors": 8,
    "min_balance": 0.0,
    "max_rejects_per_min": 12,
    "stale_book_s": 15.0,
}


class RiskManager:
    def __init__(self, cfg: Optional[dict], journal, logger: logging.Logger):
        self.cfg = {**DEFAULT_RISK, **(cfg or {})}
        self.journal = journal
        self.logger = logger
        self.kill_reason: Optional[str] = None
        self.halted: Dict[str, str] = {}
        self.consecutive_errors = 0
        self._rejects: Dict[str, deque] = {}

    # ---------------- events ----------------

    def _event(self, scope: str, reason: str, **fields):
        self.logger.warning(f"RISK {scope}: {reason}")
        self.journal.write("risk", scope=scope, reason=reason, **fields)

    def kill(self, reason: str, **fields):
        if self.kill_reason is None:
            self.kill_reason = reason
            self._event("session", reason, **fields)

    def halt_market(self, ticker: str, reason: str, **fields):
        if ticker not in self.halted:
            self.halted[ticker] = reason
            self._event("market", reason, ticker=ticker, **fields)

    def record_error(self, error: str):
        self.consecutive_errors += 1
        if self.consecutive_errors >= self.cfg["max_consecutive_errors"]:
            self.kill("consecutive_errors", count=self.consecutive_errors, last_error=error)

    def record_ok(self):
        self.consecutive_errors = 0

    def record_reject(self, ticker: str, now: float):
        q = self._rejects.setdefault(ticker, deque())
        q.append(now)
        while q and q[0] < now - 60.0:
            q.popleft()
        if len(q) >= self.cfg["max_rejects_per_min"]:
            self.halt_market(ticker, "reject_rate", rejects_last_min=len(q))

    # ---------------- checks ----------------

    def check_market(self, ticker: str, net_pnl: float):
        if net_pnl <= -abs(self.cfg["max_market_loss"]):
            self.halt_market(ticker, "max_market_loss", net_pnl=round(net_pnl, 4))

    def check_session(self, total_net_pnl: float, balance: Optional[float]):
        if total_net_pnl <= -abs(self.cfg["max_session_loss"]):
            self.kill("max_session_loss", net_pnl=round(total_net_pnl, 4))
        if balance is not None and balance < self.cfg["min_balance"]:
            self.kill("min_balance", balance=balance)

    def gate_sizes(self, q: float, bid_size: int, ask_size: int,
                   gross_headroom: float) -> Tuple[int, int, Optional[str]]:
        """Cap the position-INCREASING side(s) by the remaining gross headroom
        (shared across markets within a cycle). Reducing quotes always pass."""
        headroom = max(0, int(gross_headroom))
        reason = None
        if q >= 0 and bid_size > headroom:      # bids grow a long / flat book
            bid_size, reason = headroom, "gross_position_cap"
        if q <= 0 and ask_size > headroom:      # asks grow a short / flat book
            ask_size, reason = headroom, "gross_position_cap"
        return bid_size, ask_size, reason
