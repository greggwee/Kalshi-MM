# Market-making diagnostic report

- Run: `runs\20261105-000857_demo`  
- Environment: **demo**  
- Duration: 3403.9 s over 1684 cycles  
- End reason: duration_reached  
- Orders left resting after shutdown: 0
- Balance: 2453.2097 -> 2449.9495 (portfolio value 0.0 -> 2.72)

## Headline

**5 fills / 8.16 contracts** across 4 markets, **net P&L $-0.1490** after $0.0000 fees. Edge at fill 1.623c/contract, 60s markout -0.253c/contract.

## Liquid vs illiquid

| metric | illiquid | all |
|---|---|---|
| markets | 4 | 4 |
| quoting % of time | 56.5 | 56.5 |
| two-sided % | 56.5 | 56.5 |
| market spread (c) | 4.76 | 4.76 |
| our quoted spread (c) | 3.12 | 3.12 |
| bid at touch % | 99.5 | 99.5 |
| ask at touch % | 54.2 | 54.2 |
| public contracts printed | 31.06 | 31.06 |
| fills | 5 | 5 |
| contracts filled | 8.16 | 8.16 |
| market share % | 26.3 | 26.3 |
| edge at fill (c/ct) | 1.623 | 1.623 |
| markout 10s (c/ct) | 0.152 | 0.152 |
| markout 60s (c/ct) | -0.253 | -0.253 |
| markout 300s (c/ct) | -2.656 | -2.656 |
| adverse sel. 10s (c/ct) | -1.471 | -1.471 |
| adverse sel. 60s (c/ct) | -1.875 | -1.875 |
| adverse sel. 300s (c/ct) | -4.278 | -4.278 |
| net P&L ($) | -0.149 | -0.149 |
| fees ($) | 0 | 0 |
| order writes | 225 | 225 |
| failed writes | 6 | 6 |
| max abs inventory | 6.16 | 6.16 |
| avg vol scale sqrt(p(1-p))/0.5 | 0.172 | 0.172 |
| min_spread floor binding % | 0.0 | 0.0 |

## Findings

- **HIGH** (KXATPCHALLENGERMATCH-26NOV04RATZAR-RAT): [illiquid] quoted only 0.0% of the time; main stand-down reason: no_two_sided_book (91.1%).
- **HIGH** (KXNCAAFTOTAL-26NOV04TOLSAC-35): [illiquid] quoted only 26.9% of the time; main stand-down reason: mid_out_of_band (56.1%).
- **MEDIUM** (KXNFLTOTAL-26NOV08TBCHI-45): [illiquid] no fills. no public prints in the market during the session
- **MEDIUM** (KXNFLTOTAL-26NOV08TBCHI-45): [illiquid] 6/42 order writes failed: {'http_503_service_unavailable': 6}.
- **INFO** (KXHIGHTOKC-26NOV05-T67): [illiquid] 1 public prints traded through our quoted price without filling us (approximate: quote state sampled once per cycle).

## Per-market detail

### KXATPCHALLENGERMATCH-26NOV04RATZAR-RAT (illiquid)
_Lucio Ratti wins_

- Selection: mid 0.14, spread -, 24h vol 67.39, OI 63.39, depth +-5c 0.0, closes in 333.85 h, fee_type quadratic
- Time: quoting 0.0%, two-sided 0.0%; stood down: no_two_sided_book 91.1%, market_status:finalized 7.1%, market_status:determined 1.8%
- Market: book two-sided 0.0%, avg spread -, top depth bid/ask -/-, mid 0.14 -> 0.14 (range 0.14-0.14, 0 changes, realized vol 0.0/sqrt h), 0 public prints / 0 contracts
- Quotes: quoted spread - (model -); bid at touch - (inside -, - ticks behind otherwise); ask at touch - (inside -, - ticks behind); crossed->clamped -; risk-gated -
- Model: sigma - (raw - x vol scale -; from data - of the time), k -, gamma -; width terms inventory - / liquidity - / premium - (shares -/-/-); floor binding -; reservation skew - avg, corr with q -
- Inventory: start 0.0, final 0.0, max |q| 0.0, avg 0.0 (avg |q| 0.0), at limit 0.0%
- Fills: 0 (0 ct: bought 0, sold 0, round-trip 0), maker -, -/quoting-hour, market share -; edge at fill -; markouts: 10s - (adverse -, n=0), 60s - (adverse -, n=0), 300s - (adverse -, n=0)
- Orders: {} rejects none; amend reasons -; 0.0 writes/h; latency 
- Tape vs quotes: 0 prints through our quote, 0 at our level
- P&L: realized 0.0, unrealized 0.0 (mark 0.14), fees 0.0, **net 0.0**; exchange position -

