# Market-making diagnostic report

- Run: `runs\20261105-000744_demo`  
- Environment: **demo**  
- Duration: 3602.9 s over 1780 cycles  
- End reason: duration_reached  
- Orders left resting after shutdown: 0
- Balance: 2453.2097 -> 2449.9495 (portfolio value 0.0 -> 2.72)

## Headline

**6 fills / 10.0 contracts** across 6 markets, **net P&L $-0.2300** after $0.0000 fees. Edge at fill 0.9c/contract, 60s markout -2.4c/contract.

## Liquid vs illiquid

| metric | illiquid | liquid | all |
|---|---|---|---|
| markets | 3 | 3 | 6 |
| quoting % of time | 33.6 | 100.0 | 66.8 |
| two-sided % | 33.6 | 100.0 | 66.8 |
| market spread (c) | 3.67 | 1.06 | 2.36 |
| our quoted spread (c) | 1.67 | 1.1 | 1.39 |
| bid at touch % | 100.0 | 100.0 | 100.0 |
| ask at touch % | 100.0 | 92.2 | 96.1 |
| public contracts printed | 54.0 | 9113.21 | 9167.21 |
| fills | 0 | 6 | 6 |
| contracts filled | 0 | 10.0 | 10.0 |
| market share % | 0.0 | 0.1 | 0.1 |
| edge at fill (c/ct) | - | 0.9 | 0.9 |
| markout 10s (c/ct) | - | -3.828 | -3.828 |
| markout 60s (c/ct) | - | -2.4 | -2.4 |
| markout 300s (c/ct) | - | 0.5 | 0.5 |
| adverse sel. 10s (c/ct) | - | -4.728 | -4.728 |
| adverse sel. 60s (c/ct) | - | -3.3 | -3.3 |
| adverse sel. 300s (c/ct) | - | 0.0 | 0.0 |
| net P&L ($) | 0 | -0.23 | -0.23 |
| fees ($) | 0 | 0 | 0 |
| order writes | 68 | 144 | 212 |
| failed writes | 28 | 12 | 40 |
| max abs inventory | 0.0 | 4.0 | 4.0 |
| avg vol scale sqrt(p(1-p))/0.5 | 0.235 | 0.213 | 0.224 |
| min_spread floor binding % | 0.0 | 0.0 | 0.0 |

## Findings

- **HIGH** (KXNBAGAME-26NOV04MILBOS-MIL): [liquid] 60s markout -3.125c/contract on 5 fills (edge at fill 1.0c): adverse selection exceeded the spread captured.
- **HIGH** (KXRAINSHARD2-26NOV05-CMH): [illiquid] quoted only 0.4% of the time; main stand-down reason: halted:reject_rate (99.6%).
- **HIGH** (KXRAINSHARD2-26NOV05-TPA): [illiquid] quoted only 0.4% of the time; main stand-down reason: halted:reject_rate (99.6%).
- **MEDIUM** (KXNBAGAME-26NOV04PHIDET-PHI): [liquid] 4/46 order writes failed: {'http_503_service_unavailable': 4}.
- **MEDIUM** (KXNBAGAME-26NOV04MILBOS-MIL): [liquid] 4/54 order writes failed: {'http_503_service_unavailable': 4}.
- **MEDIUM** (KXNFLTOTAL-26NOV09BUFMIN-48): [liquid] no fills. no public prints in the market during the session
- **MEDIUM** (KXNFLTOTAL-26NOV09BUFMIN-48): [liquid] 4/44 order writes failed: {'http_503_service_unavailable': 4}.
- **MEDIUM** (KXRAINSHARD2-26NOV05-CMH): [illiquid] no fills. Market traded, quotes were competitive: check queue position.
- **MEDIUM** (KXRAINSHARD2-26NOV05-CMH): [illiquid] 12/12 order writes failed: {'insufficient_balance': 12}.
- **MEDIUM** (KXRAINSHARD2-26NOV05-TPA): [illiquid] no fills. Market traded, quotes were competitive: check queue position.
- **MEDIUM** (KXRAINSHARD2-26NOV05-TPA): [illiquid] 12/12 order writes failed: {'insufficient_balance': 12}.
- **MEDIUM** (KXNFLTOTAL-26NOV09BUFMIN-53): [illiquid] no fills. no public prints in the market during the session
- **MEDIUM** (KXNFLTOTAL-26NOV09BUFMIN-53): [illiquid] 4/44 order writes failed: {'http_503_service_unavailable': 4}.
- **INFO** (KXNBAGAME-26NOV04MILBOS-MIL): [liquid] 6 public prints traded through our quoted price without filling us (approximate: quote state sampled once per cycle).

