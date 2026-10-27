# Market-making diagnostic report

- Run: `runs\20261027-165339_demo`  
- Environment: **demo**  
- Duration: 3603.9 s over 1772 cycles  
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
| market spread (c) | 8.34 | 1.33 | 4.84 |
| our quoted spread (c) | 14.98 | 15.95 | 15.47 |
| bid at touch % | 41.6 | 0.0 | 20.8 |
| ask at touch % | 42.0 | 0.0 | 21.0 |
| public contracts printed | 0 | 0 | 0 |
| fills | 0 | 0 | 0 |
| contracts filled | 0 | 0 | 0 |
| market share % | - | - | - |
| edge at fill (c/ct) | - | - | - |
| markout 10s (c/ct) | - | - | - |
| markout 60s (c/ct) | - | - | - |
| markout 300s (c/ct) | - | - | - |
| adverse sel. 10s (c/ct) | - | - | - |
| adverse sel. 60s (c/ct) | - | - | - |
| adverse sel. 300s (c/ct) | - | - | - |
| net P&L ($) | 0 | 0 | 0 |
| fees ($) | 0 | 0 | 0 |
| order writes | 618 | 144 | 762 |
| failed writes | 4 | 0 | 4 |
| max abs inventory | 0.0 | 0.0 | 0.0 |
| avg vol scale sqrt(p(1-p))/0.5 | 0.787 | 0.913 | 0.85 |
| min_spread floor binding % | 0.0 | 0.0 | 0.0 |

## Findings

- **MEDIUM** (KXNFLGAME-26NOV01TENCIN-TEN): [liquid] no fills. quotes rarely at touch (bid 0.0%, ask 0.0%); no public prints in the market during the session
- **MEDIUM** (KXNFLGAME-26NOV01TENCIN-TEN): [liquid] quoted 6.82c vs market 1.0c: the A-S width is not competitive here (k=67.6, gamma=1.0).
- **MEDIUM** (KXNFLGAME-26NOV02CHISEA-SEA): [liquid] no fills. quotes rarely at touch (bid 0.0%, ask 0.0%); no public prints in the market during the session
- **MEDIUM** (KXNFLGAME-26NOV02CHISEA-SEA): [liquid] quoted 4.02c vs market 2.0c: the A-S width is not competitive here (k=68.2, gamma=1.0).
- **MEDIUM** (KXPAYROLLS-26OCT-T70000): [liquid] no fills. quotes rarely at touch (bid 0.0%, ask 0.0%); no public prints in the market during the session
- **MEDIUM** (KXPAYROLLS-26OCT-T70000): [liquid] quoted 37.02c vs market 1.0c: the A-S width is not competitive here (k=5.0, gamma=1.0).
- **MEDIUM** (KXTRUMPMENTIONB-26OCT27-ILHA): [illiquid] no fills. no public prints in the market during the session
- **MEDIUM** (KXCPI-26OCT-T0.2): [illiquid] no fills. no public prints in the market during the session
- **MEDIUM** (KXNFLSPREAD-26NOV01MINDET-MIN8): [illiquid] no fills. no public prints in the market during the session

## Per-market detail

### KXNFLGAME-26NOV01TENCIN-TEN (liquid)
_Tennessee wins_

- Selection: mid 0.175, spread 0.01, 24h vol 495.46, OI 23905.15, depth +-5c 5562.9800000000005, closes in 169.3 h, fee_type quadratic
- Time: quoting 100.0%, two-sided 100.0%; stood down: never
- Market: book two-sided 100.0%, avg spread 1.0c, top depth bid/ask 979.4/718.3, mid 0.175 -> 0.175 (range 0.175-0.175, 0 changes, realized vol 0.0/sqrt h), 0 public prints / 0 contracts
- Quotes: quoted spread 6.82c (model 6.81c); bid at touch 0.0% (inside 0.0%, 2.78 ticks behind otherwise); ask at touch 0.0% (inside 0.0%, 3.02 ticks behind); crossed->clamped 0.0%; risk-gated 0.0%
- Model: sigma 0.00772 (raw 0.01016 x vol scale 0.76; from data 99.5% of the time), k 67.6, gamma 1.0; width terms inventory 0.149c / liquidity 6.642c / premium 0.022c (shares 2.2%/97.5%/0.3%); floor binding 0.0%; reservation skew 0c avg, corr with q -
- Inventory: start 0.0, final 0.0, max |q| 0.0, avg 0.0 (avg |q| 0.0), at limit 0.0%
- Fills: 0 (0 ct: bought 0, sold 0, round-trip 0), maker -, 0.0/quoting-hour, market share -; edge at fill -; markouts: 10s - (adverse -, n=0), 60s - (adverse -, n=0), 300s - (adverse -, n=0)
- Orders: {'create': 14, 'cancel': 12, 'amend': 34} rejects none; amend reasons {'refresh_expiry': 26, 'reprice': 8}; 60.6 writes/h; latency create p50 106.9ms p95 125.0ms, cancel p50 102.0ms p95 124.5ms, amend p50 99.0ms p95 190.3ms
- Tape vs quotes: 0 prints through our quote, 0 at our level
- P&L: realized 0.0, unrealized 0.0 (mark 0.175), fees 0.0, **net 0.0**; exchange position 0.0

