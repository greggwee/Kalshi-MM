# Market-making diagnostic report

- Run: `runs\20261027-164220_demo_dry`  
- Environment: **demo** (DRY RUN)  
- Duration: 602.8 s over 300 cycles  
- End reason: duration_reached  
- Orders left resting after shutdown: 0
- Balance: 2453.24 -> 2453.24 (portfolio value 0.0 -> 0.0)

## Headline

**0 fills / 0 contracts** across 6 markets, **net P&L $+0.0000** after $0.0000 fees. Edge at fill -/contract, 60s markout -/contract.

## Liquid vs illiquid

| metric | illiquid | liquid | all |
|---|---|---|---|
| markets | 3 | 3 | 6 |
| quoting % of time | 100.0 | 100.0 | 100.0 |
| two-sided % | 100.0 | 100.0 | 100.0 |
| market spread (c) | 12.42 | 1.33 | 6.88 |
| our quoted spread (c) | 18.45 | 14.77 | 16.61 |
| bid at touch % | 69.8 | 0.0 | 34.9 |
| ask at touch % | 69.9 | 0.0 | 34.9 |
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
| order writes | 179 | 36 | 215 |
| failed writes | 0 | 0 | 0 |
| max abs inventory | 0.0 | 0.0 | 0.0 |
| avg vol scale sqrt(p(1-p))/0.5 | 0.797 | 0.913 | 0.855 |
| min_spread floor binding % | 0.0 | 0.0 | 0.0 |

## Findings

- **MEDIUM** (KXNFLGAME-26NOV01TENCIN-TEN): [liquid] no fills. quotes rarely at touch (bid 0.0%, ask 0.0%); no public prints in the market during the session
- **MEDIUM** (KXNFLGAME-26NOV01TENCIN-TEN): [liquid] quoted 3.06c vs market 1.0c: the A-S width is not competitive here (k=75.21, gamma=1.0).
- **MEDIUM** (KXNFLGAME-26NOV02CHISEA-SEA): [liquid] no fills. quotes rarely at touch (bid 0.0%, ask 0.0%); no public prints in the market during the session
- **MEDIUM** (KXNFLGAME-26NOV02CHISEA-SEA): [liquid] quoted 4.12c vs market 2.0c: the A-S width is not competitive here (k=67.03, gamma=1.0).
- **MEDIUM** (KXPAYROLLS-26OCT-T70000): [liquid] no fills. quotes rarely at touch (bid 0.0%, ask 0.0%); no public prints in the market during the session
- **MEDIUM** (KXPAYROLLS-26OCT-T70000): [liquid] quoted 37.12c vs market 1.0c: the A-S width is not competitive here (k=5.0, gamma=1.0).
- **MEDIUM** (KXTRUMPMENTIONB-26OCT27-ILHA): [illiquid] no fills. quotes rarely at touch (bid 9.3%, ask 9.7%); no public prints in the market during the session
- **MEDIUM** (KXTRUMPMENTIONB-26OCT27-ILHA): [illiquid] quoted 41.22c vs market 9.27c: the A-S width is not competitive here (k=33.63, gamma=1.0).
- **MEDIUM** (KXCPI-26OCT-T0.2): [illiquid] no fills. no public prints in the market during the session
- **MEDIUM** (KXNFLSPREAD-26NOV01MINDET-MIN8): [illiquid] no fills. no public prints in the market during the session
- **INFO** (session): DRY RUN: no orders reached the exchange; fills and P&L are zero by construction. Quote placement metrics are still valid.

## Per-market detail

### KXNFLGAME-26NOV01TENCIN-TEN (liquid)
_Tennessee wins_

