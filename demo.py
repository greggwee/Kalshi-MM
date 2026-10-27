"""Kalshi demo trading harness: preflight -> select -> run -> report.

    python demo.py preflight                  # reachability, auth, clock skew, rate tier
    python demo.py preflight --order-test     # + create/amend/cancel one 1-lot far from market
    python demo.py select                     # choose liquid + illiquid markets, print table
    python demo.py run --dry-run --duration 600   # live books, simulated orders
    python demo.py run --duration 3600        # live demo trading, then the report
    python demo.py report runs/<session_dir>  # (re)build the diagnostic report

Credentials come from the environment / .env (see src/credentials.py):
DEMO_KEYID plus DEMO_PRIVATE_KEY (PEM text) or DEMO_KEYFILE (path).
Production requires --env prod AND --allow-prod.
"""
import argparse
import json
import logging
import os
import signal
import sys
import time
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests
import yaml

from src.clients import Environment
from src.credentials import CredentialsError, load_credentials, make_client, \
    make_public_client, mask_key_id, parse_environment
from src.diagnostics import write_report
from src.gateway import DryRunGateway, ExchangeGateway
from src.journal import Journal
from src.orderbook import OrderBook
from src.selector import format_selection, select_markets, session_specs
from src.session import TradingSession

RUNS_DIR = "runs"


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def setup_logging(log_path: str = None) -> logging.Logger:
    handlers = [logging.StreamHandler()]
    if log_path:
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, handlers=handlers, force=True,
                        format="%(asctime)s %(levelname)s %(message)s")
    return logging.getLogger("demo")


def resolve_env(args, config) -> Environment:
    env = parse_environment(args.env or config.get("environment", "demo"))
    if env == Environment.PROD and not getattr(args, "allow_prod", False):
        sys.exit("refusing to run against PRODUCTION without --allow-prod")
    return env


def build_client(env: Environment, logger: logging.Logger, require_auth: bool = True):
    """Signed client from credentials; without credentials, an unsigned
    public-data client when the command does not need to trade."""
    try:
        client = make_client(env)
    except CredentialsError as e:
        if require_auth:
            sys.exit(f"credentials: {e}\n  (`select` and `run --dry-run` work without keys)")
        logger.warning(f"no credentials ({e}); using public market data only")
        return make_public_client(env)
    calibrate_rate_limits(client, logger)
    return client


def calibrate_rate_limits(client, logger) -> dict:
    """Size the client-side buckets from GET /account/limits (falls back to
    Basic-tier defaults if the endpoint is unavailable)."""
    try:
        limits = client.get_account_limits()
    except Exception as e:
        logger.warning(f"account limits unavailable ({e}); using Basic-tier pacing")
        return {}
    buckets = {}
    for name in ("read", "write"):
        b = limits.get(name) or {}
        rate, cap = b.get("refill_rate"), b.get("bucket_capacity")
        if rate and cap:
            buckets[name] = (float(rate), float(cap))
    if buckets:
        client.limiter.configure(buckets)
    logger.info(f"API usage tier {limits.get('usage_tier')}: {buckets or 'defaults'}")
    return limits


def network_hint(e: Exception) -> str:
    return (f"{type(e).__name__}: {e}\n  -> cannot reach the Kalshi API host. If this runs "
            f"in a sandbox, allow external-api.demo.kalshi.co in its network policy.")


# --------------------------------------------------------------------------
# preflight
# --------------------------------------------------------------------------

