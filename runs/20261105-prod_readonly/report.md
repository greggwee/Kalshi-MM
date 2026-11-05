# Market-making diagnostic report

- Run: `C:\Users\grego\AppData\Local\Temp\claude\840c593829146935\8552379b-feeb-4475-830b-aaa408586bd7\scratchpad\prod_ro`  
- Environment: **prod** (DRY RUN)  
- Duration: 242.4 s over 120 cycles  
- End reason: duration_reached  
- Orders left resting after shutdown: 0
- Balance: - -> - (portfolio value - -> -)

## Headline

**0 fills / 0 contracts** across 1 markets, **net P&L $+0.0000** after $0.0000 fees. Edge at fill -/contract, 60s markout -/contract.

## Liquid vs illiquid

| metric | liquid | all |
|---|---|---|
| markets | 1 | 1 |
| quoting % of time | 100.0 | 100.0 |
| two-sided % | 100.0 | 100.0 |
| market spread (c) | 1.0 | 1.0 |
| our quoted spread (c) | 1.0 | 1.0 |
| bid at touch % | 100.0 | 100.0 |
| ask at touch % | 100.0 | 100.0 |
| public contracts printed | 0 | 0 |
| fills | 0 | 0 |
| contracts filled | 0 | 0 |
| market share % | - | - |
| edge at fill (c/ct) | - | - |
| markout 10s (c/ct) | - | - |
| markout 60s (c/ct) | - | - |
| markout 300s (c/ct) | - | - |
| adverse sel. 10s (c/ct) | - | - |
| adverse sel. 60s (c/ct) | - | - |
| adverse sel. 300s (c/ct) | - | - |
| net P&L ($) | 0 | 0 |
| fees ($) | 0 | 0 |
| order writes | 8 | 8 |
| failed writes | 0 | 0 |
| max abs inventory | 0.0 | 0.0 |
| avg vol scale sqrt(p(1-p))/0.5 | 0.25 | 0.25 |
| min_spread floor binding % | 0.0 | 0.0 |

## Findings

- **MEDIUM** (KXHIGHNY-26NOV05-T70): [liquid] no fills. no public prints in the market during the session
- **INFO** (session): DRY RUN: no orders reached the exchange; fills and P&L are zero by construction. Quote placement metrics are still valid.

## Per-market detail

### KXHIGHNY-26NOV05-T70 (liquid)
_Will the maximum temperature be <70° on Nov 5, 2026?_

- Selection: mid 0.485, spread 0.01, 24h vol 2730.85, OI 2078.27, depth +-5c 1106.4299999999998, closes in 28.83 h, fee_type quadratic
- Time: quoting 100.0%, two-sided 100.0%; stood down: never
- Market: book two-sided 100.0%, avg spread 1.0c, top depth bid/ask 140.0/46.8, mid 0.485 -> 0.485 (range 0.485-0.485, 0 changes, realized vol 0.0/sqrt h), 0 public prints / 0 contracts
- Quotes: quoted spread 1.0c (model 1.0c); bid at touch 100.0% (inside 0.0%, - ticks behind otherwise); ask at touch 100.0% (inside 0.0%, - ticks behind); crossed->clamped 0.0%; risk-gated 0.0%
- Model: sigma 0.03928 (raw 0.15727 x vol scale 0.25; from data 0.0% of the time), k 197.7, gamma 0.2; width terms inventory 0.133c / liquidity 0.867c / premium 0.0c (shares 13.3%/86.7%/0.0%); floor binding 0.0%; reservation skew 0c avg, corr with q -
- Inventory: start 0.0, final 0.0, max |q| 0.0, avg 0.0 (avg |q| 0.0), at limit 0.0%
- Fills: 0 (0 ct: bought 0, sold 0, round-trip 0), maker -, 0.0/quoting-hour, market share -; edge at fill -; markouts: 10s - (adverse -, n=0), 60s - (adverse -, n=0), 300s - (adverse -, n=0)
- Orders: {'create': 2, 'amend': 4, 'cancel': 2} rejects none; amend reasons {'refresh_expiry': 4}; 119.9 writes/h; latency create p50 0.0ms p95 0.0ms, amend p50 0.0ms p95 0.0ms, cancel p50 0.0ms p95 0.0ms
- Tape vs quotes: 0 prints through our quote, 0 at our level
- P&L: realized 0.0, unrealized 0.0 (mark 0.485), fees 0.0, **net 0.0**; exchange position -

## Infrastructure

- Cycle time p50 67.5 ms, p95 261.3 ms, overrun 0.0%; cycle errors 0; position mismatches 0
- API: 166 requests, 0 errors, 0 retries, 0.0 s spent pacing

| endpoint | count | errors | p50 ms | p95 ms | max ms |
|---|---|---|---|---|---|
| GET /exchange/status | 8 | - | 63.4 | 66.2 | 66.2 |
| GET /markets | 9 | - | 66.6 | 220.9 | 220.9 |
| GET /markets/orderbooks | 122 | - | 63.9 | 67.5 | 75.2 |
| GET /markets/trades | 25 | - | 66.0 | 69.3 | 75.9 |
| GET /series/{x} | 2 | - | 61.5 | 61.5 | 61.5 |

## Configuration

```json
{
  "strategy": {
    "gamma": 1.0,
    "default_k": 40.0,
    "base_order_size": 1,
    "max_position": 5,
    "min_spread": 0.02,
    "premium_scale": 0.02,
    "min_time_to_resolution_h": 16,
    "min_quote_mid": 0.1,
    "max_quote_mid": 0.9,
    "max_horizon_h": 24.0,
    "gamma_mode": "constant",
    "calibration_constant": 0.04,
    "sigma_floor": 0.01,
    "vol_window": 120,
    "fair_value_adjustment": 0.0,
    "k_refresh_every": 10,
    "model": "pmm",
    "pmm": {
      "calibrated": true,
      "stand_down_h": 16,
      "maker_fee": 0.0,
      "gamma": 0.2,
      "gamma_T": 0.05,
      "min_offset": 0.005,
      "prior": {
        "T": 32.0,
        "sigma0": 0.15518895636583804,
        "sigma1": 0.04441032540569832,
        "sigma2": 0.0,
        "eta": 6.0,
        "A0": 7.390333744030485,
        "A1": 40.363656629197195,
        "xi": 8.0,
        "nu": 1.0,
        "k0": 195.0,
        "k1": 200.0,
        "kappa": 0.5
      }
    }
  },
  "strategy_overrides": {
    "liquid": {},
    "illiquid": {}
  },
  "execution": {
    "order_expiration_s": 120,
    "expiry_refresh_frac": 0.33,
    "replenish_threshold": 0.5,
    "cross_policy": "skip",
    "aggressive_reprice": "replace",
    "cancel_order_on_pause": true,
    "post_only": true,
    "flatten_on_exit": false,
    "listing_grace_s": 5.0
  },
  "risk": {
    "max_session_loss": 10.0,
    "max_market_loss": 5.0,
    "max_gross_position": 10,
    "max_consecutive_errors": 5,
    "min_balance": 0.0,
    "max_rejects_per_min": 6,
    "stale_book_s": 15.0
  }
}
```

Definitions: see the docstring of `src/diagnostics.py`. Markouts use the mid net of our own orders, sampled once per cycle.