- Selection: mid 0.175, spread 0.01, 24h vol 495.46, OI 23905.15, depth +-5c 5562.9800000000005, closes in 169.3 h, fee_type quadratic
- Time: quoting 100.0%, two-sided 100.0%; stood down: never
- Market: book two-sided 100.0%, avg spread 1.0c, top depth bid/ask 980.4/719.3, mid 0.175 -> 0.175 (range 0.175-0.175, 0 changes, realized vol 0.0/sqrt h), 0 public prints / 0 contracts
- Quotes: quoted spread 3.06c (model 2.86c); bid at touch 0.0% (inside 0.0%, 1.03 ticks behind otherwise); ask at touch 0.0% (inside 0.0%, 1.03 ticks behind); crossed->clamped 0.0%; risk-gated 0.0%
- Model: sigma 0.00828 (raw 0.0109 x vol scale 0.76; from data 97.0% of the time), k 75.21, gamma 1.0; width terms inventory 0.201c / liquidity 2.642c / premium 0.022c (shares 7.0%/92.2%/0.8%); floor binding 0.0%; reservation skew 0c avg, corr with q -
- Inventory: start 0.0, final 0.0, max |q| 0.0, avg 0.0 (avg |q| 0.0), at limit 0.0%
- Fills: 0 (0 ct: bought 0, sold 0, round-trip 0), maker -, 0.0/quoting-hour, market share -; edge at fill -; markouts: 10s - (adverse -, n=0), 60s - (adverse -, n=0), 300s - (adverse -, n=0)
- Orders: {'create': 4, 'cancel': 4, 'amend': 4} rejects none; amend reasons {'refresh_expiry': 4}; 72.0 writes/h; latency create p50 0.0ms p95 0.0ms, cancel p50 0.0ms p95 0.0ms, amend p50 0.0ms p95 0.0ms
- Tape vs quotes: 0 prints through our quote, 0 at our level
- P&L: realized 0.0, unrealized 0.0 (mark 0.175), fees 0.0, **net 0.0**; exchange position -

### KXNFLGAME-26NOV02CHISEA-SEA (liquid)
_Seattle wins_

- Selection: mid 0.6, spread 0.02, 24h vol 861.31, OI 6521.33, depth +-5c 1376.41, closes in 200.55 h, fee_type quadratic
- Time: quoting 100.0%, two-sided 100.0%; stood down: never
- Market: book two-sided 100.0%, avg spread 2.0c, top depth bid/ask 283.5/272.2, mid 0.6 -> 0.6 (range 0.6-0.6, 0 changes, realized vol 0.0/sqrt h), 0 public prints / 0 contracts
- Quotes: quoted spread 4.12c (model 3.33c); bid at touch 0.0% (inside 0.0%, 1.06 ticks behind otherwise); ask at touch 0.0% (inside 0.0%, 1.06 ticks behind); crossed->clamped 0.0%; risk-gated 0.0%
- Model: sigma 0.01068 (raw 0.0109 x vol scale 0.98; from data 97.0% of the time), k 67.03, gamma 1.0; width terms inventory 0.334c / liquidity 2.962c / premium 0.034c (shares 10.0%/88.9%/1.0%); floor binding 0.0%; reservation skew 0c avg, corr with q -
- Inventory: start 0.0, final 0.0, max |q| 0.0, avg 0.0 (avg |q| 0.0), at limit 0.0%
- Fills: 0 (0 ct: bought 0, sold 0, round-trip 0), maker -, 0.0/quoting-hour, market share -; edge at fill -; markouts: 10s - (adverse -, n=0), 60s - (adverse -, n=0), 300s - (adverse -, n=0)
- Orders: {'create': 4, 'cancel': 4, 'amend': 4} rejects none; amend reasons {'refresh_expiry': 4}; 72.0 writes/h; latency create p50 0.0ms p95 0.0ms, cancel p50 0.0ms p95 0.0ms, amend p50 0.0ms p95 0.0ms
- Tape vs quotes: 0 prints through our quote, 0 at our level
- P&L: realized 0.0, unrealized 0.0 (mark 0.6), fees 0.0, **net 0.0**; exchange position -

### KXPAYROLLS-26OCT-T70000 (liquid)
_Will above 70000 jobs be added in October 2026?_

