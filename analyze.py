"""Phase 2 research CLI: market screener + maker toxicity tables.

    python analyze.py                          # backfill DB, all series
    python analyze.py --series KXHIGH          # weather only (prefix match)
    python analyze.py --atm-only               # restrict to 0.20-0.80 prices
    python analyze.py --db data/kalshi_recorder.db
"""
import argparse
import sqlite3

from src.analysis import toxicity_tables, screener


def print_table(title: str, rows: dict, key_header: str, sort_keys=None):
    print(f"\n== {title} ==")
    print(f"{key_header:>16s} {'trades':>8s} {'contracts':>10s} "
          f"{'maker_pnl/ct':>13s} {'vw_pnl/ct':>10s} {'win%':>6s}")
    keys = sort_keys if sort_keys is not None else sorted(rows)
    for k in keys:
        r = rows.get(k if not isinstance(k, str) else k)
        if not r:
            continue
        print(f"{str(k):>16s} {r['trades']:8d} {r['contracts']:10d} "
              f"{r['maker_pnl_mean']:+13.4f} {r['maker_pnl_vw']:+10.4f} "
              f"{100*r['win_rate']:5.1f}%")


def main():
    parser = argparse.ArgumentParser(description="Kalshi tape research")
    parser.add_argument("--db", default="data/kalshi_backfill.db")
    parser.add_argument("--series", default=None,
                        help="ticker prefix filter, e.g. KXHIGH or KXHIGHNY")
    parser.add_argument("--atm-only", action="store_true",
                        help="only trades priced 0.20-0.80")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)

    print("=" * 78)
    print("SERIES SCREENER (all recorded/backfilled markets)")
    print("=" * 78)
    print(f"{'series':>16s} {'markets':>8s} {'settled':>8s} {'trades':>9s} "
          f"{'contracts':>10s} {'tr/mkt':>7s} {'avg_sz':>7s}  fee_type")
    for r in screener(conn):
        print(f"{r['series']:>16s} {r['markets']:8d} {r['settled']:8d} "
              f"{r['trades']:9d} {r['contracts']:10d} {r['trades_per_market']:7.1f} "
              f"{r['avg_trade_size']:7.1f}  {r['fee_type']}")

    scope = f"series prefix {args.series}" if args.series else "all series"
    band = " | ATM only" if args.atm_only else ""
    print()
    print("=" * 78)
    print(f"MAKER SETTLEMENT-P&L TOXICITY ({scope}{band})")
    print("maker_pnl/ct: what the passive side of each print made per contract")
    print("at settlement. Negative = makers were picked off.")
    print("=" * 78)
    tables = toxicity_tables(conn, args.series, atm_only=args.atm_only)
    print_table("by hours to market close", tables["hours_to_close"], "hrs_to_close",
                sort_keys=["0-1h", "1-2h", "2-4h", "4-8h", "8-16h", "16-24h",
                           "24-48h", ">48h"])
    print_table("by hour of day (ET)", tables["et_hour"], "et_hour")
    print_table("by trade price band", tables["price_band"], "band",
                sort_keys=["OTM (0.01-0.20)", "ATM (0.20-0.80)", "ITM (0.80-0.99)"])
    print_table("by series", tables["series"], "series")


if __name__ == "__main__":
    main()