### KXHIGHTOKC-26NOV05-T67 (illiquid)
_Will the maximum temperature be >67° on Nov 5, 2026?_

- Selection: mid 0.215, spread 0.09, 24h vol 25.5, OI 20.0, depth +-5c 10.0, closes in 30.85 h, fee_type quadratic
- Time: quoting 99.2%, two-sided 99.2%; stood down: no_two_sided_book 0.8%
- Market: book two-sided 99.2%, avg spread 6.2c, top depth bid/ask 4.6/6.1, mid 0.215 -> 0.32 (range 0.195-0.32, 13 changes, realized vol 0.1293/sqrt h), 2 public prints / 16.0 contracts
- Quotes: quoted spread 4.52c (model 4.52c); bid at touch 98.4% (inside 84.1%, 3.0 ticks behind otherwise); ask at touch 21.0% (inside 21.0%, 1.47 ticks behind); crossed->clamped 0.0%; risk-gated 0.0%
- Model: sigma 0.069 (raw 0.32732 x vol scale 0.208; from data 64.3% of the time), k 56.65, gamma 0.2; width terms inventory 0.808c / liquidity 3.715c / premium 0.0c (shares 17.9%/82.1%/0.0%); floor binding 0.0%; reservation skew 0.747c avg, corr with q -0.683
- Inventory: start 0.0, final -2.0, max |q| 2.0, avg -1.793 (avg |q| 1.793), at limit 0.0%
- Fills: 1 (2.0 ct: bought 0, sold 2.0, round-trip 0), maker 100.0%, 1.07/quoting-hour, market share 12.5%; edge at fill 2.0c; markouts: 10s -4.0c (adverse -6.0c, n=1), 60s -4.0c (adverse -6.0c, n=1), 300s -6.0c (adverse -8.0c, n=1)
- Orders: {'create': 26, 'amend': 38, 'cancel': 23} rejects none; amend reasons {'reprice': 15, 'refresh_expiry': 23}; 92.2 writes/h; latency create p50 35.8ms p95 40.1ms, amend p50 35.0ms p95 65.3ms, cancel p50 35.2ms p95 38.2ms
- Tape vs quotes: 1 prints through our quote, 1 at our level
- P&L: realized 0.0, unrealized -0.14 (mark 0.32), fees 0.0, **net -0.14**; exchange position -2.0

### KXNCAAFTOTAL-26NOV04TOLSAC-35 (illiquid)
_Full Game: Over 34.5 points scored_

- Selection: mid 0.88, spread 0.06, 24h vol 55.93, OI 58.61, depth +-5c 99.09, closes in 47.85 h, fee_type quadratic
- Time: quoting 26.9%, two-sided 26.9%; stood down: mid_out_of_band 56.1%, no_two_sided_book 17.0%
- Market: book two-sided 34.4%, avg spread 5.08c, top depth bid/ask 50.0/54.3, mid 0.88 -> 0.93 (range 0.86-0.98, 55 changes, realized vol 0.1989/sqrt h), 12 public prints / 15.06 contracts
- Quotes: quoted spread 3.84c (model 3.84c); bid at touch 100.0% (inside 100.0%, - ticks behind otherwise); ask at touch 41.5% (inside 35.8%, 1.27 ticks behind); crossed->clamped 0.0%; risk-gated 0.0%
- Model: sigma 0.031 (raw 0.49717 x vol scale 0.066; from data 27.1% of the time), k 49.6, gamma 0.2; width terms inventory 0.686c / liquidity 3.154c / premium 0.0c (shares 17.9%/82.1%/0.0%); floor binding 0.0%; reservation skew 1.453c avg, corr with q -0.921
- Inventory: start 0.0, final -6.16, max |q| 6.16, avg -5.221 (avg |q| 5.221), at limit 0.0%
- Fills: 4 (6.16 ct: bought 0, sold 6.16, round-trip 0), maker 100.0%, 15.75/quoting-hour, market share 40.9%; edge at fill 1.5c; markouts: 10s 1.5c (adverse 0.0c, n=4), 60s 0.964c (adverse -0.536c, n=4), 300s -1.57c (adverse -3.07c, n=4)
- Orders: {'create': 43, 'cancel': 43, 'amend': 10} rejects none; amend reasons {'reprice': 9, 'replenish': 1}; 101.8 writes/h; latency create p50 35.9ms p95 46.5ms, cancel p50 35.8ms p95 52.0ms, amend p50 36.9ms p95 47.6ms
- Tape vs quotes: 0 prints through our quote, 4 at our level
- P&L: realized 0.0, unrealized -0.009 (mark 0.93), fees 0.0, **net -0.009**; exchange position -6.16

