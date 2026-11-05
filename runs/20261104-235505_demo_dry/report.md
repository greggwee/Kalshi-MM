# Market-making diagnostic report

- Run: `runs\20261104-235505_demo_dry`  
- Environment: **demo** (DRY RUN)  
- Duration: 603.4 s over 300 cycles  
- End reason: duration_reached  
- Orders left resting after shutdown: 0
- Balance: 2453.2097 -> 2453.2097 (portfolio value 0.0 -> 0.0)

## Headline

**0 fills / 0 contracts** across 6 markets, **net P&L $+0.0000** after $0.0000 fees. Edge at fill -/contract, 60s markout -/contract.

## Liquid vs illiquid

| metric | illiquid | liquid | all |
|---|---|---|---|
| markets | 3 | 3 | 6 |
| quoting % of time | 100.0 | 100.0 | 100.0 |
| two-sided % | 100.0 | 100.0 | 100.0 |
| market spread (c) | 4.3 | 1.0 | 2.65 |
| our quoted spread (c) | 2.46 | 1.0 | 1.73 |
| bid at touch % | 100.0 | 100.0 | 100.0 |
| ask at touch % | 98.3 | 100.0 | 99.2 |
| public contracts printed | 14.88 | 22.57 | 37.45 |
| fills | 0 | 0 | 0 |
| contracts filled | 0 | 0 | 0 |
| market share % | 0.0 | 0.0 | 0.0 |
| edge at fill (c/ct) | - | - | - |
| markout 10s (c/ct) | - | - | - |
| markout 60s (c/ct) | - | - | - |
| markout 300s (c/ct) | - | - | - |
| adverse sel. 10s (c/ct) | - | - | - |
| adverse sel. 60s (c/ct) | - | - | - |
| adverse sel. 300s (c/ct) | - | - | - |
| net P&L ($) | 0 | 0 | 0 |
| fees ($) | 0 | 0 | 0 |
| order writes | 39 | 24 | 63 |
| failed writes | 0 | 0 | 0 |
| max abs inventory | 0.0 | 0.0 | 0.0 |
| avg vol scale sqrt(p(1-p))/0.5 | 0.21 | 0.213 | 0.211 |
| min_spread floor binding % | 0.0 | 0.0 | 0.0 |

## Findings

- **MEDIUM** (KXNBAGAME-26NOV04PHIDET-PHI): [liquid] no fills. Market traded, quotes were competitive: check queue position.
- **MEDIUM** (KXNBAGAME-26NOV04MILBOS-MIL): [liquid] no fills. Market traded, quotes were competitive: check queue position.
- **MEDIUM** (KXNFLTOTAL-26NOV09BUFMIN-48): [liquid] no fills. no public prints in the market during the session
- **MEDIUM** (KXRAINSHARD2-26NOV05-CMH): [illiquid] no fills. Market traded, quotes were competitive: check queue position.
- **MEDIUM** (KXRAINSHARD2-26NOV05-TPA): [illiquid] no fills. Market traded, quotes were competitive: check queue position.
- **MEDIUM** (KXNCAAFTOTAL-26NOV04TOLSAC-38): [illiquid] no fills. Market traded, quotes were competitive: check queue position.
- **INFO** (session): DRY RUN: no orders reached the exchange; fills and P&L are zero by construction. Quote placement metrics are still valid.
- **INFO** (KXRAINSHARD2-26NOV05-CMH): [illiquid] 5 public prints traded through our quoted price without filling us (approximate: quote state sampled once per cycle).
- **INFO** (KXRAINSHARD2-26NOV05-TPA): [illiquid] 4 public prints traded through our quoted price without filling us (approximate: quote state sampled once per cycle).
- **INFO** (KXNCAAFTOTAL-26NOV04TOLSAC-38): [illiquid] 7 public prints traded through our quoted price without filling us (approximate: quote state sampled once per cycle).

## Per-market detail

### KXNBAGAME-26NOV04PHIDET-PHI (liquid)
_Philadelphia wins_

