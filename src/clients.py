import requests
import base64
import os
import threading
import time
from collections import defaultdict, deque
from typing import Any, Callable, Dict, List, Optional
from datetime import datetime
from enum import Enum
import json

from requests.exceptions import HTTPError

from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa
from cryptography.exceptions import InvalidSignature

from src.ratelimit import RateLimiter, endpoint_bucket

try:
    import websockets
except ImportError:  # only KalshiWebSocketClient needs it
    websockets = None

class Environment(Enum):
    DEMO = "demo"
    PROD = "prod"


class KalshiAPIError(HTTPError):
    """Non-2xx response. Carries Kalshi's error code/message so callers can
    tell a post-only cross from an auth failure."""

    def __init__(self, status: int, method: str, path: str, code: Optional[str] = None,
                 message: Optional[str] = None, body: Any = None,
                 response: Optional[requests.Response] = None):
        self.status = status
        self.method = method
        self.path = path
        self.code = code
        self.message = message
        self.body = body
        super().__init__(f"{status} {method} {path}: {code or ''} {message or ''}".strip(),
                         response=response)

    @classmethod
    def from_response(cls, response: requests.Response, method: str, path: str):
        code = message = None
        try:
            body = response.json()
        except ValueError:
            body = response.text[:500]
        if isinstance(body, dict):
            err = body.get("error", body)
            if isinstance(err, dict):
                code, message = err.get("code"), err.get("message") or err.get("details")
            else:
                message = str(err)
        return cls(response.status_code, method, path, code, message, body, response)


_STATIC_PATH_SEGMENTS = {
    "trade-api", "v2", "portfolio", "events", "orders", "batched", "amend", "decrease",
    "markets", "orderbook", "orderbooks", "trades", "positions", "fills", "balance",
    "exchange", "status", "schedule", "series", "account", "limits", "endpoint_costs",
    "settlements", "queue_positions", "queue_position", "candlesticks",
}


def path_template(path: str) -> str:
    """'/markets/KXFOO-1/orderbook' -> '/markets/{x}/orderbook' for stats keys."""
    parts = path.split("?")[0].split("/")
    return "/".join(p if (not p or p in _STATIC_PATH_SEGMENTS) else "{x}" for p in parts)


class RequestStats:
    """Per-endpoint request counts, error counts and latency samples."""

    def __init__(self, max_samples: int = 5000):
        self._lock = threading.Lock()
        self.counts: Dict[str, int] = defaultdict(int)
        self.errors: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.latency_ms: Dict[str, deque] = defaultdict(lambda: deque(maxlen=max_samples))
        self.throttle_wait_s = 0.0
        self.retries = 0

    def record(self, key: str, status: Optional[int], latency_ms: float, waited_s: float):
        with self._lock:
            self.counts[key] += 1
            self.latency_ms[key].append(latency_ms)
            self.throttle_wait_s += waited_s
            if status is None or status >= 300:
                self.errors[key][str(status or "conn")] += 1

    def snapshot(self) -> dict:
        def pct(samples, q):
            s = sorted(samples)
            return round(s[min(len(s) - 1, int(q * len(s)))], 1) if s else None
        with self._lock:
            endpoints = {}
            for key, n in self.counts.items():
                lat = self.latency_ms[key]
                endpoints[key] = {"count": n, "errors": dict(self.errors.get(key, {})),
                                  "p50_ms": pct(lat, 0.5), "p95_ms": pct(lat, 0.95),
                                  "max_ms": round(max(lat), 1) if lat else None}
            return {"endpoints": endpoints, "throttle_wait_s": round(self.throttle_wait_s, 2),
                    "retries": self.retries,
                    "total_requests": sum(self.counts.values()),
                    "total_errors": sum(sum(e.values()) for e in self.errors.values())}