### KXNFLGAME-26NOV02CHISEA-SEA (liquid)
_Seattle wins_

- Selection: mid 0.6, spread 0.02, 24h vol 861.31, OI 6521.33, depth +-5c 1376.41, closes in 200.55 h, fee_type quadratic
- Time: quoting 100.0%, two-sided 100.0%; stood down: never
- Market: book two-sided 100.0%, avg spread 2.0c, top depth bid/ask 283.5/272.2, mid 0.6 -> 0.6 (range 0.6-0.6, 0 changes, realized vol 0.0/sqrt h), 0 public prints / 0 contracts
- Quotes: quoted spread 4.02c (model 3.19c); bid at touch 0.0% (inside 0.0%, 1.01 ticks behind otherwise); ask at touch 0.0% (inside 0.0%, 1.01 ticks behind); crossed->clamped 0.0%; risk-gated 0.0%
- Model: sigma 0.00995 (raw 0.01016 x vol scale 0.98; from data 99.5% of the time), k 68.2, gamma 1.0; width terms inventory 0.248c / liquidity 2.912c / premium 0.034c (shares 7.8%/91.2%/1.1%); floor binding 0.0%; reservation skew 0c avg, corr with q -
- Inventory: start 0.0, final 0.0, max |q| 0.0, avg 0.0 (avg |q| 0.0), at limit 0.0%
- Fills: 0 (0 ct: bought 0, sold 0, round-trip 0), maker -, 0.0/quoting-hour, market share -; edge at fill -; markouts: 10s - (adverse -, n=0), 60s - (adverse -, n=0), 300s - (adverse -, n=0)
- Orders: {'create': 6, 'cancel': 4, 'amend': 32} rejects none; amend reasons {'refresh_expiry': 32}; 42.4 writes/h; latency create p50 106.3ms p95 123.4ms, cancel p50 105.0ms p95 109.5ms, amend p50 105.6ms p95 146.4ms
- Tape vs quotes: 0 prints through our quote, 0 at our level
- P&L: realized 0.0, unrealized 0.0 (mark 0.6), fees 0.0, **net 0.0**; exchange position -

### KXPAYROLLS-26OCT-T70000 (liquid)
_Will above 70000 jobs be added in October 2026?_

- Selection: mid 0.515, spread 0.01, 24h vol 75.39, OI 257308.9, depth +-5c 1942.86, closes in 236.79 h, fee_type quadratic
- Time: quoting 100.0%, two-sided 100.0%; stood down: never
- Market: book two-sided 100.0%, avg spread 1.0c, top depth bid/ask 326.8/319.5, mid 0.515 -> 0.515 (range 0.515-0.515, 0 changes, realized vol 0.0/sqrt h), 0 public prints / 0 contracts
- Quotes: quoted spread 37.02c (model 36.76c); bid at touch 0.0% (inside 0.0%, 18.01 ticks behind otherwise); ask at touch 0.0% (inside 0.0%, 18.01 ticks behind); crossed->clamped 0.0%; risk-gated 0.0%
- Model: sigma 0.01015 (raw 0.01016 x vol scale 0.999; from data 99.5% of the time), k 5.0, gamma 1.0; width terms inventory 0.258c / liquidity 36.464c / premium 0.032c (shares 0.7%/99.2%/0.1%); floor binding 0.0%; reservation skew 0c avg, corr with q -
- Inventory: start 0.0, final 0.0, max |q| 0.0, avg 0.0 (avg |q| 0.0), at limit 0.0%
- Fills: 0 (0 ct: bought 0, sold 0, round-trip 0), maker -, 0.0/quoting-hour, market share -; edge at fill -; markouts: 10s - (adverse -, n=0), 60s - (adverse -, n=0), 300s - (adverse -, n=0)
- Orders: {'create': 6, 'cancel': 4, 'amend': 32} rejects none; amend reasons {'refresh_expiry': 32}; 42.4 writes/h; latency create p50 122.1ms p95 154.2ms, cancel p50 118.8ms p95 123.6ms, amend p50 102.6ms p95 134.0ms
- Tape vs quotes: 0 prints through our quote, 0 at our level
- P&L: realized 0.0, unrealized 0.0 (mark 0.515), fees 0.0, **net 0.0**; exchange position -

