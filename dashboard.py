"""Kalshi MM research dashboard.

    streamlit run dashboard.py

Tabs:
  Data status      - what the recorder/backfill have collected, freshness
  Toxicity         - maker settlement-P&L tables from the historical tape
  Market explorer  - price/volume/book history for any recorded market
  Shadow backtest  - run the strategy on recorded data, P&L + equity + markouts
  Perf logs        - inspect *_perf.jsonl / shadow fill logs
"""
import glob
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from src.analysis import screener, toxicity_tables

RECORDER_DB = "data/kalshi_recorder.db"
BACKFILL_DB = "data/kalshi_backfill.db"

st.set_page_config(page_title="Kalshi MM", layout="wide")
logger = logging.getLogger("dashboard")


def db_exists(path: str) -> bool:
    return os.path.exists(path)


def q(path: str, sql: str, params=()) -> pd.DataFrame:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        return pd.read_sql_query(sql, conn, params=params)


# ---------------------------------------------------------------- data status
def tab_status():
    for name, path in (("Live recorder", RECORDER_DB), ("Backfill", BACKFILL_DB)):
        st.subheader(name)
        if not db_exists(path):
            st.info(f"{path} not found yet")
            continue
        counts = q(path, """
            SELECT (SELECT COUNT(*) FROM markets) markets,
                   (SELECT COUNT(*) FROM book_snapshots) snapshots,
                   (SELECT COUNT(*) FROM trades) trades""")
        newest = q(path, "SELECT MAX(ts_ms) m FROM trades")["m"][0]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Markets", int(counts["markets"][0]))
        c2.metric("Book snapshots", int(counts["snapshots"][0]))
        c3.metric("Trades", int(counts["trades"][0]))
        if newest:
            age_min = (datetime.now(timezone.utc).timestamp() - newest / 1000) / 60
            c4.metric("Newest trade", f"{age_min:.0f} min ago")
        daily = q(path, """
            SELECT date(ts_ms/1000,'unixepoch') day, COUNT(*) trades
            FROM trades GROUP BY 1 ORDER BY 1""")
        if len(daily) > 1:
            st.bar_chart(daily.set_index("day")["trades"], height=180)


# ------------------------------------------------------------------- toxicity
@st.cache_data(ttl=600)
def cached_toxicity(path: str, prefix, atm_only, _mtime):
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        return toxicity_tables(conn, prefix or None, atm_only=atm_only), screener(conn)