## Per-market detail

### KXNBAGAME-26NOV04PHIDET-PHI (liquid)
_Philadelphia wins_

- Selection: mid 0.455, spread 0.01, 24h vol 7035.17, OI 9625.25, depth +-5c 2847.6399999999994, closes in 47.87 h, fee_type quadratic
- Time: quoting 100.0%, two-sided 100.0%; stood down: never
- Market: book two-sided 100.0%, avg spread 1.01c, top depth bid/ask 804.0/163.4, mid 0.455 -> 0.455 (range 0.455-0.46, 2 changes, realized vol 0.0071/sqrt h), 71 public prints / 192.11 contracts
- Quotes: quoted spread 1.24c (model 1.24c); bid at touch 100.0% (inside 0.0%, - ticks behind otherwise); ask at touch 76.6% (inside 0.0%, 1.0 ticks behind); crossed->clamped 0.0%; risk-gated 0.0%
- Model: sigma 0.03462 (raw 0.13959 x vol scale 0.248; from data 66.6% of the time), k 200.0, gamma 0.2; width terms inventory 0.447c / liquidity 0.796c / premium 0.0c (shares 36.0%/64.0%/0.0%); floor binding 0.0%; reservation skew 0.092c avg, corr with q -1.0
- Inventory: start 0.0, final -2.0, max |q| 2.0, avg -0.486 (avg |q| 0.486), at limit 0.0%
- Fills: 1 (2.0 ct: bought 0, sold 2.0, round-trip 0), maker 100.0%, 1.0/quoting-hour, market share 1.0%; edge at fill 0.5c; markouts: 10s 0.0c (adverse -0.5c, n=1), 60s 0.5c (adverse 0.0c, n=1), 300s 0.5c (adverse 0.0c, n=1)
- Orders: {'create': 5, 'amend': 35, 'create_failed': 4, 'cancel': 2} rejects {'http_503_service_unavailable': 4}; amend reasons {'refresh_expiry': 35}; 46.1 writes/h; latency create p50 34.6ms p95 35.8ms, amend p50 34.7ms p95 182.7ms, cancel p50 38.1ms p95 38.1ms
  - reject `create` http_503_service_unavailable: 503 POST /portfolio/events/orders: service_unavailable service_unavailable
  - reject `create` http_503_service_unavailable: 503 POST /portfolio/events/orders: service_unavailable service_unavailable
  - reject `create` http_503_service_unavailable: 503 POST /portfolio/events/orders: service_unavailable service_unavailable
  - reject `create` http_503_service_unavailable: 503 POST /portfolio/events/orders: service_unavailable service_unavailable
- Tape vs quotes: 0 prints through our quote, 59 at our level
- P&L: realized 0.0, unrealized 0.01 (mark 0.455), fees 0.0, **net 0.01**; exchange position -2.0

### KXNBAGAME-26NOV04MILBOS-MIL (liquid)
_Milwaukee wins_

- Selection: mid 0.175, spread 0.01, 24h vol 2188.91, OI 4818.47, depth +-5c 17151.71, closes in 48.37 h, fee_type quadratic
- Time: quoting 100.0%, two-sided 100.0%; stood down: never
- Market: book two-sided 100.0%, avg spread 1.18c, top depth bid/ask 1588.5/2066.6, mid 0.175 -> 0.175 (range 0.095-0.175, 6 changes, realized vol 0.1136/sqrt h), 80 public prints / 8921.1 contracts
- Quotes: quoted spread 1.07c (model 1.07c); bid at touch 100.0% (inside 0.8%, - ticks behind otherwise); ask at touch 100.0% (inside 0.8%, - ticks behind); crossed->clamped 0.0%; risk-gated 0.0%
- Model: sigma 0.01906 (raw 0.13255 x vol scale 0.144; from data 66.6% of the time), k 194.21, gamma 0.2; width terms inventory 0.227c / liquidity 0.848c / premium 0.0c (shares 21.1%/78.9%/0.0%); floor binding 0.0%; reservation skew -0.112c avg, corr with q -0.973
- Inventory: start 0.0, final 0.0, max |q| 4.0, avg 1.262 (avg |q| 1.262), at limit 0.0%
- Fills: 5 (8.0 ct: bought 4.0, sold 4.0, round-trip 4.0), maker 100.0%, 5.01/quoting-hour, market share 0.1%; edge at fill 1.0c; markouts: 10s -4.785c (adverse -5.785c, n=5), 60s -3.125c (adverse -4.125c, n=5), 300s 0.5c (adverse 0.0c, n=1)
- Orders: {'create': 12, 'amend': 32, 'cancel': 6, 'create_failed': 4} rejects {'http_503_service_unavailable': 4}; amend reasons {'refresh_expiry': 31, 'reprice': 1}; 54.1 writes/h; latency create p50 35.3ms p95 37.4ms, amend p50 34.3ms p95 42.7ms, cancel p50 34.5ms p95 35.6ms
  - reject `create` http_503_service_unavailable: 503 POST /portfolio/events/orders: service_unavailable service_unavailable
  - reject `create` http_503_service_unavailable: 503 POST /portfolio/events/orders: service_unavailable service_unavailable
  - reject `create` http_503_service_unavailable: 503 POST /portfolio/events/orders: service_unavailable service_unavailable
  - reject `create` http_503_service_unavailable: 503 POST /portfolio/events/orders: service_unavailable service_unavailable