- Selection: mid 0.455, spread 0.01, 24h vol 7028.23, OI 9619.54, depth +-5c 2763.9199999999996, closes in 48.08 h, fee_type quadratic
- Time: quoting 100.0%, two-sided 100.0%; stood down: never
- Market: book two-sided 100.0%, avg spread 1.0c, top depth bid/ask 745.7/147.9, mid 0.455 -> 0.455 (range 0.455-0.455, 0 changes, realized vol 0.0/sqrt h), 4 public prints / 4.71 contracts
- Quotes: quoted spread 1.0c (model 1.0c); bid at touch 100.0% (inside 0.0%, - ticks behind otherwise); ask at touch 100.0% (inside 0.0%, - ticks behind); crossed->clamped 0.0%; risk-gated 0.0%
- Model: sigma 0.0372 (raw 0.15 x vol scale 0.248; from data 0.0% of the time), k 200.0, gamma 0.2; width terms inventory 0.462c / liquidity 0.538c / premium 0.0c (shares 46.2%/53.8%/0.0%); floor binding 0.0%; reservation skew 0c avg, corr with q -
- Inventory: start 0.0, final 0.0, max |q| 0.0, avg 0.0 (avg |q| 0.0), at limit 0.0%
- Fills: 0 (0 ct: bought 0, sold 0, round-trip 0), maker -, 0.0/quoting-hour, market share 0.0%; edge at fill -; markouts: 10s - (adverse -, n=0), 60s - (adverse -, n=0), 300s - (adverse -, n=0)
- Orders: {'create': 2, 'amend': 4, 'cancel': 2} rejects none; amend reasons {'refresh_expiry': 4}; 47.9 writes/h; latency create p50 0.0ms p95 0.0ms, amend p50 0.0ms p95 0.0ms, cancel p50 0.0ms p95 0.0ms
- Tape vs quotes: 0 prints through our quote, 4 at our level
- P&L: realized 0.0, unrealized 0.0 (mark 0.455), fees 0.0, **net 0.0**; exchange position -

### KXNBAGAME-26NOV04MILBOS-MIL (liquid)
_Milwaukee wins_

- Selection: mid 0.175, spread 0.01, 24h vol 2169.3, OI 4798.65, depth +-5c 17171.53, closes in 48.58 h, fee_type quadratic
- Time: quoting 100.0%, two-sided 100.0%; stood down: never
- Market: book two-sided 100.0%, avg spread 1.0c, top depth bid/ask 2875.2/2674.9, mid 0.175 -> 0.175 (range 0.175-0.175, 0 changes, realized vol 0.0/sqrt h), 16 public prints / 17.86 contracts
- Quotes: quoted spread 1.0c (model 1.0c); bid at touch 100.0% (inside 0.0%, - ticks behind otherwise); ask at touch 100.0% (inside 0.0%, - ticks behind); crossed->clamped 0.0%; risk-gated 0.0%
- Model: sigma 0.02166 (raw 0.15 x vol scale 0.144; from data 0.0% of the time), k 200.0, gamma 0.2; width terms inventory 0.296c / liquidity 0.704c / premium 0.0c (shares 29.6%/70.4%/0.0%); floor binding 0.0%; reservation skew 0.005c avg, corr with q -
- Inventory: start 0.0, final 0.0, max |q| 0.0, avg 0.0 (avg |q| 0.0), at limit 0.0%
- Fills: 0 (0 ct: bought 0, sold 0, round-trip 0), maker -, 0.0/quoting-hour, market share 0.0%; edge at fill -; markouts: 10s - (adverse -, n=0), 60s - (adverse -, n=0), 300s - (adverse -, n=0)
- Orders: {'create': 2, 'amend': 4, 'cancel': 2} rejects none; amend reasons {'refresh_expiry': 4}; 47.9 writes/h; latency create p50 0.0ms p95 0.0ms, amend p50 0.0ms p95 0.0ms, cancel p50 0.0ms p95 0.0ms
- Tape vs quotes: 0 prints through our quote, 16 at our level
- P&L: realized 0.0, unrealized 0.0 (mark 0.175), fees 0.0, **net 0.0**; exchange position -

