import json, collections
import numpy as np
SP = r"C:\Users\grego\AppData\Local\Temp\claude\840c593829146935\8552379b-feeb-4475-830b-aaa408586bd7\scratchpad"
F = json.load(open(SP + r"\fills_autopsy.json"))
print(len(F), "fills;", round(sum(f["qty"] for f in F)), "contracts")


def stats(rows):
    if not rows:
        return None
    w = np.array([r["qty"] for r in rows])
    sign = np.array([1.0 if r["side"] == "bid" else -1.0 for r in rows])
    price = np.array([r["price"] for r in rows])
    mid = np.array([r["mid"] if r["mid"] is not None else r["price"] for r in rows])
    m60 = np.array([r["mid60"] if r["mid60"] is not None else m for r, m in zip(rows, mid)])
    m600 = np.array([r["mid600"] if r["mid600"] is not None else m for r, m in zip(rows, mid)])
    settle = np.array([r["settle"] for r in rows])
    edge = sign * (mid - price)
    mk60 = sign * (m60 - price)
    mk600 = sign * (m600 - price)
    to_settle = sign * (settle - price)
    av = lambda x: 100 * float(np.sum(w * x) / np.sum(w))
    return f"ct {int(w.sum()):7d}  edge {av(edge):+6.2f}c  m60 {av(mk60):+6.2f}c  m600 {av(mk600):+6.2f}c  to-settle {av(to_settle):+7.2f}c   total to-settle ${float(np.sum(w * to_settle)):+8.2f}"


def table(title, key, order=None):
    print("\n--", title)
    groups = collections.defaultdict(list)
    for f in F:
        groups[key(f)].append(f)
    for k in (order or sorted(groups)):
        if k in groups:
            print(f"{str(k):>16s}  {stats(groups[k])}")


print("all", stats(F))
edges = [8, 12, 16, 20, 24, 30, 36, 48, 72, 1e9]


def tth(f):
    for lo, hi in zip(edges, edges[1:]):
        if lo <= f["tth"] < hi:
            return f"{lo}-{hi if hi < 1e8 else 'inf'}h"
    return "<8h"


table("hours to close", tth, [f"{lo}-{hi if hi < 1e8 else 'inf'}h" for lo, hi in zip(edges, edges[1:])])


def spread(f):
    if f["bid"] is None or f["ask"] is None:
        return "one-sided"
    s = round((f["ask"] - f["bid"]) * 100)
    return f"{s}c" if s <= 4 else ("5-9c" if s < 10 else "10c+")


table("market spread at fill", spread, ["1c", "2c", "3c", "4c", "5-9c", "10c+", "one-sided"])
table("side", lambda f: f["side"])


def pband(f):
    m = f["mid"] or f["price"]
    return "<.2" if m < .2 else ".2-.4" if m < .4 else ".4-.6" if m < .6 else ".6-.8" if m < .8 else ">.8"


table("price band", pband, ["<.2", ".2-.4", ".4-.6", ".6-.8", ">.8"])


def fill_type(f):
    """Printed past our quote in a move the recorded book had not caught up with?"""
    m = f["mid"]
    if m is None:
        return "no mid"
    gap = (f["price"] - m) if f["side"] == "bid" else (m - f["price"])
    return "quote through mid (stale)" if gap >= 0 else "quote on our side of mid"


table("was the quote stale at the fill", fill_type)
table("series", lambda f: f["ticker"].split("-")[0])
hours = lambda f: f"{__import__('datetime').datetime.utcfromtimestamp(f['ts']).hour:02d}"
table("UTC hour", hours)