- Selection: mid 0.515, spread 0.01, 24h vol 75.39, OI 257308.9, depth +-5c 1942.86, closes in 236.79 h, fee_type quadratic
- Time: quoting 100.0%, two-sided 100.0%; stood down: never
- Market: book two-sided 100.0%, avg spread 1.0c, top depth bid/ask 326.8/319.5, mid 0.515 -> 0.515 (range 0.515-0.515, 0 changes, realized vol 0.0/sqrt h), 0 public prints / 0 contracts
- Quotes: quoted spread 37.12c (model 36.84c); bid at touch 0.0% (inside 0.0%, 18.06 ticks behind otherwise); ask at touch 0.0% (inside 0.0%, 18.06 ticks behind); crossed->clamped 0.0%; risk-gated 0.0%
- Model: sigma 0.01089 (raw 0.0109 x vol scale 1.0; from data 97.0% of the time), k 5.0, gamma 1.0; width terms inventory 0.348c / liquidity 36.464c / premium 0.033c (shares 0.9%/99.0%/0.1%); floor binding 0.0%; reservation skew 0c avg, corr with q -
- Inventory: start 0.0, final 0.0, max |q| 0.0, avg 0.0 (avg |q| 0.0), at limit 0.0%
- Fills: 0 (0 ct: bought 0, sold 0, round-trip 0), maker -, 0.0/quoting-hour, market share -; edge at fill -; markouts: 10s - (adverse -, n=0), 60s - (adverse -, n=0), 300s - (adverse -, n=0)
- Orders: {'create': 4, 'cancel': 4, 'amend': 4} rejects none; amend reasons {'refresh_expiry': 4}; 72.0 writes/h; latency create p50 0.0ms p95 0.0ms, cancel p50 0.0ms p95 0.0ms, amend p50 0.0ms p95 0.0ms
- Tape vs quotes: 0 prints through our quote, 0 at our level
- P&L: realized 0.0, unrealized 0.0 (mark 0.515), fees 0.0, **net 0.0**; exchange position -

### KXTRUMPMENTIONB-26OCT27-ILHA (illiquid)
_What will Donald Trump say during midterm rally in Waco, Texas originally scheduled for October 27, 2026?_

- Selection: mid 0.865, spread 0.13, 24h vol 18.0, OI 11.0, depth +-5c 0, closes in 334.3 h, fee_type quadratic
- Time: quoting 100.0%, two-sided 100.0%; stood down: never
- Market: book two-sided 99.7%, avg spread 9.27c, top depth bid/ask 21.8/25.0, mid 0.86 -> 0.875 (range 0.82-0.875, 17 changes, realized vol 0.1704/sqrt h), 0 public prints / 0 contracts
- Quotes: quoted spread 41.22c (model 70.0c); bid at touch 9.3% (inside 9.3%, 29.65 ticks behind otherwise); ask at touch 9.7% (inside 9.7%, 5.87 ticks behind); crossed->clamped 0.0%; risk-gated 0.0%
- Model: sigma 0.12005 (raw 0.17133 x vol scale 0.689; from data 97.0% of the time), k 33.63, gamma 1.0; width terms inventory 60.249c / liquidity 9.741c / premium 0.013c (shares 86.1%/13.9%/0.0%); floor binding 0.0%; reservation skew 0c avg, corr with q -
- Inventory: start 0.0, final 0.0, max |q| 0.0, avg 0.0 (avg |q| 0.0), at limit 0.0%
- Fills: 0 (0 ct: bought 0, sold 0, round-trip 0), maker -, 0.0/quoting-hour, market share -; edge at fill -; markouts: 10s - (adverse -, n=0), 60s - (adverse -, n=0), 300s - (adverse -, n=0)
- Orders: {'create': 67, 'cancel': 67, 'amend': 21} rejects none; amend reasons {'reprice': 20, 'refresh_expiry': 1}; 929.6 writes/h; latency create p50 0.0ms p95 0.0ms, cancel p50 0.0ms p95 0.0ms, amend p50 0.0ms p95 0.0ms
- Tape vs quotes: 0 prints through our quote, 0 at our level
- P&L: realized 0.0, unrealized 0.0 (mark 0.875), fees 0.0, **net 0.0**; exchange position -

### KXCPI-26OCT-T0.2 (illiquid)
_Will CPI rise more than 0.2% in October 2026?_

