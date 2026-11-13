"""Download station observations for the low-temperature markets.

    python fetch_obs.py --since 2026-04-28 [--db data/obs.db] [--series KXLOWTBOS ...]

Public Iowa Environmental Mesonet archive; no key. One request per station
per month, a few seconds apart.
"""
import argparse
import time
from datetime import date, datetime, timedelta

import requests

from src import obs


def month_chunks(start, end):
    cur = start
    while cur < end:
        nxt = (cur.replace(day=1) + timedelta(days=32)).replace(day=1)
        yield cur, min(nxt, end)
        cur = nxt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/obs.db")
    ap.add_argument("--since", default="2026-04-28")
    ap.add_argument("--until", default=None, help="exclusive; default tomorrow")
    ap.add_argument("--series", nargs="*", default=sorted(obs.STATIONS))
    ap.add_argument("--gap", type=float, default=2.0, help="seconds between requests")
    args = ap.parse_args()
    start = datetime.strptime(args.since, "%Y-%m-%d").date()
    end = datetime.strptime(args.until, "%Y-%m-%d").date() if args.until \
        else date.today() + timedelta(days=2)
    conn = obs.connect(args.db)
    session = requests.Session()
    session.headers["User-Agent"] = "weather-research-script"
    for series in args.series:
        station = obs.STATIONS[series][0]
        total = 0
        for a, b in month_chunks(start, end):
            for attempt in range(4):
                try:
                    rows = obs.fetch_station(session, station, a, b)
                    break
                except requests.RequestException as e:
                    print(f"  {station} {a}: {e}; retrying", flush=True)
                    time.sleep(10 * (attempt + 1))
            else:
                print(f"  {station} {a}: gave up", flush=True)
                continue
            obs.store(conn, rows)
            total += len(rows)
            time.sleep(args.gap)
        print(f"{series} {station}: {total:,} reports", flush=True)


if __name__ == "__main__":
    main()
