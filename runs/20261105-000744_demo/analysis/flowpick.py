"""Read-only: which demo markets printed recently and can be quoted right now."""
import collections, json, sys, time
sys.path.insert(0, r"C:\Users\grego\AppData\Local\Temp\claude\840c593829146935\8552379b-feeb-4475-830b-aaa408586bd7\scratchpad\wt")
from src.clients import Environment
from src.credentials import make_public_client
from src.gateway import ExchangeGateway, market_view
from src.orderbook import OrderBook

c = make_public_client(Environment.DEMO)
gw = ExchangeGateway(c)
now = time.time()
trades = c.paginate(f"{c.markets_url}/trades", "trades", {"limit": 1000, "min_ts": int(now - 3 * 3600)}, max_pages=60)
print("prints in last 3h (capped by page limit):", len(trades))
counts = collections.Counter(t.get("ticker") or t.get("market_ticker") for t in trades)
last = {}
for t in trades:
    k = t.get("ticker") or t.get("market_ticker")
    ts = t.get("created_time")
    last[k] = max(last.get(k, ""), ts or "")
tickers = [t for t, _ in counts.most_common(1500)]
views = gw.markets(tickers)
books = {}
for i in range(0, len(tickers), 100):
    books.update(gw.orderbooks(tickers[i:i + 100]))
rows = []
for t in tickers:
    m = views.get(t)
    if not m or not m["tradable"] or not m.get("close_ts") or m.get("mve"):
        continue
    h = (m["close_ts"] - now) / 3600
    b = OrderBook(t)
    if t not in books:
        continue
    b.apply_snapshot(books[t])
    if b.mid is None or not (0.08 <= b.mid <= 0.92) or h < 5 or h > 400:
        continue
    rows.append((counts[t], t, round(b.mid, 3), round(b.best_yes_ask - b.best_yes_bid, 3),
                 b.yes.get(b.best_yes_bid), b.no.get(b.best_no_bid), round(h, 1), last[t][11:19],
                 m["series_ticker"]))
rows.sort(reverse=True)
print("prints ticker mid spread bid_depth ask_depth close_h last_print series")
seen = set()
for r in rows[:40]:
    print(r)
json.dump(rows, open(r"C:\Users\grego\AppData\Local\Temp\claude\840c593829146935\8552379b-feeb-4475-830b-aaa408586bd7\scratchpad\flowpick.json", "w"))