- Tape vs quotes: 6 prints through our quote, 74 at our level
- P&L: realized -0.24, unrealized 0.0 (mark 0.175), fees 0.0, **net -0.24**; exchange position 0.0

### KXNFLTOTAL-26NOV09BUFMIN-48 (liquid)
_Full Game: over 47.5 points scored?_

- Selection: mid 0.435, spread 0.01, 24h vol 377.49, OI 377.49, depth +-5c 11256.990000000002, closes in 169.12 h, fee_type quadratic
- Time: quoting 100.0%, two-sided 100.0%; stood down: never
- Market: book two-sided 100.0%, avg spread 1.0c, top depth bid/ask 387.6/1137.6, mid 0.435 -> 0.435 (range 0.435-0.435, 0 changes, realized vol 0.0/sqrt h), 0 public prints / 0 contracts
- Quotes: quoted spread 1.0c (model 1.0c); bid at touch 100.0% (inside 0.0%, - ticks behind otherwise); ask at touch 100.0% (inside 0.0%, - ticks behind); crossed->clamped 0.0%; risk-gated 0.0%
- Model: sigma 0.03431 (raw 0.13961 x vol scale 0.246; from data 66.6% of the time), k 200.0, gamma 0.2; width terms inventory 0.434c / liquidity 0.566c / premium 0.0c (shares 43.4%/56.6%/0.0%); floor binding 0.0%; reservation skew 0.001c avg, corr with q -
- Inventory: start 0.0, final 0.0, max |q| 0.0, avg 0.0 (avg |q| 0.0), at limit 0.0%
- Fills: 0 (0 ct: bought 0, sold 0, round-trip 0), maker -, 0.0/quoting-hour, market share -; edge at fill -; markouts: 10s - (adverse -, n=0), 60s - (adverse -, n=0), 300s - (adverse -, n=0)
- Orders: {'create': 4, 'amend': 34, 'create_failed': 4, 'cancel': 2} rejects {'http_503_service_unavailable': 4}; amend reasons {'refresh_expiry': 34}; 44.1 writes/h; latency create p50 34.0ms p95 37.2ms, amend p50 35.2ms p95 72.8ms, cancel p50 37.8ms p95 37.8ms
  - reject `create` http_503_service_unavailable: 503 POST /portfolio/events/orders: service_unavailable service_unavailable
  - reject `create` http_503_service_unavailable: 503 POST /portfolio/events/orders: service_unavailable service_unavailable
  - reject `create` http_503_service_unavailable: 503 POST /portfolio/events/orders: service_unavailable service_unavailable
  - reject `create` http_503_service_unavailable: 503 POST /portfolio/events/orders: service_unavailable service_unavailable
- Tape vs quotes: 0 prints through our quote, 0 at our level
- P&L: realized 0.0, unrealized 0.0 (mark 0.435), fees 0.0, **net 0.0**; exchange position -

### KXRAINSHARD2-26NOV05-CMH (illiquid)
_Will it rain in Columbus on Nov 5, 2026?_