### KXNFLTOTAL-26NOV09BUFMIN-48 (liquid)
_Full Game: over 47.5 points scored?_

- Selection: mid 0.435, spread 0.01, 24h vol 377.49, OI 377.49, depth +-5c 11256.990000000002, closes in 169.33 h, fee_type quadratic
- Time: quoting 100.0%, two-sided 100.0%; stood down: never
- Market: book two-sided 100.0%, avg spread 1.0c, top depth bid/ask 387.6/1137.6, mid 0.435 -> 0.435 (range 0.435-0.435, 0 changes, realized vol 0.0/sqrt h), 0 public prints / 0 contracts
- Quotes: quoted spread 1.0c (model 1.0c); bid at touch 100.0% (inside 0.0%, - ticks behind otherwise); ask at touch 100.0% (inside 0.0%, - ticks behind); crossed->clamped 0.0%; risk-gated 0.0%
- Model: sigma 0.03687 (raw 0.15 x vol scale 0.246; from data 0.0% of the time), k 200.0, gamma 0.2; width terms inventory 0.469c / liquidity 0.531c / premium 0.0c (shares 46.9%/53.1%/0.0%); floor binding 0.0%; reservation skew 0.001c avg, corr with q -
- Inventory: start 0.0, final 0.0, max |q| 0.0, avg 0.0 (avg |q| 0.0), at limit 0.0%
- Fills: 0 (0 ct: bought 0, sold 0, round-trip 0), maker -, 0.0/quoting-hour, market share -; edge at fill -; markouts: 10s - (adverse -, n=0), 60s - (adverse -, n=0), 300s - (adverse -, n=0)
- Orders: {'create': 2, 'amend': 4, 'cancel': 2} rejects none; amend reasons {'refresh_expiry': 4}; 47.9 writes/h; latency create p50 0.0ms p95 0.0ms, amend p50 0.0ms p95 0.0ms, cancel p50 0.0ms p95 0.0ms
- Tape vs quotes: 0 prints through our quote, 0 at our level
- P&L: realized 0.0, unrealized 0.0 (mark 0.435), fees 0.0, **net 0.0**; exchange position -

### KXRAINSHARD2-26NOV05-CMH (illiquid)
_Will it rain in Columbus on Nov 5, 2026?_

- Selection: mid 0.5, spread 0.04, 24h vol 292.0, OI 4.0, depth +-5c 400.0, closes in 29.08 h, fee_type quadratic
- Time: quoting 100.0%, two-sided 100.0%; stood down: never
- Market: book two-sided 100.0%, avg spread 4.0c, top depth bid/ask 264.3/264.0, mid 0.5 -> 0.5 (range 0.5-0.5, 0 changes, realized vol 0.0/sqrt h), 5 public prints / 5.0 contracts
- Quotes: quoted spread 2.0c (model 2.0c); bid at touch 100.0% (inside 100.0%, - ticks behind otherwise); ask at touch 100.0% (inside 100.0%, - ticks behind); crossed->clamped 0.0%; risk-gated 0.0%
- Model: sigma 0.0375 (raw 0.15 x vol scale 0.25; from data 0.0% of the time), k 50.0, gamma 0.2; width terms inventory 0.663c / liquidity 1.337c / premium 0.0c (shares 33.2%/66.8%/0.0%); floor binding 0.0%; reservation skew 0c avg, corr with q -
- Inventory: start 0.0, final 0.0, max |q| 0.0, avg 0.0 (avg |q| 0.0), at limit 0.0%
- Fills: 0 (0 ct: bought 0, sold 0, round-trip 0), maker -, 0.0/quoting-hour, market share 0.0%; edge at fill -; markouts: 10s - (adverse -, n=0), 60s - (adverse -, n=0), 300s - (adverse -, n=0)
- Orders: {'create': 2, 'amend': 4, 'cancel': 2} rejects none; amend reasons {'refresh_expiry': 4}; 47.9 writes/h; latency create p50 0.0ms p95 0.0ms, amend p50 0.0ms p95 0.0ms, cancel p50 0.0ms p95 0.0ms
- Tape vs quotes: 5 prints through our quote, 0 at our level
- P&L: realized 0.0, unrealized 0.0 (mark 0.5), fees 0.0, **net 0.0**; exchange position -

