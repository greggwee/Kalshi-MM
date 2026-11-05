"""Replay the model on recorded markets, logging every simulated fill, to see where the
losses come from. Read-only: a recorder database and public settlement lookups."""
import bisect, json, logging, os, sqlite3, sys
from concurrent.futures import ProcessPoolExecutor
SP = r"C:\Users\grego\AppData\Local\Temp\claude\840c593829146935\8552379b-feeb-4475-830b-aaa408586bd7\scratchpad"
sys.path.insert(0, SP + r"\wt")
DB = r"C:\Users\grego\Documents\Github\Kalshi-MM\data\kalshi_recorder.db"
SERIES = ["KXHIGHNY", "KXHIGHCHI", "KXHIGHMIA", "KXHIGHLAX", "KXHIGHDEN", "KXHIGHPHIL", "KXHIGHAUS"]


def job(a):
    from src.shadow import ShadowEngine, replay_events
    t, settle, stand_down = a
    q = logging.getLogger("q")
    q.addHandler(logging.NullHandler())
    q.propagate = False
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    row = conn.execute("SELECT close_time, fee_type, fee_multiplier FROM markets WHERE ticker=?", (t,)).fetchone()
    params = {"base_order_size": 2, "max_position": 10, "min_time_to_resolution_h": stand_down}
    cfg = {"calibration_file": SP + r"\wt\pmm_calibration.json", "resolve_every_s": 14400.0, "resolve_min_s": 1800.0}
    e = ShadowEngine(t, row[0], row[1] or "quadratic", row[2] or 1.0, params, q, fill_mode="queue", pmm_config=cfg)
    fills, mids = [], []
    orig = e._apply_fill

    def logged(side, price, qty, ts_ms):
        fills.append(dict(ticker=t, ts=ts_ms / 1000.0, side=side, price=price, qty=qty, mid=e.mid(ts_ms),
                          tth=e.time_to_close_h(ts_ms), bid=e.book.best_yes_bid, ask=e.book.best_yes_ask,
                          q=e.position))
        orig(side, price, qty, ts_ms)
    e._apply_fill = logged
    for kind, ts, payload in replay_events(conn, t):
        e.on_event(kind, ts, payload)
        m = e.mid(ts)
        if m is not None:
            mids.append((ts / 1000.0, m))
    conn.close()
    times = [m[0] for m in mids]
    for f in fills:
        for h in (60, 600):
            i = bisect.bisect_right(times, f["ts"] + h) - 1
            f[f"mid{h}"] = mids[i][1] if i >= 0 else None
        f["settle"] = settle
    return fills


def main():
    from src.shadow import fetch_settle_price
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    tickers = [r[0] for r in conn.execute("SELECT ticker FROM markets") if r[0].split("-")[0] in SERIES]
    conn.close()
    jobs = []
    for t in tickers:
        s = fetch_settle_price(t)
        if s is not None:
            jobs.append((t, s, 8.0))
    out = []
    with ProcessPoolExecutor(max_workers=4) as pool:
        for fills in pool.map(job, jobs, chunksize=1):
            out.extend(fills)
    json.dump(out, open(SP + r"\fills_autopsy.json", "w"))
    print(len(out), "fills from", len(jobs), "markets")


if __name__ == "__main__":
    main()