- Selection: mid 0.5, spread 0.04, 24h vol 298.0, OI 5.0, depth +-5c 800.0, closes in 28.87 h, fee_type quadratic
- Time: quoting 0.4%, two-sided 0.4%; stood down: halted:reject_rate 99.6%
- Market: book two-sided 99.8%, avg spread 4.0c, top depth bid/ask 262.9/262.9, mid 0.5 -> 0.5 (range 0.5-0.52, 2 changes, realized vol 0.0283/sqrt h), 30 public prints / 30.0 contracts
- Quotes: quoted spread 2.0c (model 2.0c); bid at touch 100.0% (inside 100.0%, - ticks behind otherwise); ask at touch 100.0% (inside 100.0%, - ticks behind); crossed->clamped 0.0%; risk-gated 0.0%
- Model: sigma 0.0375 (raw 0.15 x vol scale 0.25; from data 0.0% of the time), k 50.0, gamma 0.2; width terms inventory 0.651c / liquidity 1.349c / premium 0.0c (shares 32.6%/67.4%/0.0%); floor binding 0.0%; reservation skew 0c avg, corr with q -
- Inventory: start 0.0, final 0.0, max |q| 0.0, avg 0.0 (avg |q| 0.0), at limit 0.0%
- Fills: 0 (0 ct: bought 0, sold 0, round-trip 0), maker -, 0.0/quoting-hour, market share 0.0%; edge at fill -; markouts: 10s - (adverse -, n=0), 60s - (adverse -, n=0), 300s - (adverse -, n=0)
- Orders: {'create_failed': 12} rejects {'insufficient_balance': 12}; amend reasons -; 12.0 writes/h; latency create p50 36.7ms p95 54.4ms
  - reject `create` insufficient_balance: 404 POST /portfolio/events/orders: insufficient_shard_balance insufficient shard balance
  - reject `create` insufficient_balance: 404 POST /portfolio/events/orders: insufficient_shard_balance insufficient shard balance
  - reject `create` insufficient_balance: 404 POST /portfolio/events/orders: insufficient_shard_balance insufficient shard balance
  - reject `create` insufficient_balance: 404 POST /portfolio/events/orders: insufficient_shard_balance insufficient shard balance
  - reject `create` insufficient_balance: 404 POST /portfolio/events/orders: insufficient_shard_balance insufficient shard balance
- Tape vs quotes: 0 prints through our quote, 0 at our level
- P&L: realized 0.0, unrealized 0.0 (mark 0.5), fees 0.0, **net 0.0**; exchange position -

### KXRAINSHARD2-26NOV05-TPA (illiquid)
_Will it rain in Tampa on Nov 5, 2026?_

- Selection: mid 0.5, spread 0.04, 24h vol 322.0, OI 8.0, depth +-5c 800.0, closes in 28.87 h, fee_type quadratic
- Time: quoting 0.4%, two-sided 0.4%; stood down: halted:reject_rate 99.6%
- Market: book two-sided 99.8%, avg spread 4.0c, top depth bid/ask 265.1/265.1, mid 0.5 -> 0.5 (range 0.5-0.52, 2 changes, realized vol 0.0283/sqrt h), 24 public prints / 24.0 contracts
- Quotes: quoted spread 2.0c (model 2.0c); bid at touch 100.0% (inside 100.0%, - ticks behind otherwise); ask at touch 100.0% (inside 100.0%, - ticks behind); crossed->clamped 0.0%; risk-gated 0.0%
- Model: sigma 0.0375 (raw 0.15 x vol scale 0.25; from data 0.0% of the time), k 50.0, gamma 0.2; width terms inventory 0.654c / liquidity 1.346c / premium 0.0c (shares 32.7%/67.3%/0.0%); floor binding 0.0%; reservation skew 0c avg, corr with q -
- Inventory: start 0.0, final 0.0, max |q| 0.0, avg 0.0 (avg |q| 0.0), at limit 0.0%
- Fills: 0 (0 ct: bought 0, sold 0, round-trip 0), maker -, 0.0/quoting-hour, market share 0.0%; edge at fill -; markouts: 10s - (adverse -, n=0), 60s - (adverse -, n=0), 300s - (adverse -, n=0)
- Orders: {'create_failed': 12} rejects {'insufficient_balance': 12}; amend reasons -; 12.0 writes/h; latency create p50 35.7ms p95 47.3ms
  - reject `create` insufficient_balance: 404 POST /portfolio/events/orders: insufficient_shard_balance insufficient shard balance
  - reject `create` insufficient_balance: 404 POST /portfolio/events/orders: insufficient_shard_balance insufficient shard balance
  - reject `create` insufficient_balance: 404 POST /portfolio/events/orders: insufficient_shard_balance insufficient shard balance
  - reject `create` insufficient_balance: 404 POST /portfolio/events/orders: insufficient_shard_balance insufficient shard balance
  - reject `create` insufficient_balance: 404 POST /portfolio/events/orders: insufficient_shard_balance insufficient shard balance
- Tape vs quotes: 0 prints through our quote, 0 at our level
- P&L: realized 0.0, unrealized 0.0 (mark 0.5), fees 0.0, **net 0.0**; exchange position -

