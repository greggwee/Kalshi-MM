"""Market selection for a test session: N liquid + N illiquid markets.

1. Page through open markets closing inside [min_hours_to_close,
   max_hours_to_close] (multivariate combos excluded by default).
2. Filter to markets the strategy can actually quote: tradable, a usable mid
   inside [min_mid, max_mid], a tick no coarser than one cent.
3. Score liquidity from the market listing (24h volume, open interest, top
   of book size, spread), then confirm the most liquid candidates and every
   possible illiquid pick against their real order books (batched: 100
   books per request) and add depth within +-depth_band of mid to the score.
4. Liquid = highest scores with spread <= liquid_max_spread.
   Illiquid = a two-sided book (a mid must exist, or the strategy just
   stands down) with either a wide spread or thin 24h volume, but a spread
   no wider than illiquid_max_spread: a 1c bid under a 97c ask has no price
   to make a market around. Preference: traded in the last 24h, then any
   history, then the thinnest.
   At most one market per event, so the test isn't six correlated strikes.

Liquidity is relative to what the environment has: demo flow is far thinner
than prod, so the report records the absolute numbers alongside the class.
"""
import logging
import math
import time
from typing import Dict, List, Optional

from src.orderbook import OrderBook

DEFAULT_SELECTION = {
    "liquid_count": 3,
    "illiquid_count": 3,
    "tickers": {"liquid": [], "illiquid": []},   # manual override per class
    "min_hours_to_close": 6.0,
    "max_hours_to_close": 336.0,
    "exclude_multivariate": True,
    "one_per_event": True,
    "max_pages": 10,
    "min_mid": 0.10,
    "max_mid": 0.90,
    "max_tick": 0.01,
    "liquid_max_spread": 0.05,
    "illiquid_min_spread": 0.04,
    "illiquid_max_volume_24h": 50,
    "illiquid_max_spread": 0.25,
    "book_check_top_n": 40,
    "depth_band": 0.05,
}


def listing_score(c: dict) -> float:
    """Higher = more liquid. Log-scaled so one huge market can't dominate."""
    spread_c = (c["spread"] if c["spread"] is not None else 0.5) * 100
    return (math.log1p(c["volume_24h"]) + 0.5 * math.log1p(c["open_interest"])
            + 0.5 * math.log1p(c["top_size"]) - 0.25 * spread_c
            + 0.5 * math.log1p(c.get("depth_band_contracts") or 0.0))


def candidate_from_market(m: dict, now: float) -> dict:
    bid, ask, last = m.get("yes_bid"), m.get("yes_ask"), m.get("last_price")
    two_sided = bid is not None and ask is not None and 0.0 < bid < ask < 1.0
    if two_sided:
        mid, source = round((bid + ask) / 2.0, 4), "listing"
    elif last is not None and 0.0 < last < 1.0:
        mid, source = last, "last_price"
    else:
        mid, source = None, None
    c = {
        "ticker": m["ticker"], "event_ticker": m.get("event_ticker"),
        "series_ticker": m.get("series_ticker"), "title": m.get("title"),
        "status": m.get("status"), "close_ts": m.get("close_ts"),
        "hours_to_close": round((m["close_ts"] - now) / 3600.0, 2) if m.get("close_ts") else None,
        "yes_bid": bid, "yes_ask": ask, "two_sided": two_sided,
        "spread": round(ask - bid, 4) if two_sided else None,
        "mid": mid, "mid_source": source,
        "volume_24h": m.get("volume_24h") or 0.0, "volume": m.get("volume") or 0.0,
        "open_interest": m.get("open_interest") or 0.0,
        "top_size": (m.get("bid_size") or 0.0) + (m.get("ask_size") or 0.0),
        "max_tick": m.get("max_tick"), "mve": m.get("mve", False),
        "tradable": m.get("tradable", True),
    }
    c["score"] = round(listing_score(c), 3)
    return c