### KXRAINSHARD2-26NOV05-TPA (illiquid)
_Will it rain in Tampa on Nov 5, 2026?_

- Selection: mid 0.5, spread 0.04, 24h vol 314.0, OI 8.0, depth +-5c 400.0, closes in 29.08 h, fee_type quadratic
- Time: quoting 100.0%, two-sided 100.0%; stood down: never
- Market: book two-sided 100.0%, avg spread 4.0c, top depth bid/ask 266.0/266.0, mid 0.5 -> 0.5 (range 0.5-0.5, 0 changes, realized vol 0.0/sqrt h), 4 public prints / 4.0 contracts
- Quotes: quoted spread 2.0c (model 2.0c); bid at touch 100.0% (inside 100.0%, - ticks behind otherwise); ask at touch 100.0% (inside 100.0%, - ticks behind); crossed->clamped 0.0%; risk-gated 0.0%
- Model: sigma 0.0375 (raw 0.15 x vol scale 0.25; from data 0.0% of the time), k 50.0, gamma 0.2; width terms inventory 0.664c / liquidity 1.336c / premium 0.0c (shares 33.2%/66.8%/0.0%); floor binding 0.0%; reservation skew 0c avg, corr with q -
- Inventory: start 0.0, final 0.0, max |q| 0.0, avg 0.0 (avg |q| 0.0), at limit 0.0%
- Fills: 0 (0 ct: bought 0, sold 0, round-trip 0), maker -, 0.0/quoting-hour, market share 0.0%; edge at fill -; markouts: 10s - (adverse -, n=0), 60s - (adverse -, n=0), 300s - (adverse -, n=0)
- Orders: {'create': 2, 'amend': 4, 'cancel': 2} rejects none; amend reasons {'refresh_expiry': 4}; 47.9 writes/h; latency create p50 0.0ms p95 0.0ms, amend p50 0.0ms p95 0.1ms, cancel p50 0.0ms p95 0.0ms
- Tape vs quotes: 4 prints through our quote, 0 at our level
- P&L: realized 0.0, unrealized 0.0 (mark 0.5), fees 0.0, **net 0.0**; exchange position -

### KXNCAAFTOTAL-26NOV04TOLSAC-38 (illiquid)
_Full Game: Over 37.5 points scored_

- Selection: mid 0.835, spread 0.05, 24h vol 54.79, OI 60.95, depth +-5c 72.45, closes in 48.08 h, fee_type quadratic
- Time: quoting 100.0%, two-sided 100.0%; stood down: never
- Market: book two-sided 68.4%, avg spread 4.9c, top depth bid/ask 41.4/29.8, mid 0.835 -> 0.87 (range 0.835-0.87, 7 changes, realized vol 0.1392/sqrt h), 7 public prints / 5.88 contracts
- Quotes: quoted spread 3.37c (model 3.37c); bid at touch 100.0% (inside 100.0%, - ticks behind otherwise); ask at touch 95.0% (inside 95.0%, 2.0 ticks behind); crossed->clamped 0.0%; risk-gated 0.0%
- Model: sigma 0.02173 (raw 0.17046 x vol scale 0.129; from data 0.0% of the time), k 41.0, gamma 0.2; width terms inventory 0.4c / liquidity 2.967c / premium 0.0c (shares 11.9%/88.1%/0.0%); floor binding 0.0%; reservation skew 0.014c avg, corr with q -
- Inventory: start 0.0, final 0.0, max |q| 0.0, avg 0.0 (avg |q| 0.0), at limit 0.0%
- Fills: 0 (0 ct: bought 0, sold 0, round-trip 0), maker -, 0.0/quoting-hour, market share 0.0%; edge at fill -; markouts: 10s - (adverse -, n=0), 60s - (adverse -, n=0), 300s - (adverse -, n=0)
- Orders: {'create': 8, 'cancel': 8, 'amend': 7} rejects none; amend reasons {'refresh_expiry': 1, 'reprice': 6}; 137.8 writes/h; latency create p50 0.0ms p95 0.0ms, cancel p50 0.0ms p95 0.0ms, amend p50 0.0ms p95 0.0ms
- Tape vs quotes: 7 prints through our quote, 0 at our level
- P&L: realized 0.0, unrealized 0.0 (mark 0.88), fees 0.0, **net 0.0**; exchange position -

