"""Read-only audit of a Kalshi account's trading history.

Answers "has the bot actually traded?" from the exchange's own records:
fills (executions), orders (everything ever placed), settlements (positions
held through an event's resolution), and the current balance. Places and
cancels nothing.

    python check_demo.py            # demo account (DEMO_KEYID / DEMO_KEYFILE)
    python check_demo.py --prod     # prod account (PROD_KEYID / PROD_KEYFILE)
"""
import argparse
import os
import sys
from collections import Counter, defaultdict

from cryptography.hazmat.primitives import serialization
from dotenv import load_dotenv

from src.clients import Environment, KalshiHttpClient


def make_client(env: Environment) -> KalshiHttpClient:
    prefix = "PROD" if env == Environment.PROD else "DEMO"
    key_id = os.getenv(f"{prefix}_KEYID")
    keyfile = os.getenv(f"{prefix}_KEYFILE")
    if not key_id or not keyfile:
        sys.exit(f"{prefix}_KEYID / {prefix}_KEYFILE missing from .env")
    if not os.path.exists(keyfile):
        sys.exit(f"{prefix}_KEYFILE points at {keyfile!r}, which does not exist")
    with open(keyfile, "rb") as f:
        private_key = serialization.load_pem_private_key(f.read(), password=None)
    return KalshiHttpClient(key_id=key_id, private_key=private_key, environment=env)


def fetch_all(fetch, key: str, max_pages: int = 20) -> list:
    """Follow Kalshi's cursor pagination until exhausted (capped for safety)."""
    items, cursor = [], None
    for _ in range(max_pages):
        resp = fetch(limit=100, cursor=cursor)
        items += resp.get(key, [])
        cursor = resp.get("cursor")
        if not cursor:
            break
    return items


def price_of(x: dict) -> str:
    if x.get("yes_price_dollars") is not None:
        return f"{float(x['yes_price_dollars']):.2f}"
    if x.get("yes_price") is not None:
        return f"{x['yes_price'] / 100:.2f}"
    return "?"


def section(title: str, fetch, key: str):
    """Fetch one endpoint, reporting (not crashing on) API errors."""
    print(f"\n=== {title} ===")
    try:
        items = fetch_all(fetch, key)
    except Exception as e:
        print(f"  request failed: {e}")
        return None
    print(f"  {len(items)} total")
    return items


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--prod", action="store_true", help="audit the prod account")
    args = parser.parse_args()

    load_dotenv()
    env = Environment.PROD if args.prod else Environment.DEMO
    client = make_client(env)
    print(f"Auditing {env.value.upper()} account")

    try:
        print(f"Balance: {client.get_balance()}")
    except Exception as e:
        sys.exit(f"Auth/connection check failed on get_balance: {e}")

    fills = section("FILLS (executions)", client.get_fills, "fills")
    if fills:
        by_ticker = defaultdict(list)
        for f in fills:
            by_ticker[f.get("ticker")].append(f)
        for ticker, fs in by_ticker.items():
            makers = sum(1 for f in fs if f.get("is_taker") is False)
            times = sorted(f.get("created_time", "") for f in fs)
            print(f"  {ticker}: {len(fs)} fills ({makers} as maker), "
                  f"{times[0]} -> {times[-1]}")
        print("  most recent:")
        for f in fills[:10]:
            print(f"    {f.get('created_time')}  {f.get('ticker')}  "
                  f"{f.get('action')} {f.get('side')}  "
                  f"{f.get('count_fp', f.get('count'))} @ {price_of(f)}  "
                  f"taker={f.get('is_taker')}")

    orders = section("ORDERS (all ever placed)", client.get_orders, "orders")
    if orders:
        for (ticker, status), n in Counter(
                (o.get("ticker"), o.get("status")) for o in orders).most_common():
            print(f"  {ticker}: {n} {status}")
        times = sorted(o.get("created_time", "") for o in orders)
        print(f"  first order {times[0]}, last order {times[-1]}")

    settlements = section("SETTLEMENTS (positions held to resolution)",
                          client.get_portfolio_settlements, "settlements")
    for s in settlements or []:
        print(f"  {s.get('settled_time')}  {s.get('ticker')}  "
              f"result={s.get('market_result')}  revenue={s.get('revenue')}")


if __name__ == "__main__":
    main()