### KXTRUMPMENTIONB-26OCT27-ILHA (illiquid)
_What will Donald Trump say during midterm rally in Waco, Texas originally scheduled for October 27, 2026?_

- Selection: mid 0.865, spread 0.13, 24h vol 18.0, OI 11.0, depth +-5c 0, closes in 334.3 h, fee_type quadratic
- Time: quoting 100.0%, two-sided 100.0%; stood down: never
- Market: book two-sided 96.5%, avg spread 6.7c, top depth bid/ask 18.6/25.0, mid 0.875 -> 0.85 (range 0.82-0.905, 38 changes, realized vol 0.1449/sqrt h), 0 public prints / 0 contracts
- Quotes: quoted spread 25.17c (model 34.86c); bid at touch 35.4% (inside 25.5%, 21.23 ticks behind otherwise); ask at touch 35.5% (inside 25.7%, 5.78 ticks behind); crossed->clamped 0.0%; risk-gated 0.0%
- Model: sigma 0.06245 (raw 0.09318 x vol scale 0.663; from data 99.5% of the time), k 49.57, gamma 1.0; width terms inventory 22.558c / liquidity 12.286c / premium 0.012c (shares 64.7%/35.2%/0.0%); floor binding 0.0%; reservation skew 0c avg, corr with q -
- Inventory: start 0.0, final 0.0, max |q| 0.0, avg 0.0 (avg |q| 0.0), at limit 0.0%
- Fills: 0 (0 ct: bought 0, sold 0, round-trip 0), maker -, 0.0/quoting-hour, market share -; edge at fill -; markouts: 10s - (adverse -, n=0), 60s - (adverse -, n=0), 300s - (adverse -, n=0)
- Orders: {'create': 94, 'cancel': 92, 'amend': 87, 'amend_failed': 2} rejects {'http_503_service_unavailable': 2}; amend reasons {'reprice': 84, 'refresh_expiry': 5}; 277.8 writes/h; latency create p50 102.8ms p95 143.7ms, cancel p50 103.1ms p95 175.3ms, amend p50 102.1ms p95 177.4ms
  - reject `amend` http_503_service_unavailable: 503 POST /portfolio/events/orders/01a17e62-93c0-7fe7-88d5-ea2d332b6624/amend: service_unavailable service_unavailable
  - reject `amend` http_503_service_unavailable: 503 POST /portfolio/events/orders/01a17e63-91a8-7083-9ccb-335ed57898cf/amend: service_unavailable service_unavailable
- Tape vs quotes: 0 prints through our quote, 0 at our level
- P&L: realized 0.0, unrealized 0.0 (mark 0.85), fees 0.0, **net 0.0**; exchange position -

### KXCPI-26OCT-T0.2 (illiquid)
_Will CPI rise more than 0.2% in October 2026?_

- Selection: mid 0.605, spread 0.11, 24h vol 5.0, OI 52974.04, depth +-5c 0, closes in 332.72 h, fee_type quadratic
- Time: quoting 100.0%, two-sided 100.0%; stood down: never
- Market: book two-sided 98.9%, avg spread 8.43c, top depth bid/ask 4.0/2.9, mid 0.605 -> 0.66 (range 0.6-0.66, 30 changes, realized vol 0.0763/sqrt h), 0 public prints / 0 contracts
- Quotes: quoted spread 13.67c (model 12.98c); bid at touch 33.9% (inside 30.5%, 4.94 ticks behind otherwise); ask at touch 35.0% (inside 31.6%, 4.24 ticks behind); crossed->clamped 0.0%; risk-gated 0.0%
- Model: sigma 0.0445 (raw 0.04569 x vol scale 0.975; from data 99.5% of the time), k 50.13, gamma 1.0; width terms inventory 7.72c / liquidity 5.237c / premium 0.026c (shares 59.5%/40.3%/0.2%); floor binding 0.0%; reservation skew 0c avg, corr with q -
- Inventory: start 0.0, final 0.0, max |q| 0.0, avg 0.0 (avg |q| 0.0), at limit 0.0%
- Fills: 0 (0 ct: bought 0, sold 0, round-trip 0), maker -, 0.0/quoting-hour, market share -; edge at fill -; markouts: 10s - (adverse -, n=0), 60s - (adverse -, n=0), 300s - (adverse -, n=0)
- Orders: {'create': 97, 'cancel': 95, 'amend': 81} rejects none; amend reasons {'reprice': 75, 'refresh_expiry': 6}; 275.8 writes/h; latency create p50 103.1ms p95 136.5ms, cancel p50 106.3ms p95 153.2ms, amend p50 100.0ms p95 127.3ms
- Tape vs quotes: 0 prints through our quote, 0 at our level
- P&L: realized 0.0, unrealized 0.0 (mark 0.66), fees 0.0, **net 0.0**; exchange position -