## Infrastructure

- Cycle time p50 48.4 ms, p95 320.5 ms, overrun 0.0%; cycle errors 0; position mismatches 0
- API: 725 requests, 0 errors, 0 retries, 0.0 s spent pacing

| endpoint | count | errors | p50 ms | p95 ms | max ms |
|---|---|---|---|---|---|
| GET /account/limits | 1 | - | 117.6 | 117.6 | 117.6 |
| GET /exchange/status | 20 | - | 34.0 | 49.9 | 49.9 |
| GET /markets | 20 | - | 34.9 | 45.7 | 45.7 |
| GET /markets/orderbooks | 301 | - | 34.4 | 48.2 | 94.6 |
| GET /markets/trades | 366 | - | 33.9 | 39.0 | 54.6 |
| GET /portfolio/balance | 11 | - | 34.0 | 43.2 | 43.2 |
| GET /series/{x} | 6 | - | 33.4 | 33.9 | 33.9 |

## Configuration

```json
{
  "strategy": {
    "gamma": 1.0,
    "default_k": 40.0,
    "base_order_size": 2,
    "max_position": 10,
    "min_spread": 0.02,
    "premium_scale": 0.02,
    "min_time_to_resolution_h": 1,
    "min_quote_mid": 0.05,
    "max_quote_mid": 0.95,
    "max_horizon_h": 24.0,
    "gamma_mode": "constant",
    "calibration_constant": 0.04,
    "sigma_floor": 0.01,
    "vol_window": 120,
    "fair_value_adjustment": 0.0,
    "k_refresh_every": 10,
    "model": "pmm",
    "pmm": {
      "calibrated": false,
      "stand_down_h": 1,
      "maker_fee": 0.0,
      "gamma": 0.2,
      "gamma_T": 0.05,
      "min_offset": 0.005,
      "prior": {
        "T": 48.0,
        "sigma0": 0.15,
        "sigma1": 0.0,
        "sigma2": 0.0,
        "eta": 3.0,
        "A0": 2.0,
        "A1": 0.0,
        "xi": 2.0,
        "nu": 1.0,
        "k0": 80.0,
        "k1": 80.0,
        "kappa": 1.5
      }
    }
  },
  "strategy_overrides": {
    "liquid": {},
    "illiquid": {}
  },
  "execution": {
    "order_expiration_s": 300,
    "expiry_refresh_frac": 0.33,
    "replenish_threshold": 0.5,
    "cross_policy": "clamp",
    "aggressive_reprice": "replace",
    "cancel_order_on_pause": true,
    "post_only": true,
    "flatten_on_exit": false,
    "listing_grace_s": 5.0
  },
  "risk": {
    "max_session_loss": 25.0,
    "max_market_loss": 10.0,
    "max_gross_position": 40,
    "max_consecutive_errors": 8,
    "min_balance": 0.0,
    "max_rejects_per_min": 12,
    "stale_book_s": 15.0
  }
}
```

Definitions: see the docstring of `src/diagnostics.py`. Markouts use the mid net of our own orders, sampled once per cycle.
