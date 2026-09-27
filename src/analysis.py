"""Phase 2 research: market screening and maker-toxicity measurement from the
public trade tape + settlement outcomes.

Core idea: every tape print has a maker on the passive side. With the
settlement outcome known, each trade tells us what the MAKER eventually made
or lost per contract on that fill:

    taker bought YES at p  ->  maker was short YES:  maker pnl = p - settle
    taker bought NO  at p  ->  maker was long  YES:  maker pnl = settle - p

Aggregating maker pnl by how far the trade was from market close (and by time
of day, price band, series) maps exactly WHEN passive quoting was profitable
vs. picked off — without needing any order book history.
"""
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from typing import Iterable, Optional

ET_UTC_OFFSET_H = -4  # EDT; the backfill window (Apr-Jul) is entirely daylight time

HOURS_TO_CLOSE_BUCKETS = [
    (0.0, 1.0, "0-1h"), (1.0, 2.0, "1-2h"), (2.0, 4.0, "2-4h"),
    (4.0, 8.0, "4-8h"), (8.0, 16.0, "8-16h"), (16.0, 24.0, "16-24h"),
    (24.0, 48.0, "24-48h"), (48.0, float("inf"), ">48h"),
]

PRICE_BANDS = [
    (0.01, 0.20, "OTM (0.01-0.20)"),
    (0.20, 0.80, "ATM (0.20-0.80)"),
    (0.80, 0.99, "ITM (0.80-0.99)"),
]


def maker_pnl_per_contract(taker_side: Optional[str], yes_price: Optional[float],
                           settle: float) -> Optional[float]:
    """Settlement P&L per contract for the passive side of one print."""
    if yes_price is None:
        return None
    if taker_side == "yes":      # maker sold YES
        return yes_price - settle
    if taker_side == "no":       # maker bought YES (sold NO)
        return settle - yes_price
    return None                  # unknown aggressor: cannot sign


def bucket_label(value: float, buckets) -> Optional[str]:
    for lo, hi, label in buckets:
        if lo <= value < hi:
            return label
    return None


class Agg:
    """Running mean / volume-weighted mean / win rate."""
    __slots__ = ("n", "vol", "pnl_sum", "pnl_vol_sum", "wins")

    def __init__(self):
        self.n = 0
        self.vol = 0.0
        self.pnl_sum = 0.0
        self.pnl_vol_sum = 0.0
        self.wins = 0

    def add(self, pnl: float, count: float):
        self.n += 1
        self.vol += count
        self.pnl_sum += pnl
        self.pnl_vol_sum += pnl * count
        self.wins += pnl > 0

    def row(self):
        if self.n == 0:
            return None
        return {
            "trades": self.n,
            "contracts": round(self.vol),
            "maker_pnl_mean": self.pnl_sum / self.n,
            "maker_pnl_vw": self.pnl_vol_sum / self.vol if self.vol else 0.0,
            "win_rate": self.wins / self.n,
        }


def iter_settled_trades(conn: sqlite3.Connection, series_prefix: Optional[str] = None
                        ) -> Iterable[tuple]:
    """Yields (series_ticker, ts_ms, yes_price, count, taker_side,
    close_time_iso, settle) for trades in settled markets."""
    cond = "WHERE m.result IN ('yes','no') AND m.close_time IS NOT NULL"
    params = []
    if series_prefix:
        cond += " AND m.ticker LIKE ?"
        params.append(series_prefix + "%")
    cursor = conn.execute(f"""
        SELECT COALESCE(m.series_ticker, substr(m.ticker, 1, instr(m.ticker,'-')-1)),
               t.ts_ms, t.yes_price, t.count, t.taker_side, m.close_time, m.result
        FROM trades t JOIN markets m ON m.ticker = t.market_ticker {cond}""", params)
    while True:
        rows = cursor.fetchmany(50_000)
        if not rows:
            return
        for series, ts_ms, yp, cnt, taker, close_iso, result in rows:
            yield (series, ts_ms, yp, cnt or 0.0, taker, close_iso,
                   1.0 if result == "yes" else 0.0)


def toxicity_tables(conn: sqlite3.Connection, series_prefix: Optional[str] = None,
                    atm_only: bool = False) -> dict:
    """Maker settlement-P&L aggregates keyed three ways: hours-to-close,
    ET hour-of-day, price band. Returns {dimension: {label: Agg.row()}}."""
    by_htc = defaultdict(Agg)
    by_hour = defaultdict(Agg)
    by_band = defaultdict(Agg)
    by_series = defaultdict(Agg)
    close_cache: dict = {}
    for series, ts_ms, yp, cnt, taker, close_iso, settle in iter_settled_trades(
            conn, series_prefix):
        pnl = maker_pnl_per_contract(taker, yp, settle)
        if pnl is None:
            continue
        band = bucket_label(yp, PRICE_BANDS)
        if atm_only and band != "ATM (0.20-0.80)":
            continue
        if close_iso not in close_cache:
            close_cache[close_iso] = datetime.fromisoformat(
                close_iso.replace("Z", "+00:00")).timestamp()
        htc = (close_cache[close_iso] - ts_ms / 1000.0) / 3600.0
        if htc < 0:
            continue  # trade after close: settlement prints, not quotable flow
        label = bucket_label(htc, HOURS_TO_CLOSE_BUCKETS)
        if label:
            by_htc[label].add(pnl, cnt)
        et_hour = int(datetime.fromtimestamp(
            ts_ms / 1000.0, tz=timezone.utc).hour + ET_UTC_OFFSET_H) % 24
        by_hour[et_hour].add(pnl, cnt)
        if band:
            by_band[band].add(pnl, cnt)
        by_series[series].add(pnl, cnt)
    finish = lambda d: {k: v.row() for k, v in d.items() if v.row()}
    return {"hours_to_close": finish(by_htc), "et_hour": finish(by_hour),
            "price_band": finish(by_band), "series": finish(by_series)}


def screener(conn: sqlite3.Connection) -> list:
    """Per-series flow statistics for market selection."""
    rows = conn.execute("""
        SELECT COALESCE(m.series_ticker, substr(m.ticker, 1, instr(m.ticker,'-')-1)) s,
               COUNT(DISTINCT m.ticker) markets,
               SUM(CASE WHEN m.result IN ('yes','no') THEN 1 ELSE 0 END) settled,
               m.fee_type
        FROM markets m GROUP BY 1""").fetchall()
    out = []
    for series, n_markets, n_settled, fee_type in rows:
        stats = conn.execute("""
            SELECT COUNT(*), COALESCE(SUM(t.count),0), COALESCE(AVG(t.count),0)
            FROM trades t JOIN markets m ON m.ticker = t.market_ticker
            WHERE COALESCE(m.series_ticker,
                           substr(m.ticker, 1, instr(m.ticker,'-')-1)) = ?""",
            (series,)).fetchone()
        n_trades, volume, avg_size = stats
        out.append({
            "series": series, "markets": n_markets, "settled": n_settled or 0,
            "trades": n_trades, "contracts": round(volume),
            "trades_per_market": round(n_trades / n_markets, 1) if n_markets else 0,
            "avg_trade_size": round(avg_size, 1),
            "fee_type": fee_type or "?",
        })
    out.sort(key=lambda r: -r["trades"])
    return out