def tab_toxicity():
    if not db_exists(BACKFILL_DB):
        st.info("run backfill.py first")
        return
    c1, c2 = st.columns([2, 1])
    prefix = c1.text_input("Series/ticker prefix filter", "KXHIGH")
    atm_only = c2.checkbox("ATM trades only (0.20-0.80)", True)
    tables, screen = cached_toxicity(BACKFILL_DB, prefix, atm_only,
                                     os.path.getmtime(BACKFILL_DB) // 600)
    st.caption("maker_pnl = settlement P&L per contract for the PASSIVE side of "
               "each print. Negative = makers picked off. vw = volume-weighted "
               "(whales); a big gap between mean and vw means large trades are "
               "the informed ones.")

    order = ["0-1h", "1-2h", "2-4h", "4-8h", "8-16h", "16-24h", "24-48h", ">48h"]
    htc = tables["hours_to_close"]
    if htc:
        df = pd.DataFrame([{**{"bucket": k}, **v} for k, v in htc.items()])
        df["bucket"] = pd.Categorical(df["bucket"], order)
        df = df.sort_values("bucket")
        st.subheader("Maker P&L by hours to market close")
        st.bar_chart(df.set_index("bucket")[["maker_pnl_mean", "maker_pnl_vw"]],
                     height=260)
        st.dataframe(df, hide_index=True)

    hod = tables["et_hour"]
    if hod:
        df = pd.DataFrame([{**{"et_hour": k}, **v} for k, v in hod.items()]
                          ).sort_values("et_hour")
        st.subheader("Maker P&L by hour of day (ET)")
        st.bar_chart(df.set_index("et_hour")[["maker_pnl_mean", "maker_pnl_vw"]],
                     height=260)

    c1, c2 = st.columns(2)
    if tables["price_band"]:
        c1.subheader("By price band")
        c1.dataframe(pd.DataFrame([{**{"band": k}, **v}
                                   for k, v in tables["price_band"].items()]),
                     hide_index=True)
    if tables["series"]:
        c2.subheader("By series")
        c2.dataframe(pd.DataFrame([{**{"series": k}, **v}
                                   for k, v in tables["series"].items()]),
                     hide_index=True)

    st.subheader("Series screener")
    st.dataframe(pd.DataFrame(screen), hide_index=True)


# ------------------------------------------------------------- market explorer
def best_levels(levels_json: str):
    levels = json.loads(levels_json)
    return max((p for p, _ in levels), default=None)


def tab_explorer():
    path = st.radio("Database", [RECORDER_DB, BACKFILL_DB], horizontal=True,
                    format_func=lambda p: "live recorder" if p == RECORDER_DB else "backfill")
    if not db_exists(path):
        st.info(f"{path} not found yet")
        return
    markets = q(path, """
        SELECT t.market_ticker, COUNT(*) n FROM trades t
        GROUP BY 1 ORDER BY n DESC LIMIT 300""")
    if markets.empty:
        st.info("no trades recorded yet")
        return
    ticker = st.selectbox("Market", markets["market_ticker"],
                          format_func=lambda t: f"{t}  "
                          f"({int(markets.set_index('market_ticker')['n'][t])} trades)")
    meta = q(path, "SELECT * FROM markets WHERE ticker = ?", (ticker,))
    if not meta.empty:
        m = meta.iloc[0].to_dict()  # older DBs may lack status/result columns
        st.caption(f"{m.get('title')} | close {m.get('close_time')} | status "
                   f"{m.get('status') or '-'} | result {m.get('result') or '-'} | "
                   f"fee {m.get('fee_type')}")

    trades = q(path, """
        SELECT ts_ms, yes_price, count, taker_side FROM trades
        WHERE market_ticker = ? ORDER BY ts_ms""", (ticker,))
    trades["time"] = pd.to_datetime(trades["ts_ms"], unit="ms")
    st.subheader("Trade prices")
    st.line_chart(trades.set_index("time")["yes_price"], height=260)
    st.subheader("Hourly volume (contracts)")
    vol = trades.set_index("time")["count"].resample("1h").sum()
    st.bar_chart(vol, height=180)

    snaps = q(path, """
        SELECT ts_ms, yes_levels, no_levels FROM book_snapshots
        WHERE market_ticker = ? ORDER BY ts_ms""", (ticker,))
    if not snaps.empty:
        snaps["time"] = pd.to_datetime(snaps["ts_ms"], unit="ms")
        snaps["bid"] = snaps["yes_levels"].map(best_levels)
        best_no = snaps["no_levels"].map(best_levels)
        snaps["ask"] = best_no.map(lambda p: None if p is None else round(1 - p, 4))
        st.subheader("Recorded book: best bid/ask")
        st.line_chart(snaps.set_index("time")[["bid", "ask"]], height=260)


# ------------------------------------------------------------- shadow backtest
@st.cache_data(ttl=600, show_spinner="replaying recorded data...")
def run_shadow(db_path: str, tickers: tuple, params: tuple, fill_mode: str, _mtime):
    from src.shadow import ShadowEngine, replay_events, fetch_settle_price
    mm_params = dict(params)
    results, all_events = [], []
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    for ticker in tickers:
        row = conn.execute("SELECT close_time, fee_type, fee_multiplier FROM markets"
                           " WHERE ticker = ?", (ticker,)).fetchone()
        close_time, fee_type, fee_mult = row if row else (None, "quadratic", 1.0)
        events = []
        eng = ShadowEngine(ticker, close_time, fee_type or "quadratic",
                           fee_mult or 1.0, mm_params, logger,
                           fill_mode=fill_mode, event_sink=events.append)
        for kind, ts, payload in replay_events(conn, ticker):
            eng.on_event(kind, ts, payload)
        results.append(eng.finalize(fetch_settle_price(ticker)))
        for e in events:
            e["ticker"] = ticker
        all_events.extend(events)
    conn.close()
    return results, all_events


def tab_shadow():
    if not db_exists(RECORDER_DB):
        st.info("recorder DB not found")
        return
    with st.form("shadow"):
        c = st.columns(6)
        gamma = c[0].number_input("gamma", 0.1, 10.0, 1.0, 0.1)
        min_spread = c[1].number_input("min_spread", 0.0, 0.2, 0.02, 0.01)
        size = c[2].number_input("order size", 1, 100, 5)
        max_pos = c[3].number_input("max position", 1, 500, 20)
        horizon = c[4].number_input("max_horizon_h", 1.0, 168.0, 24.0, 1.0)
        fill_mode = c[5].selectbox("fill model", ["queue", "strict"])
        candidates = q(RECORDER_DB, """
            SELECT market_ticker, COUNT(*) n FROM book_snapshots
            GROUP BY 1 ORDER BY n DESC LIMIT 60""")
        tickers = st.multiselect("Markets (ranked by recorded book activity)",
                                 candidates["market_ticker"],
                                 default=list(candidates["market_ticker"][:8]))
        go = st.form_submit_button("Run backtest")
    if not (go or st.session_state.get("shadow_ran")):
        return
    st.session_state["shadow_ran"] = True
    params = (("gamma", gamma), ("min_spread", min_spread),
              ("base_order_size", int(size)), ("max_position", int(max_pos)),
              ("max_horizon_h", horizon))
    results, events = run_shadow(RECORDER_DB, tuple(tickers), params, fill_mode,
                                 os.path.getmtime(RECORDER_DB) // 600)

    df = pd.DataFrame(results)
    total = df[["realized_pnl", "total_pnl", "fees", "net_pnl", "fills"]].sum()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Net P&L", f"${total['net_pnl']:.2f}")
    c2.metric("Fills", int(total["fills"]))
    c3.metric("Fees", f"${total['fees']:.2f}")
    mo = df["avg_markout_60s"].dropna()
    c4.metric("Avg 60s markout", f"{mo.mean():+.4f}" if len(mo) else "-",
              help="negative = adverse selection")

    fills = pd.DataFrame([e for e in events if e.get("event") == "fill"])
    if not fills.empty:
        fills["time"] = pd.to_datetime(fills["ts"], unit="s")
        fills = fills.sort_values("time")
        fills["cum_realized"] = (fills.groupby("ticker")["realized_after"]
                                 .transform(lambda s: s)).astype(float)
        equity = (fills.set_index("time").groupby("ticker")["realized_after"]
                  .last())  # per-market final
        curve = fills.set_index("time")[["realized_after", "ticker"]]
        pivot = curve.pivot_table(index="time", columns="ticker",
                                  values="realized_after", aggfunc="last").ffill()
        st.subheader("Realized P&L over time (per market)")
        st.line_chart(pivot.fillna(0.0).sum(axis=1), height=240)
        st.subheader("Fills")
        st.dataframe(fills[["time", "ticker", "signed_qty", "price",
                            "mid_at_fill", "position_after", "realized_after"]],
                     hide_index=True, height=240)
    marks = pd.DataFrame([e for e in events if e.get("event") == "markout"
                          and e.get("horizon") == 60])
    if not marks.empty:
        st.subheader("60s markout per fill (negative = picked off)")
        marks["time"] = pd.to_datetime(marks["ts"], unit="s")
        st.scatter_chart(marks.set_index("time")["markout"], height=200)

    st.subheader("Per-market results")
    st.dataframe(df, hide_index=True)


# ------------------------------------------------------------------- perf logs
def tab_logs():
    files = sorted(glob.glob("*_perf.jsonl") + glob.glob("shadow_fills.jsonl"))
    if not files:
        st.info("no *_perf.jsonl files yet (created by live/demo runs and "
                "shadow sessions with fills_log set)")
        return
    fname = st.selectbox("Log file", files)
    rows = [json.loads(line) for line in open(fname) if line.strip()]
    fills = pd.DataFrame([r for r in rows if r.get("event") == "fill"])
    marks = pd.DataFrame([r for r in rows if r.get("event") == "markout"])
    c1, c2 = st.columns(2)
    c1.metric("Fills", len(fills))
    c2.metric("Markouts", len(marks))
    if not fills.empty:
        fills["time"] = pd.to_datetime(fills["ts"], unit="s")
        if "realized_after" in fills:
            st.subheader("Realized P&L")
            st.line_chart(fills.set_index("time")["realized_after"].ffill(),
                          height=220)
        st.dataframe(fills, hide_index=True, height=240)
    if not marks.empty:
        marks["time"] = pd.to_datetime(marks["ts"], unit="s")
        st.subheader("Markouts (rolling mean of 20)")
        st.line_chart(marks.set_index("time")["markout"].rolling(20).mean(),
                      height=220)


st.title("Kalshi market making — research dashboard")
tabs = st.tabs(["Data status", "Toxicity research", "Market explorer",
                "Shadow backtest", "Perf logs"])
with tabs[0]:
    tab_status()
with tabs[1]:
    tab_toxicity()
with tabs[2]:
    tab_explorer()
with tabs[3]:
    tab_shadow()
with tabs[4]:
    tab_logs()
