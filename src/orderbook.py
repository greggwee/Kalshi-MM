"""Local order book for a Kalshi market.

Kalshi's book is two buy-side ladders: resting YES bids and resting NO bids,
both priced in dollars. A YES ask at price p is equivalent to a NO bid at
(1 - p), so the full yes-axis quote picture is:

    best yes bid = max(yes ladder)
    best yes ask = 1 - max(no ladder)

Handles the three wire formats Kalshi currently emits:
  - REST GetOrderbook:   {"orderbook_fp": {"yes_dollars": [["0.36","12.00"], ...], "no_dollars": [...]}}
  - WS orderbook_snapshot: {"yes_dollars_fp": [["0.36","12.00"], ...], "no_dollars_fp": [...]}
  - WS orderbook_delta:    {"price_dollars": "0.36", "delta_fp": "-2.00", "side": "yes"}
"""
from typing import Dict, List, Optional, Tuple

Level = Tuple[float, float]  # (price_dollars, contracts)


def _parse_levels(levels) -> Dict[float, float]:
    out: Dict[float, float] = {}
    for price, count in levels or []:
        p = round(float(price), 4)
        c = float(count)
        if c > 0:
            out[p] = c
    return out


class OrderBook:
    def __init__(self, market_ticker: str):
        self.market_ticker = market_ticker
        self.yes: Dict[float, float] = {}
        self.no: Dict[float, float] = {}
        self.last_seq: Optional[int] = None
        self.last_update_ms: Optional[int] = None

    # ---------- ingestion ----------

    def apply_snapshot(self, msg: dict, seq: Optional[int] = None, ts_ms: Optional[int] = None):
        """Accepts either a REST orderbook_fp dict or a WS snapshot msg payload."""
        yes = msg.get("yes_dollars_fp", msg.get("yes_dollars"))
        no = msg.get("no_dollars_fp", msg.get("no_dollars"))
        self.yes = _parse_levels(yes)
        self.no = _parse_levels(no)
        self.last_seq = seq
        self.last_update_ms = ts_ms

    def apply_delta(self, side: str, price_dollars: str, delta_fp: str,
                    seq: Optional[int] = None, ts_ms: Optional[int] = None) -> bool:
        """Apply an incremental change. Returns False on a sequence gap
        (caller should resubscribe for a fresh snapshot)."""
        if seq is not None and self.last_seq is not None and seq != self.last_seq + 1:
            return False
        ladder = self.yes if side == "yes" else self.no
        p = round(float(price_dollars), 4)
        new_count = ladder.get(p, 0.0) + float(delta_fp)
        if new_count > 1e-9:
            ladder[p] = new_count
        else:
            ladder.pop(p, None)
        if seq is not None:
            self.last_seq = seq
        self.last_update_ms = ts_ms
        return True

    def remove_liquidity(self, side: str, price: float, count: float):
        """Net out contracts (e.g. our own resting orders) from a ladder so
        derived mids reflect everyone else's interest, not our own quotes."""
        ladder = self.yes if side == "yes" else self.no
        p = round(price, 4)
        if p in ladder:
            remaining = ladder[p] - count
            if remaining > 1e-9:
                ladder[p] = remaining
            else:
                del ladder[p]

    # ---------- derived quantities ----------

    @property
    def best_yes_bid(self) -> Optional[float]:
        return max(self.yes) if self.yes else None

    @property
    def best_no_bid(self) -> Optional[float]:
        return max(self.no) if self.no else None

    @property
    def best_yes_ask(self) -> Optional[float]:
        nb = self.best_no_bid
        return round(1.0 - nb, 4) if nb is not None else None

    @property
    def mid(self) -> Optional[float]:
        b, a = self.best_yes_bid, self.best_yes_ask
        if b is None or a is None:
            return None
        return round((b + a) / 2.0, 4)

    @property
    def spread(self) -> Optional[float]:
        b, a = self.best_yes_bid, self.best_yes_ask
        if b is None or a is None:
            return None
        return round(a - b, 4)

    def depth_profile(self) -> List[Tuple[float, float]]:
        """(distance_from_mid, cumulative_contracts) for both sides combined,
        sorted by distance. Used for fill-intensity (k) estimation."""
        m = self.mid
        if m is None:
            return []
        points = []
        for p, c in self.yes.items():           # bids: distance below mid
            points.append((round(m - p, 4), c))
        for p, c in self.no.items():            # asks at 1-p: distance above mid
            points.append((round((1.0 - p) - m, 4), c))
        points = [(d, c) for d, c in points if d > 0]
        points.sort()
        cum, out = 0.0, []
        for d, c in points:
            cum += c
            out.append((d, cum))
        return out

    def levels_json(self) -> Tuple[List[List[float]], List[List[float]]]:
        """Compact [[price, count], ...] lists for persistence, best-first."""
        yes = sorted(([p, c] for p, c in self.yes.items()), key=lambda x: -x[0])
        no = sorted(([p, c] for p, c in self.no.items()), key=lambda x: -x[0])
        return yes, no
