"""Shadow trading CLI: backtest recorded data, or shadow-trade live.

    python shadow.py                            # replay: backtest all configured markets
    python shadow.py --tickers KXHIGHNY-26JUL03-B98.5
    python shadow.py --follow                   # live shadow trading (tails the
                                                # DB the recorder is writing)
    python shadow.py --follow --duration 3600   # live shadow for an hour

No Kalshi account required: market data comes from the recorder database, and
fills are simulated locally (pessimistic queue model — see src/shadow.py).
"""
import argparse
import logging
import sqlite3
from collections import defaultdict

import yaml

from src.shadow import (ShadowEngine, replay_events, follow_events,
                        fetch_settle_price)

MM_PARAM_KEYS = ("gamma", "default_k", "base_order_size", "max_position",
                 "min_spread", "premium_scale", "min_time_to_resolution_h",
                 "min_quote_mid", "max_quote_mid",
                 "max_horizon_h", "gamma_mode", "calibration_constant",
                 "sigma_floor", "fair_value_adjustment", "k_refresh_every")


def load_market_rows(conn, args, config):
    """Markets to run: explicit --tickers, else config tickers/series prefixes."""
    rows = {r[0]: r for r in conn.execute(
        "SELECT ticker, close_time, fee_type, fee_multiplier FROM markets")}
    if args.tickers:
        wanted = args.tickers.split(",")
        missing = [t for t in wanted if t not in rows]
        if missing:
            raise SystemExit(f"not in recorder DB: {missing}")
        return [rows[t] for t in wanted]
    selected = []
    explicit = set(config.get("markets", {}).get("tickers") or [])
    prefixes = tuple(config.get("markets", {}).get("series_tickers") or [])
    for ticker, row in rows.items():
        if ticker in explicit or (prefixes and ticker.startswith(prefixes)):
            selected.append(row)
    return selected


def main():
    parser = argparse.ArgumentParser(description="Kalshi shadow trader / backtester")
    parser.add_argument("--config", default="shadow_config.yaml")
    parser.add_argument("--tickers", help="comma-separated market tickers (override config)")
    parser.add_argument("--follow", action="store_true",
                        help="live shadow trading: tail the recorder DB")
    parser.add_argument("--duration", type=float, default=None,
                        help="follow mode: stop after N seconds")
    args = parser.parse_args()

    with open(args.config) as f:
        config = yaml.safe_load(f)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler("shadow.log")])
    logger = logging.getLogger("shadow")

    conn = sqlite3.connect(config.get("database", "data/kalshi_recorder.db"))
    market_rows = load_market_rows(conn, args, config)
    if not market_rows:
        raise SystemExit("no markets matched; check config markets section / --tickers")

    mm_params = {k: config[k] for k in MM_PARAM_KEYS if k in config}
    fill_mode = config.get("fill_mode", "queue")

    engines = {}
    for ticker, close_time, fee_type, fee_mult in market_rows:
        engines[ticker] = ShadowEngine(
            ticker, close_time, fee_type or "quadratic", fee_mult or 1.0,
            mm_params, logger, fill_mode=fill_mode,
            fills_log_path=config.get("fills_log"))

    logger.info(f"{'FOLLOW (live shadow)' if args.follow else 'REPLAY (backtest)'} "
                f"on {len(engines)} markets, fill_mode={fill_mode}, params={mm_params}")

    if args.follow:
        try:
            for ticker, kind, ts, payload in follow_events(
                    conn, list(engines), duration=args.duration):
                engines[ticker].on_event(kind, ts, payload)
        except KeyboardInterrupt:
            logger.info("stopped by user")
        results = [eng.finalize(fetch_settle_price(t)) for t, eng in engines.items()]
    else:
        results = []
        for ticker, eng in engines.items():
            for kind, ts, payload in replay_events(conn, ticker):
                eng.on_event(kind, ts, payload)
            results.append(eng.finalize(fetch_settle_price(ticker)))

    results.sort(key=lambda r: r["net_pnl"], reverse=True)
    logger.info("=" * 100)
    logger.info(f"{'ticker':44s} {'fills':>5s} {'pos':>5s} {'realized':>9s} "
                f"{'total':>8s} {'fees':>6s} {'net':>8s} {'mo60':>7s} {'settled':>7s}")
    agg = defaultdict(float)
    for r in results:
        mo = r.get("avg_markout_60s")
        logger.info(f"{r['ticker']:44s} {r['fills']:5d} {r['position']:5g} "
                    f"{r['realized_pnl']:9.2f} {r['total_pnl']:8.2f} "
                    f"{r['fees']:6.2f} {r['net_pnl']:8.2f} "
                    f"{mo if mo is not None else '-':>7} {str(r['settled']):>7s}")
        for key in ("fills", "realized_pnl", "total_pnl", "fees", "net_pnl"):
            agg[key] += r[key]
    logger.info("-" * 100)
    logger.info(f"{'TOTAL':44s} {int(agg['fills']):5d} {'':5s} "
                f"{agg['realized_pnl']:9.2f} {agg['total_pnl']:8.2f} "
                f"{agg['fees']:6.2f} {agg['net_pnl']:8.2f}")
    logger.info("Reminder: shadow fills are simulated against a tape our quotes did "
                "not influence; trust the sign only after weeks of data.")


if __name__ == "__main__":
    main()