class KalshiBaseClient:
    """Base client class for interacting with the Kalshi API."""
    def __init__(
        self,
        key_id: str,
        private_key: rsa.RSAPrivateKey,
        environment: Environment = Environment.DEMO,
    ):
        """Initializes the client with the provided API key and private key.

        Args:
            key_id (str): Your Kalshi API key ID.
            private_key (rsa.RSAPrivateKey): Your RSA private key.
            environment (Environment): The API environment to use (DEMO or PROD).
        """
        self.key_id = key_id
        self.private_key = private_key
        self.environment = environment
        self.last_api_call = datetime.now()

        # Dedicated external Trade API hosts (docs, 2026). They serve the whole
        # REST surface including the V2 /portfolio/events/orders endpoints (the
        # legacy /portfolio/orders mutations were retired in June 2026), so one
        # host covers everything. KALSHI_API_BASE / KALSHI_WS_BASE override.
        if self.environment == Environment.DEMO:
            self.HTTP_BASE_URL = "https://external-api.demo.kalshi.co/trade-api/v2"
            self.WS_BASE_URL = "wss://external-api-ws.demo.kalshi.co"
        elif self.environment == Environment.PROD:
            self.HTTP_BASE_URL = "https://external-api.kalshi.com/trade-api/v2"
            self.WS_BASE_URL = "wss://external-api-ws.kalshi.com"
        else:
            raise ValueError("Invalid environment")
        self.HTTP_BASE_URL = os.getenv("KALSHI_API_BASE", self.HTTP_BASE_URL).rstrip("/")
        self.WS_BASE_URL = os.getenv("KALSHI_WS_BASE", self.WS_BASE_URL).rstrip("/")
        self.V2_BASE_URL = self.HTTP_BASE_URL

    @property
    def authenticated(self) -> bool:
        return self.private_key is not None

    def request_headers(self, method: str, path: str) -> Dict[str, Any]:
        """Generates the required authentication headers for API requests.
        Without a key (public market-data mode) requests go out unsigned."""
        if self.private_key is None:
            return {"Content-Type": "application/json"}
        current_time_milliseconds = int(time.time() * 1000)
        timestamp_str = str(current_time_milliseconds)

        # Remove query params from path
        path_parts = path.split('?')

        if path_parts[0].startswith('/trade-api/ws'):
            msg_string = timestamp_str + method + path_parts[0] # For WebSocket
        else:
            msg_string = timestamp_str + method + '/trade-api/v2' + path_parts[0] # For HTTP
        
        signature = self.sign_pss_text(msg_string)

        headers = {
            "Content-Type": "application/json",
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "KALSHI-ACCESS-TIMESTAMP": timestamp_str,
        }
        return headers

    def sign_pss_text(self, text: str) -> str:
        """Signs the text using RSA-PSS (or Ed25519 for Ed25519 keys) and
        returns the base64 encoded signature."""
        message = text.encode('utf-8')
        if isinstance(self.private_key, ed25519.Ed25519PrivateKey):
            return base64.b64encode(self.private_key.sign(message)).decode('utf-8')
        try:
            signature = self.private_key.sign(
                message,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.DIGEST_LENGTH
                ),
                hashes.SHA256()
            )
            return base64.b64encode(signature).decode('utf-8')
        except InvalidSignature as e:
            raise ValueError("RSA sign PSS failed") from e

