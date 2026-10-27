"""Client-side token buckets mirroring Kalshi's server-side rate limits.

Kalshi meters every authenticated request in tokens (default cost 10) drawn
from independent Read and Write buckets, plus a separate bucket for
GET /portfolio/balance. Basic tier (per docs, Oct 2026):

    Read   200 tokens/s, capacity 3 s of budget (600)
    Write  100 tokens/s, capacity 1 s of budget (100)  -> 10 creates/s
    Balance 1 request/s, no burst

Order creates/amends/decreases cost 10 tokens; cancels (single, batched per
order, or cancel-all) cost 2. Pacing ourselves keeps 429s rare; the HTTP
client still backs off on 429 because other processes may share the key.
"""
import threading
import time
from typing import Callable, Dict, Optional, Tuple

DEFAULT_COST = 10.0
CANCEL_COST = 2.0

# (rate tokens/s, capacity tokens) per bucket, Basic tier
BASIC_TIER_BUCKETS: Dict[str, Tuple[float, float]] = {
    "read": (200.0, 600.0),
    "write": (100.0, 100.0),
    "balance": (10.0, 10.0),   # 1 req/s at cost 10
}


class TokenBucket:
    def __init__(self, rate: float, capacity: float,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        if rate <= 0 or capacity <= 0:
            raise ValueError("rate and capacity must be positive")
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self._clock = clock
        self._sleep = sleep
        self._last = clock()
        self._lock = threading.Lock()

    def _refill(self):
        now = self._clock()
        self.tokens = min(self.capacity, self.tokens + (now - self._last) * self.rate)
        self._last = now

    def acquire(self, cost: float) -> float:
        """Block until `cost` tokens are available, then spend them. Returns
        seconds spent waiting. A cost above capacity is clamped (it could
        otherwise never be satisfied)."""
        cost = min(cost, self.capacity)
        waited = 0.0
        while True:
            with self._lock:
                self._refill()
                if self.tokens >= cost:
                    self.tokens -= cost
                    return waited
                wait = (cost - self.tokens) / self.rate
            self._sleep(wait)
            waited += wait


def endpoint_bucket(method: str, path: str, n_items: int = 1) -> Tuple[str, float]:
    """Which bucket a request draws from and its token cost."""
    method = method.upper()
    base = path.split("?")[0]
    if base == "/portfolio/balance":
        return "balance", DEFAULT_COST
    if method == "GET":
        return "read", DEFAULT_COST
    if base.startswith("/portfolio/events/orders") or base.startswith("/portfolio/orders"):
        if method == "DELETE":
            return "write", CANCEL_COST * max(n_items, 1)
        return "write", DEFAULT_COST * max(n_items, 1)
    return "read", DEFAULT_COST


class RateLimiter:
    """One bucket per Kalshi budget. safety < 1 leaves headroom for clock
    drift between our bucket and the exchange's."""

    def __init__(self, buckets: Optional[Dict[str, Tuple[float, float]]] = None,
                 safety: float = 0.9,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.safety = safety
        self._clock = clock
        self._sleep = sleep
        self.buckets: Dict[str, TokenBucket] = {}
        self.configure(buckets or BASIC_TIER_BUCKETS)

    def configure(self, buckets: Dict[str, Tuple[float, float]]):
        for name, (rate, capacity) in buckets.items():
            self.buckets[name] = TokenBucket(rate * self.safety, capacity * self.safety,
                                             clock=self._clock, sleep=self._sleep)

    def acquire(self, bucket: str, cost: float) -> float:
        b = self.buckets.get(bucket)
        return b.acquire(cost) if b else 0.0
