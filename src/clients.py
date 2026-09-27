import requests
import base64
import time
from typing import Any, Dict, Optional
from datetime import datetime, timedelta
from enum import Enum
import json

from requests.exceptions import HTTPError

from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.exceptions import InvalidSignature

import websockets

class Environment(Enum):
    DEMO = "demo"
    PROD = "prod"

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

        if self.environment == Environment.DEMO:
            self.HTTP_BASE_URL = "https://demo-api.kalshi.co/trade-api/v2"
            self.WS_BASE_URL = "wss://demo-api.kalshi.co"
            # V2 order endpoints live on a different host (legacy POST /portfolio/orders
            # returns 410 Gone since May 2026)
            self.V2_BASE_URL = "https://external-api.demo.kalshi.co/trade-api/v2"
        elif self.environment == Environment.PROD:
            self.HTTP_BASE_URL = "https://api.elections.kalshi.com"
            self.WS_BASE_URL = "wss://api.elections.kalshi.com"
            self.V2_BASE_URL = "https://external-api.kalshi.com/trade-api/v2"
        else:
            raise ValueError("Invalid environment")

    def request_headers(self, method: str, path: str) -> Dict[str, Any]:
        """Generates the required authentication headers for API requests."""
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
        """Signs the text using RSA-PSS and returns the base64 encoded signature."""
        message = text.encode('utf-8')
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
    ):
        super().__init__(key_id, private_key, environment)
        self.host = self.HTTP_BASE_URL
        self.exchange_url = "/exchange"
        self.markets_url = "/markets"
        self.portfolio_url = "/portfolio"
        self.events_url = "/events"
        self.series_url = "/series"

    def rate_limit(self) -> None:
        """Built-in rate limiter to prevent exceeding API rate limits."""
        THRESHOLD_IN_MILLISECONDS = 100
        now = datetime.now()
        threshold_in_microseconds = 1000 * THRESHOLD_IN_MILLISECONDS
        threshold_in_seconds = THRESHOLD_IN_MILLISECONDS / 1000
        if now - self.last_api_call < timedelta(microseconds=threshold_in_microseconds):
            time.sleep(threshold_in_seconds)
        self.last_api_call = datetime.now()

    def raise_if_bad_response(self, response: requests.Response) -> None:
        """Raises an HTTPError if the response status code indicates an error."""
        if response.status_code not in range(200, 299):
            response.raise_for_status()

    def post(self, path: str, body: dict) -> Any:
        """Performs an authenticated POST request to the Kalshi API."""
        self.rate_limit()
        response = requests.post(
            self.host + path,
            json=body,
            headers=self.request_headers("POST", path)
        )
        self.raise_if_bad_response(response)
        return response.json()

    def get(self, path: str, params: Dict[str, Any] = {}) -> Any:
        """Performs an authenticated GET request to the Kalshi API."""
        self.rate_limit()
        response = requests.get(
            self.host + path,
            headers=self.request_headers("GET", path),
            params=params
        )
        self.raise_if_bad_response(response)
        return response.json()

    def delete(self, path: str, params: Dict[str, Any] = {}) -> Any:
        """Performs an authenticated DELETE request to the Kalshi API."""
        self.rate_limit()
        response = requests.delete(
            self.host + path,
            headers=self.request_headers("DELETE", path),
            params=params
        )
        self.raise_if_bad_response(response)
        return response.json()

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
                    params: Optional[Dict[str, Any]] = None) -> Any:
        """Signed request against the V2 order host (same signature scheme)."""
        self.rate_limit()
        response = requests.request(
            method,
            self.V2_BASE_URL + path,
            json=body,
            params=params,
            headers=self.request_headers(method, path),
        )
        self.raise_if_bad_response(response)
        return response.json()

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
    ) -> Dict[str, Any]:
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
            body["expiration_time"] = expiration_ts
        if reduce_only:
            body["reduce_only"] = True
        return self._v2_request("POST", "/portfolio/events/orders", body=body)

    def amend_order_v2(self, order_id: str, ticker: str, side: str, count: float,
                       price: float, client_order_id: Optional[str] = None,
                       updated_client_order_id: Optional[str] = None) -> Dict[str, Any]:
        """count = already-filled + desired remaining. Price changes forfeit
        queue priority; size decreases preserve it."""
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
        return self._v2_request("POST", f"/portfolio/events/orders/{order_id}/amend", body=body)

    def cancel_order_v2(self, order_id: str) -> Dict[str, Any]:
        return self._v2_request("DELETE", f"/portfolio/events/orders/{order_id}")

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