def apply_book(c: dict, raw_book: dict, depth_band: float):
    """Replace listing quotes with the live book and add depth near mid."""
    book = OrderBook(c["ticker"])
    book.apply_snapshot(raw_book or {})
    c["book_checked"] = True
    c["book_levels"] = len(book.yes) + len(book.no)
    if book.mid is None:
        c["two_sided"] = False
        c["spread"] = None
        c["depth_band_contracts"] = 0.0
    else:
        c["two_sided"] = True
        c["yes_bid"], c["yes_ask"] = book.best_yes_bid, book.best_yes_ask
        c["spread"], c["mid"], c["mid_source"] = book.spread, book.mid, "book"
        c["top_size"] = book.yes.get(book.best_yes_bid, 0.0) + book.no.get(book.best_no_bid, 0.0)
        c["depth_band_contracts"] = sum(q for d, q in _distances(book) if d <= depth_band)
    c["score"] = round(listing_score(c), 3)


def _distances(book: OrderBook):
    m = book.mid
    for p, q in book.yes.items():
        yield m - p, q
    for p, q in book.no.items():
        yield (1.0 - p) - m, q


def _eligible(c: dict, cfg: dict) -> Optional[str]:
    """None if quotable, else the rejection reason."""
    if not c["tradable"]:
        return "not_tradable"
    if cfg["exclude_multivariate"] and c["mve"]:
        return "multivariate"
    if c["max_tick"] is not None and c["max_tick"] > cfg["max_tick"] + 1e-9:
        return "coarse_tick"
    if c["hours_to_close"] is not None and not (
            cfg["min_hours_to_close"] <= c["hours_to_close"] <= cfg["max_hours_to_close"]):
        return "close_time"
    if c["mid"] is None:
        return "no_mid"
    if not (cfg["min_mid"] <= c["mid"] <= cfg["max_mid"]):
        return "mid_out_of_band"
    return None


def _pick(cands: List[dict], n: int, used_events: set, one_per_event: bool) -> List[dict]:
    out = []
    for c in cands:
        if len(out) >= n:
            break
        ev = c.get("event_ticker") or c["ticker"]
        if one_per_event and ev in used_events:
            continue
        used_events.add(ev)
        out.append(c)
    return out