### KXNFLTOTAL-26NOV08TBCHI-45 (illiquid)
_Full Game: over 44.5 points scored?_

- Selection: mid 0.585, spread 0.03, 24h vol 385.39, OI 333.23, depth +-5c 4794.94, closes in 145.19 h, fee_type quadratic
- Time: quoting 100.0%, two-sided 100.0%; stood down: never
- Market: book two-sided 100.0%, avg spread 3.0c, top depth bid/ask 585.8/781.1, mid 0.585 -> 0.585 (range 0.585-0.585, 0 changes, realized vol 0.0/sqrt h), 0 public prints / 0 contracts
- Quotes: quoted spread 1.0c (model 1.0c); bid at touch 100.0% (inside 100.0%, - ticks behind otherwise); ask at touch 100.0% (inside 100.0%, - ticks behind); crossed->clamped 0.0%; risk-gated 0.0%
- Model: sigma 0.03423 (raw 0.141 x vol scale 0.243; from data 64.6% of the time), k 66.67, gamma 0.2; width terms inventory 0.542c / liquidity 0.458c / premium 0.0c (shares 54.2%/45.8%/0.0%); floor binding 0.0%; reservation skew 0c avg, corr with q -
- Inventory: start 0.0, final 0.0, max |q| 0.0, avg 0.0 (avg |q| 0.0), at limit 0.0%
- Fills: 0 (0 ct: bought 0, sold 0, round-trip 0), maker -, 0.0/quoting-hour, market share -; edge at fill -; markouts: 10s - (adverse -, n=0), 60s - (adverse -, n=0), 300s - (adverse -, n=0)
- Orders: {'create': 4, 'amend': 30, 'create_failed': 6, 'cancel': 2} rejects {'http_503_service_unavailable': 6}; amend reasons {'refresh_expiry': 30}; 44.5 writes/h; latency create p50 36.1ms p95 64.6ms, amend p50 35.2ms p95 38.6ms, cancel p50 35.7ms p95 35.7ms
  - reject `create` http_503_service_unavailable: 503 POST /portfolio/events/orders: service_unavailable service_unavailable
  - reject `create` http_503_service_unavailable: 503 POST /portfolio/events/orders: service_unavailable service_unavailable
  - reject `create` http_503_service_unavailable: 503 POST /portfolio/events/orders: service_unavailable service_unavailable
  - reject `create` http_503_service_unavailable: 503 POST /portfolio/events/orders: service_unavailable service_unavailable
  - reject `create` http_503_service_unavailable: 503 POST /portfolio/events/orders: service_unavailable service_unavailable
- Tape vs quotes: 0 prints through our quote, 0 at our level
- P&L: realized 0.0, unrealized 0.0 (mark 0.585), fees 0.0, **net 0.0**; exchange position -

## Infrastructure

- Cycle time p50 157.3 ms, p95 394.0 ms, overrun 0.7%; cycle errors 1; position mismatches 0
- API: 8621 requests, 11 errors, 4 retries, 0.0 s spent pacing

| endpoint | count | errors | p50 ms | p95 ms | max ms |
|---|---|---|---|---|---|
| DELETE /portfolio/events/orders/{x} | 68 | - | 35.2 | 39.7 | 77.0 |
| GET /account/limits | 1 | - | 137.8 | 137.8 | 137.8 |
| GET /exchange/status | 117 | {'503': 5} | 35.1 | 40.2 | 241.9 |
| GET /markets | 113 | - | 37.7 | 46.5 | 233.4 |
| GET /markets/orderbooks | 1685 | - | 35.5 | 44.1 | 783.4 |
| GET /markets/trades | 1360 | - | 35.8 | 44.7 | 2413.9 |
| GET /portfolio/balance | 58 | - | 36.9 | 46.4 | 89.1 |
| GET /portfolio/fills | 1685 | - | 36.6 | 45.0 | 2749.0 |
| GET /portfolio/orders | 1687 | - | 36.6 | 42.9 | 314.2 |
| GET /portfolio/positions | 1686 | - | 37.7 | 44.7 | 4118.4 |
| GET /series/{x} | 4 | - | 36.2 | 39.0 | 39.0 |
| POST /portfolio/events/orders | 79 | {'503': 6} | 35.7 | 46.3 | 96.6 |
| POST /portfolio/events/orders/{x}/amend | 78 | - | 34.9 | 40.2 | 68.0 |

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