### KXNFLTOTAL-26NOV09BUFMIN-53 (illiquid)
_Full Game: over 52.5 points scored?_

- Selection: mid 0.285, spread 0.03, 24h vol 378.78, OI 378.78, depth +-5c 7024.14, closes in 169.12 h, fee_type quadratic
- Time: quoting 100.0%, two-sided 100.0%; stood down: never
- Market: book two-sided 100.0%, avg spread 3.0c, top depth bid/ask 1234.6/1057.3, mid 0.285 -> 0.285 (range 0.285-0.285, 0 changes, realized vol 0.0/sqrt h), 0 public prints / 0 contracts
- Quotes: quoted spread 1.0c (model 1.0c); bid at touch 100.0% (inside 100.0%, - ticks behind otherwise); ask at touch 100.0% (inside 100.0%, - ticks behind); crossed->clamped 0.0%; risk-gated 0.0%
- Model: sigma 0.02845 (raw 0.13963 x vol scale 0.204; from data 66.6% of the time), k 66.67, gamma 0.2; width terms inventory 0.457c / liquidity 0.543c / premium 0.0c (shares 45.7%/54.3%/0.0%); floor binding 0.0%; reservation skew 0c avg, corr with q -
- Inventory: start 0.0, final 0.0, max |q| 0.0, avg 0.0 (avg |q| 0.0), at limit 0.0%
- Fills: 0 (0 ct: bought 0, sold 0, round-trip 0), maker -, 0.0/quoting-hour, market share -; edge at fill -; markouts: 10s - (adverse -, n=0), 60s - (adverse -, n=0), 300s - (adverse -, n=0)
- Orders: {'create': 4, 'amend': 34, 'create_failed': 4, 'cancel': 2} rejects {'http_503_service_unavailable': 4}; amend reasons {'refresh_expiry': 34}; 44.1 writes/h; latency create p50 34.7ms p95 36.9ms, amend p50 35.0ms p95 163.8ms, cancel p50 41.7ms p95 41.7ms
  - reject `create` http_503_service_unavailable: 503 POST /portfolio/events/orders: service_unavailable service_unavailable
  - reject `create` http_503_service_unavailable: 503 POST /portfolio/events/orders: service_unavailable service_unavailable
  - reject `create` http_503_service_unavailable: 503 POST /portfolio/events/orders: service_unavailable service_unavailable
  - reject `create` http_503_service_unavailable: 503 POST /portfolio/events/orders: service_unavailable service_unavailable
- Tape vs quotes: 0 prints through our quote, 0 at our level
- P&L: realized 0.0, unrealized 0.0 (mark 0.285), fees 0.0, **net 0.0**; exchange position -

## Infrastructure

- Cycle time p50 159.5 ms, p95 395.1 ms, overrun 0.8%; cycle errors 1; position mismatches 0
- API: 9814 requests, 48 errors, 7 retries, 0.0 s spent pacing

| endpoint | count | errors | p50 ms | p95 ms | max ms |
|---|---|---|---|---|---|
| DELETE /portfolio/events/orders/{x} | 12 | - | 35.5 | 41.6 | 41.6 |
| GET /account/limits | 1 | - | 131.9 | 131.9 | 131.9 |
| GET /exchange/status | 127 | {'503': 8} | 35.2 | 49.7 | 301.0 |
| GET /markets | 120 | - | 37.1 | 45.7 | 673.9 |
| GET /markets/orderbooks | 1781 | - | 35.3 | 45.3 | 402.7 |
| GET /markets/trades | 2160 | - | 35.3 | 44.1 | 2148.4 |
| GET /portfolio/balance | 61 | - | 36.6 | 48.3 | 78.0 |
| GET /portfolio/fills | 1781 | - | 36.2 | 44.0 | 2930.5 |
| GET /portfolio/orders | 1783 | - | 36.0 | 42.2 | 386.8 |
| GET /portfolio/positions | 1782 | - | 37.2 | 45.8 | 3103.7 |
| GET /series/{x} | 6 | - | 33.9 | 34.5 | 34.5 |
| POST /portfolio/events/orders | 65 | {'404': 24, '503': 16} | 34.7 | 47.1 | 54.2 |
| POST /portfolio/events/orders/{x}/amend | 135 | - | 34.6 | 90.5 | 255.6 |

Risk events:
- market KXRAINSHARD2-26NOV05-CMH: reject_rate
- market KXRAINSHARD2-26NOV05-TPA: reject_rate

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