class KalshiHttpClient(KalshiBaseClient):
    """Client for handling HTTP connections to the Kalshi API."""
    def __init__(
        self,
        key_id: str,
        private_key: rsa.RSAPrivateKey,
        environment: Environment = Environment.DEMO,
        timeout: tuple = (5.0, 15.0),
        max_retries: int = 4,
        limiter: Optional[RateLimiter] = None,
    ):
        super().__init__(key_id, private_key, environment)
        self.host = self.HTTP_BASE_URL
        self.exchange_url = "/exchange"
        self.markets_url = "/markets"
        self.portfolio_url = "/portfolio"
        self.events_url = "/events"
        self.series_url = "/series"
        self.timeout = timeout
        self.max_retries = max_retries
        self.limiter = limiter or RateLimiter()
        self.stats = RequestStats()
        self.session = requests.Session()
        self.on_error: Optional[Callable[[dict], None]] = None  # e.g. a session journal
        self.last_server_date: Optional[str] = None

    def raise_if_bad_response(self, response: requests.Response) -> None:
        """Raises an HTTPError if the response status code indicates an error."""
        if not 200 <= response.status_code < 300:
            response.raise_for_status()

    def _request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None,
                 body: Optional[dict] = None, n_items: int = 1,
                 idempotent: Optional[bool] = None) -> Any:
        """Signed, paced request with retries. 429s are always retried (the
        request was not processed); 5xx/connection errors only for idempotent
        requests, so a timed-out order create is never blindly re-sent (the
        caller re-syncs from GET /portfolio/orders instead)."""
        if idempotent is None:
            idempotent = method in ("GET", "DELETE")
        bucket, cost = endpoint_bucket(method, path, n_items)
        key = f"{method} {path_template(path)}"
        attempt = 0
        while True:
            waited = self.limiter.acquire(bucket, cost)
            t0 = time.perf_counter()
            try:
                response = self.session.request(
                    method, self.host + path, params=params, json=body,
                    headers=self.request_headers(method, path), timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout) as e:
                self.stats.record(key, None, (time.perf_counter() - t0) * 1000, waited)
                if idempotent and attempt < self.max_retries:
                    attempt += 1
                    self.stats.retries += 1
                    time.sleep(min(0.25 * 2 ** attempt, 4.0))
                    continue
                self._report_error({"endpoint": key, "status": None, "error": repr(e)})
                raise
            latency_ms = (time.perf_counter() - t0) * 1000
            self.stats.record(key, response.status_code, latency_ms, waited)
            self.last_server_date = response.headers.get("Date", self.last_server_date)
            status = response.status_code
            retryable = status == 429 or (status >= 500 and idempotent)
            if retryable and attempt < self.max_retries:
                attempt += 1
                self.stats.retries += 1
                time.sleep(min(0.25 * 2 ** attempt, 4.0))
                continue
            if not 200 <= status < 300:
                err = KalshiAPIError.from_response(response, method, path)
                self._report_error({"endpoint": key, "status": status, "code": err.code,
                                    "message": err.message})
                raise err
            if status == 204 or not response.content:
                return {}
            return response.json()

    def _report_error(self, info: dict):
        if self.on_error is not None:
            try:
                self.on_error(info)
            except Exception:
                pass

    def post(self, path: str, body: dict) -> Any:
        """Performs an authenticated POST request to the Kalshi API."""
        return self._request("POST", path, body=body)

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Performs an authenticated GET request to the Kalshi API."""
        return self._request("GET", path, params=params or {})

    def delete(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Performs an authenticated DELETE request to the Kalshi API."""
        return self._request("DELETE", path, params=params or {})

    def paginate(self, path: str, key: str, params: Optional[Dict[str, Any]] = None,
                 max_pages: int = 50) -> List[dict]:
        """Follow `cursor` until exhausted; returns the concatenated `key` lists."""
        params = dict(params or {})
        out: List[dict] = []
        for _ in range(max_pages):
            data = self.get(path, params=params)
            out.extend(data.get(key) or [])
            cursor = data.get("cursor")
            if not cursor:
                break
            params["cursor"] = cursor
        return out

    def query_generation(self, params: dict) -> str:
        """
        Generate URL query string from params dict, filtering out None values.
        Note: This is kept for compatibility but prefer using requests' params parameter.
        """
        relevant_params = {k: v for k, v in params.items() if v is not None}
        if len(relevant_params):
            query = '?' + ''.join("&" + str(k) + "=" + str(v) for k, v in relevant_params.items())[1:]
        else:
            query = ''
        return query

    def get_balance(self) -> Dict[str, Any]:
        """Retrieves the account balance."""
        return self.get(self.portfolio_url + '/balance')

    def get_exchange_status(self) -> Dict[str, Any]:
        """Retrieves the exchange status."""
        return self.get(self.exchange_url + "/status")
    
    def get_markets(
    self,
    limit: Optional[int] = None,
    cursor: Optional[str] = None,
    event_ticker: Optional[str] = None,
    series_ticker: Optional[str] = None,
    max_close_ts: Optional[int] = None,
    min_close_ts: Optional[int] = None,
    status: Optional[str] = None,
    tickers: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Retrieves markets based on provided filters."""
        params = {
            'limit': limit,
            'cursor': cursor,
            'event_ticker': event_ticker,
            'series_ticker': series_ticker,
            'max_close_ts': max_close_ts,
            'min_close_ts': min_close_ts,
            'status': status,
            'tickers': tickers,
        }
        params = {k: v for k, v in params.items() if v is not None}
        return self.get(self.markets_url, params=params)
    
    def get_market(self, ticker: str) -> Dict[str, Any]:
        """Retrieves a specific market by ticker."""
        return self.get(f"{self.markets_url}/{ticker}")

    def get_market_url(self, ticker: str) -> str:
        """Helper method to construct market URL."""
        return f"{self.markets_url}/{ticker}"

    def get_orderbook(
    self,
    ticker: str,
    depth: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Retrieves the order book for a specific market."""
        params = {}
        if depth is not None:
            params['depth'] = depth
        return self.get(f"{self.markets_url}/{ticker}/orderbook", params=params)

    def get_trades(
        self,
        ticker: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        max_ts: Optional[int] = None,
        min_ts: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Retrieves trades based on provided filters."""
        params = {
            'ticker': ticker,
            'limit': limit,
            'cursor': cursor,
            'max_ts': max_ts,
            'min_ts': min_ts,
        }
        # Remove None values
        params = {k: v for k, v in params.items() if v is not None}
        return self.get(self.markets_url + '/trades', params=params)
    
    def get_market_history(
    self,
    series_ticker: str,
    market_ticker: str,
    period_interval: int,
    start_ts: int,
    end_ts: int,
    ) -> Dict[str, Any]:
        """Retrieves candlestick/historical data for a market."""
        params = {
            'period_interval': period_interval,
            'start_ts': start_ts,
            'end_ts': end_ts,
        }
        url = f"{self.series_url}/{series_ticker}/markets/{market_ticker}/candlesticks"
        return self.get(url, params=params)
    
    def get_event(self, event_ticker: str) -> Dict[str, Any]:
        """Retrieves event information by event ticker."""
        return self.get(f"{self.events_url}/{event_ticker}")
    
    def get_series(self, series_ticker: str) -> Dict[str, Any]:
        """Retrieves series information by series ticker."""
        return self.get(f"{self.series_url}/{series_ticker}")
    
    def get_positions(
    self,
    limit: Optional[int] = None,
    cursor: Optional[str] = None,
    settlement_status: Optional[str] = None,
    ticker: Optional[str] = None,
    event_ticker: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Retrieves current positions with optional filters."""
        params = {
            'limit': limit,
            'cursor': cursor,
            'settlement_status': settlement_status,
            'ticker': ticker,
            'event_ticker': event_ticker,
        }
        params = {k: v for k, v in params.items() if v is not None}
        return self.get(f"{self.portfolio_url}/positions", params=params)
    
    def get_fills(
    self,
    ticker: Optional[str] = None,
    order_id: Optional[str] = None,
    min_ts: Optional[int] = None,
    max_ts: Optional[int] = None,
    limit: Optional[int] = None,
    cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Retrieves fill history with optional filters."""
        params = {
            'ticker': ticker,
            'order_id': order_id,
            'min_ts': min_ts,
            'max_ts': max_ts,
            'limit': limit,
            'cursor': cursor,
        }
        params = {k: v for k, v in params.items() if v is not None}
        return self.get(f"{self.portfolio_url}/fills", params=params)
    
    def get_orders(
    self,
    ticker: Optional[str] = None,
    event_ticker: Optional[str] = None,
    min_ts: Optional[int] = None,
    max_ts: Optional[int] = None,
    limit: Optional[int] = None,
    cursor: Optional[str] = None,
    status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Retrieves order history with optional filters."""
        params = {
            'ticker': ticker,
            'event_ticker': event_ticker,
            'min_ts': min_ts,
            'max_ts': max_ts,
            'limit': limit,
            'cursor': cursor,
            'status': status,
        }
        params = {k: v for k, v in params.items() if v is not None}
        return self.get(f"{self.portfolio_url}/orders", params=params)
    
    def get_order(self, order_id: str) -> Dict[str, Any]:
        """Retrieves a specific order by ID."""
        return self.get(f"{self.portfolio_url}/orders/{order_id}")
    
    def get_portfolio_settlements(
    self,
    limit: Optional[int] = None,
    cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Retrieves settlement history."""
        params = {}
        if limit is not None:
            params['limit'] = limit
        if cursor is not None:
            params['cursor'] = cursor
        return self.get(f"{self.portfolio_url}/settlements", params=params)
    
    def create_order(
    self,
    ticker: str,
    client_order_id: str,
    side: str,
    action: str,
    count: int,
    type: str,
    yes_price: Optional[int] = None,
    no_price: Optional[int] = None,
    expiration_ts: Optional[int] = None,
    sell_position_floor: Optional[int] = None,
    buy_max_cost: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Creates a new order.
        
        Args:
            ticker: Market ticker symbol
            client_order_id: Unique client-generated order ID
            side: "yes" or "no"
            action: "buy" or "sell"
            count: Number of contracts
            type: Order type (e.g., "limit")
            yes_price: Price in cents for yes side (optional)
            no_price: Price in cents for no side (optional)
            expiration_ts: Unix timestamp for order expiration (optional)
            sell_position_floor: Minimum position to maintain when selling (optional)
            buy_max_cost: Maximum cost in cents for buy orders (optional)
        
        Returns:
            Order creation response with order details
        """
        body = {
            'ticker': ticker,
            'client_order_id': client_order_id,
            'side': side,
            'action': action,
            'count': count,
            'type': type,
        }
        
        # Add optional parameters
        if yes_price is not None:
            body['yes_price'] = yes_price
        if no_price is not None:
            body['no_price'] = no_price
        if expiration_ts is not None:
            body['expiration_ts'] = expiration_ts
        if sell_position_floor is not None:
            body['sell_position_floor'] = sell_position_floor
        if buy_max_cost is not None:
            body['buy_max_cost'] = buy_max_cost
        
        return self.post(f"{self.portfolio_url}/orders", body=body)
    
    def batch_create_orders(self, orders: list) -> Dict[str, Any]:
        """Creates multiple orders in a single request.
        
        Args:
            orders: List of order dictionaries, each containing order parameters
        
        Returns:
            Batch order creation response
        """
        body = {'orders': orders}
        return self.post(f"{self.portfolio_url}/orders/batched", body=body)
    
    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """Cancels an existing order.
        
        Args:
            order_id: The ID of the order to cancel
        
        Returns:
            Cancellation response
        """
        return self.delete(f"{self.portfolio_url}/orders/{order_id}/cancel")
    
    def batch_cancel_orders(self, order_ids: list) -> Dict[str, Any]:
        """Cancels multiple orders in a single request.
        
        Args:
            order_ids: List of order IDs to cancel
        
        Returns:
            Batch cancellation response
        """
        body = {'ids': order_ids}
        return self.delete(f"{self.portfolio_url}/orders/batched", params=body) # Need to check if Kalshi API accepts DELETE with JSON body or if we need to use POST to a cancel endpoint or if we need to pass as query params
    
    # ------------------------------------------------------------------
    # V2 order API (the only supported way to create/amend/cancel orders
    # since the legacy /portfolio/orders endpoints were retired in May 2026).
    #
    # Semantics: single yes-axis book. side="bid" rests a buy of YES exposure;
    # side="ask" rests a sell of YES exposure (equivalently a NO bid at 1-price)
    # and requires NO inventory. Prices are dollar strings, counts are
    # fixed-point contract strings.
    # ------------------------------------------------------------------

    def _v2_request(self, method: str, path: str, body: Optional[dict] = None,
                    params: Optional[Dict[str, Any]] = None, n_items: int = 1,
                    idempotent: Optional[bool] = None) -> Any:
        """Signed request against the V2 order endpoints (same host and
        signature scheme as everything else)."""
        return self._request(method, path, params=params, body=body, n_items=n_items,
                             idempotent=idempotent)

    @staticmethod
    def v2_order_body(ticker: str, side: str, count: float, price: float,
                      client_order_id: Optional[str] = None,
                      time_in_force: str = "good_till_canceled", post_only: bool = True,
                      expiration_ts: Optional[int] = None,
                      self_trade_prevention_type: str = "maker",
                      reduce_only: bool = False,
                      cancel_order_on_pause: bool = False) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "ticker": ticker,
            "side": side,
            "count": f"{count:.2f}",
            "price": f"{price:.4f}",
            "time_in_force": time_in_force,
            "post_only": post_only,
            "self_trade_prevention_type": self_trade_prevention_type,
        }
        if client_order_id is not None:
            body["client_order_id"] = client_order_id
        if expiration_ts is not None:
            body["expiration_time"] = int(expiration_ts)
        if reduce_only:
            body["reduce_only"] = True
        if cancel_order_on_pause:
            body["cancel_order_on_pause"] = True
        return body

    def create_order_v2(
        self,
        ticker: str,
        side: str,                    # "bid" | "ask" (yes-axis price for both)
        count: float,
        price: float,                 # dollars, e.g. 0.56
        client_order_id: Optional[str] = None,
        time_in_force: str = "good_till_canceled",
        post_only: bool = True,       # maker-only: reject instead of crossing
        expiration_ts: Optional[int] = None,
        self_trade_prevention_type: str = "maker",
        reduce_only: bool = False,
        cancel_order_on_pause: bool = False,
    ) -> Dict[str, Any]:
        """Response: order_id, client_order_id, fill_count, remaining_count,
        average_fill_price (if filled), ts_ms."""
        body = self.v2_order_body(ticker, side, count, price, client_order_id,
                                  time_in_force, post_only, expiration_ts,
                                  self_trade_prevention_type, reduce_only,
                                  cancel_order_on_pause)
        return self._v2_request("POST", "/portfolio/events/orders", body=body)

    def amend_order_v2(self, order_id: str, ticker: str, side: str, count: float,
                       price: float, client_order_id: Optional[str] = None,
                       updated_client_order_id: Optional[str] = None,
                       expiration_ts: Optional[int] = None) -> Dict[str, Any]:
        """count = already-filled + desired remaining. Price changes forfeit
        queue priority; size decreases and expiry-only amends preserve it."""
        body: Dict[str, Any] = {
            "ticker": ticker,
            "side": side,
            "count": f"{count:.2f}",
            "price": f"{price:.4f}",
        }
        if client_order_id is not None:
            body["client_order_id"] = client_order_id
        if updated_client_order_id is not None:
            body["updated_client_order_id"] = updated_client_order_id
        if expiration_ts is not None:
            body["expiration_time"] = int(expiration_ts)
        # absolute price/count: re-sending after a timeout is harmless
        return self._v2_request("POST", f"/portfolio/events/orders/{order_id}/amend",
                                body=body, idempotent=True)

    def cancel_order_v2(self, order_id: str, market_ticker: Optional[str] = None
                        ) -> Dict[str, Any]:
        """market_ticker lets the exchange auto-route to the right shard."""
        params = {"market_ticker": market_ticker} if market_ticker else None
        return self._v2_request("DELETE", f"/portfolio/events/orders/{order_id}",
                                params=params)

    def decrease_order_v2(self, order_id: str, reduce_to: Optional[float] = None,
                          reduce_by: Optional[float] = None,
                          market_ticker: Optional[str] = None) -> Dict[str, Any]:
        if (reduce_to is None) == (reduce_by is None):
            raise ValueError("exactly one of reduce_to / reduce_by")
        body: Dict[str, Any] = {}
        if reduce_to is not None:
            body["reduce_to"] = f"{reduce_to:.2f}"
        else:
            body["reduce_by"] = f"{reduce_by:.2f}"
        if market_ticker:
            body["market_ticker"] = market_ticker
        return self._v2_request("POST", f"/portfolio/events/orders/{order_id}/decrease",
                                body=body, idempotent=reduce_to is not None)

    def batch_create_orders_v2(self, orders: List[Dict[str, Any]]) -> Dict[str, Any]:
        """orders: bodies from v2_order_body(). Per-order errors come back in
        each entry's `error` field rather than failing the whole request."""
        return self._v2_request("POST", "/portfolio/events/orders/batched",
                                body={"orders": orders}, n_items=len(orders))

    def batch_cancel_orders_v2(self, orders: List[Dict[str, Any]]) -> Dict[str, Any]:
        """orders: [{"order_id": ..., "market_ticker": ...}, ...]"""
        return self._v2_request("DELETE", "/portfolio/events/orders/batched",
                                body={"orders": orders}, n_items=len(orders))

    def cancel_all_orders_v2(self) -> Dict[str, Any]:
        """Cancels EVERY resting order on the account (all markets)."""
        return self._v2_request("DELETE", "/portfolio/events/orders")

    # ------------------------------------------------------------------
    # batched / paginated reads used by the live session engine
    # ------------------------------------------------------------------

    def get_orderbooks(self, tickers: List[str]) -> List[Dict[str, Any]]:
        """Up to 100 books per request -> [{"ticker", "orderbook_fp"}, ...]."""
        out: List[Dict[str, Any]] = []
        for i in range(0, len(tickers), 100):
            chunk = tickers[i:i + 100]
            out.extend(self.get(f"{self.markets_url}/orderbooks",
                                params={"tickers": chunk}).get("orderbooks", []))
        return out

    def get_all_orders(self, tickers: Optional[List[str]] = None,
                       status: Optional[str] = None, limit: int = 1000) -> List[dict]:
        params: Dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status
        if not tickers:
            return self.paginate(f"{self.portfolio_url}/orders", "orders", params)
        out: List[dict] = []
        for i in range(0, len(tickers), 100):
            params["ticker"] = ",".join(tickers[i:i + 100])
            out.extend(self.paginate(f"{self.portfolio_url}/orders", "orders", dict(params)))
        return out

    def get_all_positions(self, count_filter: Optional[str] = "position",
                          settlement_status: Optional[str] = None) -> List[dict]:
        params: Dict[str, Any] = {"limit": 1000}
        if count_filter:
            params["count_filter"] = count_filter
        if settlement_status:
            params["settlement_status"] = settlement_status
        return self.paginate(f"{self.portfolio_url}/positions", "market_positions", params)

    def get_all_fills(self, min_ts: Optional[int] = None, ticker: Optional[str] = None,
                      limit: int = 1000) -> List[dict]:
        params: Dict[str, Any] = {"limit": limit}
        if min_ts is not None:
            params["min_ts"] = int(min_ts)
        if ticker:
            params["ticker"] = ticker
        return self.paginate(f"{self.portfolio_url}/fills", "fills", params)

    def get_account_limits(self) -> Dict[str, Any]:
        """usage_tier plus read/write {refill_rate, bucket_capacity}."""
        return self.get("/account/limits")

    def get_endpoint_costs(self) -> Dict[str, Any]:
        return self.get("/account/endpoint_costs")

    def decrease_order(self, order_id: str, reduce_by: int) -> Dict[str, Any]:
        """Decreases the size of an existing order.
        
        Args:
            order_id: The ID of the order to decrease
            reduce_by: Number of contracts to reduce by
        
        Returns:
            Order decrease response
        """
        body = {'reduce_by': reduce_by}
        return self.post(f"{self.portfolio_url}/orders/{order_id}/decrease", body=body)

class KalshiWebSocketClient(KalshiBaseClient):
    """Client for handling WebSocket connections to the Kalshi API."""
    def __init__(
        self,
        key_id: str,
        private_key: rsa.RSAPrivateKey,
        environment: Environment = Environment.DEMO,
    ):
        super().__init__(key_id, private_key, environment)
        self.ws = None
        self.url_suffix = "/trade-api/ws/v2"
        self.message_id = 1  # Add counter for message IDs

    async def connect(self):
        """Establishes a WebSocket connection using authentication."""
        host = self.WS_BASE_URL + self.url_suffix
        auth_headers = self.request_headers("GET", self.url_suffix)
        async with websockets.connect(host, additional_headers=auth_headers) as websocket:
            self.ws = websocket
            await self.on_open()
            await self.handler()

    async def on_open(self):
        """Callback when WebSocket connection is opened."""
        print("WebSocket connection opened.")
        await self.subscribe_to_tickers()

    async def subscribe_to_tickers(self):
        """Subscribe to ticker updates for all markets."""
        subscription_message = {
            "id": self.message_id,
            "cmd": "subscribe",
            "params": {
                "channels": ["ticker"]
            }
        }
        await self.ws.send(json.dumps(subscription_message))
        self.message_id += 1

    async def handler(self):
        """Handle incoming messages."""
        try:
            async for message in self.ws:
                await self.on_message(message)
        except websockets.ConnectionClosed as e:
            await self.on_close(e.code, e.reason)
        except Exception as e:
            await self.on_error(e)

    async def on_message(self, message):
        """Callback for handling incoming messages."""
        print("Received message:", message)

    async def on_error(self, error):
        """Callback for handling errors."""
        print("WebSocket error:", error)

    async def on_close(self, close_status_code, close_msg):
        """Callback when WebSocket connection is closed."""
        print("WebSocket connection closed with code:", close_status_code, "and message:", close_msg)