def cmd_preflight(args, config):
    logger = setup_logging()
    env = resolve_env(args, config)
    results = []

    def step(name, fn):
        t0 = time.perf_counter()
        try:
            detail = fn()
            ok = True
        except (requests.ConnectionError, requests.Timeout) as e:
            detail, ok = network_hint(e), False
        except Exception as e:
            detail, ok = f"{type(e).__name__}: {e}", False
        ms = (time.perf_counter() - t0) * 1000
        results.append({"step": name, "ok": ok, "ms": round(ms, 1), "detail": detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {name:28s} {ms:7.0f} ms  {detail}")
        return ok, detail

    def skip(name, why):
        results.append({"step": name, "ok": None, "ms": 0.0, "detail": why})
        print(f"[SKIP] {name:28s} {'':7s}    {why}")

    try:
        key_id, key = load_credentials(env)
        print(f"[PASS] credentials                  key {mask_key_id(key_id)} "
              f"({type(key).__name__}), environment {env.value}")
        client = make_client(env)
    except CredentialsError as e:
        results.append({"step": "credentials", "ok": False, "ms": 0.0, "detail": str(e)})
        print(f"[FAIL] credentials                  {e}\n"
              f"       continuing with public market-data checks only")
        client = make_public_client(env)
    gw = ExchangeGateway(client)
    print(f"       host {client.host}")

    ok, _ = step("exchange status", lambda: client.get_exchange_status())
    if not ok:
        return _save_preflight(results)

    def skew():
        if not client.last_server_date:
            return "no Date header"
        server = parsedate_to_datetime(client.last_server_date).timestamp()
        delta = time.time() - server
        if abs(delta) > 5:
            raise RuntimeError(f"local clock off by {delta:+.1f}s (signatures may be rejected)")
        return f"{delta:+.2f}s"
    step("clock skew vs server", skew)
    private = ["auth: balance", "rate limit tier", "resting orders", "positions"]
    if client.authenticated:
        step("auth: balance", lambda: gw.balance())
        step("rate limit tier", lambda: {k: v for k, v in calibrate_rate_limits(client, logger)
                                         .items() if k != "grants"})
    sample = {}

    def markets():
        ms = gw.list_open_markets(max_pages=1)
        two = [m for m in ms if m["yes_bid"] and m["yes_ask"] and 0 < m["yes_bid"] < m["yes_ask"] < 1]
        two.sort(key=lambda m: -(m["volume_24h"] or 0))
        if two:
            sample.update(two[0])
        return f"{len(ms)} open markets on first page, {len(two)} two-sided"
    step("market data", markets)
    if sample:
        step("orderbooks (batched)", lambda: {t: len(b.get("yes_dollars") or []) +
                                              len(b.get("no_dollars") or [])
                                              for t, b in gw.orderbooks([sample["ticker"]]).items()})
    if not client.authenticated:
        for name in private + (["order test"] if args.order_test else []):
            skip(name, "needs DEMO_KEYID + DEMO_PRIVATE_KEY")
        return _save_preflight(results)
    step("resting orders", lambda: f"{len(client.get_all_orders(status='resting'))} resting")
    step("positions", lambda: f"{len(client.get_all_positions(count_filter='position'))} open")

    if args.order_test:
        ticker = args.ticker or sample.get("ticker")
        if not ticker:
            print("[SKIP] order test: no two-sided market found")
        else:
            _order_test(gw, ticker, step)
    return _save_preflight(results)


def _order_test(gw, ticker, step):
    """1 contract, post-only, priced at $0.01 so it rests far behind the
    market; expires in 2 minutes even if every later step fails."""
    book = OrderBook(ticker)
    book.apply_snapshot(gw.orderbooks([ticker]).get(ticker) or {})
    if book.best_yes_bid is not None and book.best_yes_bid <= 0.02:
        print(f"[SKIP] order test: best bid {book.best_yes_bid} too close to $0.01")
        return
    state = {}

    def create():
        resp = gw.create_order(ticker=ticker, side="bid", count=1, price=0.01,
                               expiration_ts=int(time.time()) + 120, post_only=True,
                               client_order_id=str(uuid.uuid4()))
        state["id"] = resp["order_id"]
        return resp
    ok, _ = step(f"order create {ticker[:18]}", create)
    if not ok:
        return
    step("order visible as resting", lambda: next(
        o for o in gw.resting_orders([ticker])[ticker] if o["order_id"] == state["id"]))
    step("order amend (expiry only)", lambda: gw.amend_order(
        state["id"], ticker=ticker, side="bid", count=1, price=0.01,
        expiration_ts=int(time.time()) + 180))
    step("order cancel", lambda: gw.cancel_order(state["id"], ticker))

    def gone():
        if any(o["order_id"] == state["id"] for o in gw.resting_orders([ticker])[ticker]):
            raise RuntimeError("order still resting after cancel")
        return "not resting"
    step("cancel confirmed", gone)


def _save_preflight(results):
    os.makedirs(RUNS_DIR, exist_ok=True)
    path = os.path.join(RUNS_DIR, f"preflight_{datetime.now():%Y%m%d-%H%M%S}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)
    failed = [r["step"] for r in results if r["ok"] is False]
    skipped = [r["step"] for r in results if r["ok"] is None]
    status = ("FAILED: " + ", ".join(failed) if failed else
              "passed (public checks only; trading not verified)" if skipped else "passed")
    print(f"\npreflight {status} (saved {path})")
    return 1 if failed else 0


# --------------------------------------------------------------------------
# select / run / report
# --------------------------------------------------------------------------

def cmd_select(args, config):
    logger = setup_logging()
    env = resolve_env(args, config)
    gw = ExchangeGateway(build_client(env, logger, require_auth=False))
    try:
        selection = select_markets(gw, config.get("selection"), logger)
    except (requests.ConnectionError, requests.Timeout) as e:
        sys.exit(network_hint(e))
    print(format_selection(selection))
    os.makedirs(RUNS_DIR, exist_ok=True)
    path = args.out or os.path.join(RUNS_DIR, f"selection_{datetime.now():%Y%m%d-%H%M%S}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(selection, f, indent=2, default=str)
    print(f"\nsaved {path}  (pass to `run --selection {path}` to trade exactly these)")
    return 0


def cmd_run(args, config):
    env = resolve_env(args, config)
    config["environment"] = env.value
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_dir = os.path.join(RUNS_DIR, f"{stamp}_{env.value}{'_dry' if args.dry_run else ''}")
    os.makedirs(run_dir, exist_ok=True)
    logger = setup_logging(os.path.join(run_dir, "session.log"))
    client = build_client(env, logger, require_auth=not args.dry_run)
    base_gw = ExchangeGateway(client)

    try:
        if args.selection:
            with open(args.selection, encoding="utf-8") as f:
                selection = json.load(f)
        else:
            selection = select_markets(base_gw, config.get("selection"), logger)
    except (requests.ConnectionError, requests.Timeout) as e:
        sys.exit(network_hint(e))
    specs = session_specs(selection)
    if not specs:
        sys.exit("no markets selected; loosen the selection criteria")
    logger.info("selected markets:\n" + format_selection(selection))
    with open(os.path.join(run_dir, "selection.json"), "w", encoding="utf-8") as f:
        json.dump(selection, f, indent=2, default=str)
    with open(os.path.join(run_dir, "config.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, sort_keys=False)

    journal = Journal(os.path.join(run_dir, "events.jsonl"))
    client.on_error = lambda info: journal.write("api_error", **info)
    gw = DryRunGateway(base_gw) if args.dry_run else base_gw
    session = TradingSession(gw, specs, config, journal, logger, dry_run=args.dry_run)

    def on_signal(signum, _frame):
        logger.info(f"signal {signum}: stopping after this cycle")
        session.request_stop(f"signal_{signum}")
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    duration = args.duration if args.duration is not None else config.get("duration_s")
    logger.info(f"session {run_dir}: {len(specs)} markets, duration {duration}s, "
                f"{'DRY RUN' if args.dry_run else 'LIVE ORDERS'} on {env.value}")
    try:
        session.run(duration)
    finally:
        journal.close()
    report = write_report(run_dir)
    print(f"\nreport: {os.path.join(run_dir, 'report.md')}")
    for f in report["findings"][:10]:
        print(f"  {f['severity'].upper():8s} {f['area']}: {f['text']}")
    return 0


def cmd_report(args, config):
    write_report(args.run_dir)
    with open(os.path.join(args.run_dir, "report.md"), encoding="utf-8") as f:
        print(f.read())
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Kalshi demo market-making harness")
    parser.add_argument("--config", default="session_config.yaml")
    parser.add_argument("--env", choices=["demo", "prod"], default=None)
    parser.add_argument("--allow-prod", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("preflight")
    p.add_argument("--order-test", action="store_true",
                   help="create/amend/cancel one post-only 1-lot at $0.01")
    p.add_argument("--ticker", help="market for the order test (default: auto)")
    p = sub.add_parser("select")
    p.add_argument("--out")
    p = sub.add_parser("run")
    p.add_argument("--duration", type=float, default=None, help="seconds")
    p.add_argument("--selection", help="selection JSON from `select`")
    p.add_argument("--dry-run", action="store_true",
                   help="live market data, orders simulated locally")
    p = sub.add_parser("report")
    p.add_argument("run_dir")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # Windows consoles: never crash on a title
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    config = load_config(args.config) if os.path.exists(args.config) else {}
    return {"preflight": cmd_preflight, "select": cmd_select, "run": cmd_run,
            "report": cmd_report}[args.cmd](args, config)


if __name__ == "__main__":
    sys.exit(main())