### KXNFLSPREAD-26NOV01MINDET-MIN8 (illiquid)
_Minnesota wins by over 7.5 points?_

- Selection: mid 0.155, spread 0.17, 24h vol 50.0, OI 50.0, depth +-5c 0, closes in 169.3 h, fee_type quadratic
- Time: quoting 100.0%, two-sided 100.0%; stood down: never
- Market: book two-sided 100.0%, avg spread 9.9c, top depth bid/ask 1348.3/409.0, mid 0.155 -> 0.155 (range 0.155-0.155, 0 changes, realized vol 0.0/sqrt h), 0 public prints / 0 contracts
- Quotes: quoted spread 6.11c (model 5.87c); bid at touch 55.6% (inside 55.6%, 2.03 ticks behind otherwise); ask at touch 55.6% (inside 55.6%, 2.03 ticks behind); crossed->clamped 0.0%; risk-gated 0.0%
- Model: sigma 0.00735 (raw 0.01016 x vol scale 0.724; from data 99.5% of the time), k 35.29, gamma 1.0; width terms inventory 0.135c / liquidity 5.713c / premium 0.02c (shares 2.3%/97.3%/0.3%); floor binding 0.0%; reservation skew 0c avg, corr with q -
- Inventory: start 0.0, final 0.0, max |q| 0.0, avg 0.0 (avg |q| 0.0), at limit 0.0%
- Fills: 0 (0 ct: bought 0, sold 0, round-trip 0), maker -, 0.0/quoting-hour, market share -; edge at fill -; markouts: 10s - (adverse -, n=0), 60s - (adverse -, n=0), 300s - (adverse -, n=0)
- Orders: {'create': 18, 'cancel': 16, 'amend': 34, 'amend_failed': 2} rejects {'http_503_service_unavailable': 1, 'not_found': 1}; amend reasons {'refresh_expiry': 24, 'reprice': 12}; 70.7 writes/h; latency create p50 104.2ms p95 161.6ms, cancel p50 102.5ms p95 139.1ms, amend p50 102.0ms p95 3955.6ms
  - reject `amend` http_503_service_unavailable: 503 POST /portfolio/events/orders/01a17e61-0d20-7bd1-9003-5dc084c699f8/amend: service_unavailable service_unavailable
  - reject `amend` not_found: 404 POST /portfolio/events/orders/01a17e61-1108-7da2-b821-90d7c7b0c9c0/amend: not_found not found
- Tape vs quotes: 0 prints through our quote, 0 at our level
- P&L: realized 0.0, unrealized 0.0 (mark 0.155), fees 0.0, **net 0.0**; exchange position -

## Infrastructure

- Cycle time p50 461.1 ms, p95 1481.2 ms, overrun 1.4%; cycle errors 1; position mismatches 0
- API: 10315 requests, 25 errors, 20 retries, 0.0 s spent pacing

| endpoint | count | errors | p50 ms | p95 ms | max ms |
|---|---|---|---|---|---|
| DELETE /portfolio/events/orders/{x} | 223 | - | 103.7 | 153.1 | 504.8 |
| GET /account/limits | 1 | - | 417.5 | 417.5 | 417.5 |
| GET /exchange/status | 125 | {'503': 6} | 104.5 | 161.2 | 397.3 |
| GET /markets | 120 | - | 106.6 | 185.3 | 409.9 |
| GET /markets/orderbooks | 1773 | - | 102.8 | 161.3 | 9745.6 |
| GET /markets/trades | 2136 | - | 102.0 | 153.1 | 567.9 |
| GET /portfolio/balance | 61 | - | 108.5 | 164.3 | 373.5 |
| GET /portfolio/fills | 1773 | - | 101.7 | 159.9 | 824.2 |
| GET /portfolio/orders | 1775 | - | 103.8 | 166.8 | 519.9 |
| GET /portfolio/positions | 1774 | - | 103.0 | 164.2 | 470.1 |
| POST /portfolio/events/orders | 235 | - | 103.1 | 143.2 | 192.4 |
| POST /portfolio/events/orders/{x}/amend | 319 | {'503': 18, '404': 1} | 101.2 | 153.8 | 600.8 |

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
    "min_time_to_resolution_h": 0.05,
    "min_quote_mid": 0.05,
    "max_quote_mid": 0.95,
    "max_horizon_h": 24.0,
    "gamma_mode": "constant",
    "calibration_constant": 0.04,
    "sigma_floor": 0.01,
    "vol_window": 120,
    "fair_value_adjustment": 0.0,
    "k_refresh_every": 10
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