- Selection: mid 0.605, spread 0.11, 24h vol 5.0, OI 52974.04, depth +-5c 0, closes in 332.72 h, fee_type quadratic
- Time: quoting 100.0%, two-sided 100.0%; stood down: never
- Market: book two-sided 100.0%, avg spread 11.0c, top depth bid/ask 5.0/1.0, mid 0.605 -> 0.605 (range 0.605-0.605, 0 changes, realized vol 0.0/sqrt h), 0 public prints / 0 contracts
- Quotes: quoted spread 7.06c (model 5.3c); bid at touch 100.0% (inside 100.0%, - ticks behind otherwise); ask at touch 100.0% (inside 100.0%, - ticks behind); crossed->clamped 0.0%; risk-gated 0.0%
- Model: sigma 0.01066 (raw 0.0109 x vol scale 0.978; from data 97.0% of the time), k 40.0, gamma 1.0; width terms inventory 0.333c / liquidity 4.938c / premium 0.026c (shares 6.3%/93.2%/0.5%); floor binding 0.0%; reservation skew 0c avg, corr with q -
- Inventory: start 0.0, final 0.0, max |q| 0.0, avg 0.0 (avg |q| 0.0), at limit 0.0%
- Fills: 0 (0 ct: bought 0, sold 0, round-trip 0), maker -, 0.0/quoting-hour, market share -; edge at fill -; markouts: 10s - (adverse -, n=0), 60s - (adverse -, n=0), 300s - (adverse -, n=0)
- Orders: {'create': 4, 'cancel': 4, 'amend': 4} rejects none; amend reasons {'refresh_expiry': 4}; 72.0 writes/h; latency create p50 0.0ms p95 0.0ms, cancel p50 0.0ms p95 0.0ms, amend p50 0.0ms p95 0.0ms
- Tape vs quotes: 0 prints through our quote, 0 at our level
- P&L: realized 0.0, unrealized 0.0 (mark 0.605), fees 0.0, **net 0.0**; exchange position -

### KXNFLSPREAD-26NOV01MINDET-MIN8 (illiquid)
_Minnesota wins by over 7.5 points?_

- Selection: mid 0.155, spread 0.17, 24h vol 50.0, OI 50.0, depth +-5c 0, closes in 169.3 h, fee_type quadratic
- Time: quoting 100.0%, two-sided 100.0%; stood down: never
- Market: book two-sided 100.0%, avg spread 17.0c, top depth bid/ask 2380.9/694.4, mid 0.155 -> 0.155 (range 0.155-0.155, 0 changes, realized vol 0.0/sqrt h), 0 public prints / 0 contracts
- Quotes: quoted spread 7.06c (model 6.67c); bid at touch 100.0% (inside 100.0%, - ticks behind otherwise); ask at touch 100.0% (inside 100.0%, - ticks behind); crossed->clamped 0.0%; risk-gated 0.0%
- Model: sigma 0.00789 (raw 0.0109 x vol scale 0.724; from data 97.0% of the time), k 30.42, gamma 1.0; width terms inventory 0.182c / liquidity 6.469c / premium 0.02c (shares 2.7%/97.0%/0.3%); floor binding 0.0%; reservation skew 0c avg, corr with q -
- Inventory: start 0.0, final 0.0, max |q| 0.0, avg 0.0 (avg |q| 0.0), at limit 0.0%
- Fills: 0 (0 ct: bought 0, sold 0, round-trip 0), maker -, 0.0/quoting-hour, market share -; edge at fill -; markouts: 10s - (adverse -, n=0), 60s - (adverse -, n=0), 300s - (adverse -, n=0)
- Orders: {'create': 4, 'cancel': 4, 'amend': 4} rejects none; amend reasons {'refresh_expiry': 4}; 72.0 writes/h; latency create p50 0.0ms p95 0.0ms, cancel p50 0.0ms p95 0.0ms, amend p50 0.0ms p95 0.0ms
- Tape vs quotes: 0 prints through our quote, 0 at our level
- P&L: realized 0.0, unrealized 0.0 (mark 0.155), fees 0.0, **net 0.0**; exchange position -

## Infrastructure

- Cycle time p50 115.6 ms, p95 952.1 ms, overrun 0.0%; cycle errors 0; position mismatches 0
- API: 713 requests, 0 errors, 0 retries, 0.0 s spent pacing

| endpoint | count | errors | p50 ms | p95 ms | max ms |
|---|---|---|---|---|---|
| GET /account/limits | 1 | - | 597.9 | 597.9 | 597.9 |
| GET /exchange/status | 20 | - | 102.0 | 176.4 | 176.4 |
| GET /markets | 20 | - | 104.1 | 144.3 | 144.3 |
| GET /markets/orderbooks | 301 | - | 108.1 | 176.8 | 643.5 |
| GET /markets/trades | 360 | - | 105.7 | 154.1 | 440.7 |
| GET /portfolio/balance | 11 | - | 105.4 | 321.4 | 321.4 |

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