def select_markets(gw, cfg: Optional[dict] = None, logger: Optional[logging.Logger] = None,
                   now: Optional[float] = None) -> dict:
    cfg = {**DEFAULT_SELECTION, **(cfg or {})}
    logger = logger or logging.getLogger("selector")
    now = now or time.time()
    manual = cfg.get("tickers") or {}
    result = {"generated_ts": now, "criteria": cfg, "rejected": {}, "scanned": 0}

    if manual.get("liquid") or manual.get("illiquid"):
        wanted = list(manual.get("liquid") or []) + list(manual.get("illiquid") or [])
        markets = gw.markets(wanted)
        books = gw.orderbooks(wanted)
        for cls in ("liquid", "illiquid"):
            picked = []
            for t in manual.get(cls) or []:
                if t not in markets:
                    logger.warning(f"manual ticker {t} not found; skipping")
                    continue
                c = candidate_from_market(markets[t], now)
                apply_book(c, books.get(t), cfg["depth_band"])
                c["liquidity_class"], c["manual"] = cls, True
                picked.append(c)
            result[cls] = picked
        result["scanned"] = len(wanted)
        return _annotate_fees(gw, result, logger)

    listed = gw.list_open_markets(
        min_close_ts=now + cfg["min_hours_to_close"] * 3600,
        max_close_ts=now + cfg["max_hours_to_close"] * 3600,
        max_pages=cfg["max_pages"], exclude_multivariate=cfg["exclude_multivariate"])
    result["scanned"] = len(listed)
    eligible = []
    for m in listed:
        c = candidate_from_market(m, now)
        reason = _eligible(c, cfg)
        if reason:
            result["rejected"][reason] = result["rejected"].get(reason, 0) + 1
        else:
            eligible.append(c)
    result["eligible"] = len(eligible)
    logger.info(f"scanned {len(listed)} open markets, {len(eligible)} quotable "
                f"(rejected: {result['rejected']})")

    # confirm against live books: the most liquid by listing, and every
    # two-sided market narrow enough to be an illiquid pick (the bottom of
    # the ranking alone is all dead books)
    eligible.sort(key=lambda c: -c["score"])
    top = eligible[:cfg["book_check_top_n"]]
    thin = [c for c in eligible if c["two_sided"]
            and c["spread"] <= cfg["illiquid_max_spread"] + 1e-9]
    to_check = {c["ticker"]: c for c in top + thin}
    if to_check:
        books = gw.orderbooks(list(to_check))
        for t, c in to_check.items():
            apply_book(c, books.get(t), cfg["depth_band"])

    used: set = set()
    checked = list(to_check.values())
    liquid_pool = sorted([c for c in checked if c["two_sided"]
                          and c["spread"] <= cfg["liquid_max_spread"] + 1e-9],
                         key=lambda c: -c["score"])
    liquid = _pick(liquid_pool, cfg["liquid_count"], used, cfg["one_per_event"])
    if len(liquid) < cfg["liquid_count"]:  # environment too thin: best available
        rest = sorted([c for c in checked if c["two_sided"] and c not in liquid],
                      key=lambda c: -c["score"])
        for c in _pick(rest, cfg["liquid_count"] - len(liquid), used, cfg["one_per_event"]):
            c["below_liquid_threshold"] = True
            liquid.append(c)
    chosen = {c["ticker"] for c in liquid}
    illiquid_pool = [c for c in checked if c["two_sided"] and c["ticker"] not in chosen
                     and c["spread"] <= cfg["illiquid_max_spread"] + 1e-9
                     and (c["spread"] >= cfg["illiquid_min_spread"] - 1e-9
                          or c["volume_24h"] <= cfg["illiquid_max_volume_24h"])]
    illiquid_pool.sort(key=lambda c: (-(c["volume_24h"] > 0),
                                      -(c["volume"] > 0 or c["open_interest"] > 0),
                                      c["score"]))
    illiquid = _pick(illiquid_pool, cfg["illiquid_count"], used, cfg["one_per_event"])
    for c in liquid:
        c["liquidity_class"] = "liquid"
    for c in illiquid:
        c["liquidity_class"] = "illiquid"
    result["liquid"], result["illiquid"] = liquid, illiquid
    return _annotate_fees(gw, result, logger)


def _annotate_fees(gw, result: dict, logger: logging.Logger) -> dict:
    cache: Dict[str, dict] = {}
    for c in result.get("liquid", []) + result.get("illiquid", []):
        s = c.get("series_ticker")
        if not s:
            continue
        if s not in cache:
            try:
                cache[s] = gw.series_info(s)
            except Exception as e:
                logger.warning(f"series {s} fee lookup failed: {e}")
                cache[s] = {}
        c["fee_type"] = cache[s].get("fee_type")
        c["fee_multiplier"] = cache[s].get("fee_multiplier")
    return result


def session_specs(selection: dict) -> List[dict]:
    return [{"ticker": c["ticker"], "liquidity_class": c["liquidity_class"], "selection": c}
            for c in selection.get("liquid", []) + selection.get("illiquid", [])]


def format_selection(selection: dict) -> str:
    lines = [f"{'class':9s} {'ticker':40s} {'mid':>5s} {'sprd':>5s} {'vol24h':>7s} "
             f"{'OI':>7s} {'depth5c':>8s} {'close_h':>8s} {'score':>6s}  fee_type"]
    for c in selection.get("liquid", []) + selection.get("illiquid", []):
        flag = "*" if c.get("below_liquid_threshold") else ""
        lines.append(
            f"{c['liquidity_class'] + flag:9s} {c['ticker'][:40]:40s} "
            f"{c['mid'] or 0:5.2f} {c['spread'] if c['spread'] is not None else 0:5.2f} "
            f"{c['volume_24h']:7.0f} {c['open_interest']:7.0f} "
            f"{c.get('depth_band_contracts') or 0:8.0f} {c['hours_to_close'] or 0:8.1f} "
            f"{c['score']:6.2f}  {c.get('fee_type') or '?'}")
    return "\n".join(lines)
