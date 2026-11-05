# Kalshi Market Maker: Avellaneda-Stoikov Implementation

A Python-based market making system for [Kalshi](https://kalshi.com) prediction markets, implementing the **Avellaneda-Stoikov (2008)** optimal market making framework.

## Overview

This repository demonstrates the practical application of the seminal [Avellaneda & Stoikov (2008) paper](https://www.math.nyu.edu/faculty/avellane/HighFrequencyTrading.pdf) "High-frequency trading in a limit order book" to prediction markets. The implementation provides a hands-on learning tool for understanding how theoretical market making models work in practice.

### What This Does

The system continuously quotes bid and ask prices on Kalshi prediction markets, dynamically adjusting:
- **Spreads** based on market volatility and time remaining
- **Quote prices** based on inventory position (long/short)
- **Order sizes** to manage risk and prevent excessive exposure

The goal is to profit from the bid-ask spread while managing inventory risk through mathematically optimal price adjustments.

## The Avellaneda-Stoikov Framework

### Core Principles

The Avellaneda-Stoikov model solves the market maker's fundamental challenge: balancing profitability (wider spreads) against competitiveness (narrower spreads) while managing inventory risk.

#### 1. **Reservation Price**

The "fair value" adjusted for current inventory position:

```
r(s, q, t) = s - q * γ * σ² * (T - t)
```

Where:
- `s` = current mid-price
- `q` = current inventory (position)
- `γ` = risk aversion parameter
- `σ` = volatility
- `T - t` = time remaining

**Intuition**: When you're long (positive inventory), your reservation price is *lower* than mid-price, incentivizing you to sell. When short, it's higher, incentivizing buying.

#### 2. **Optimal Spread**

The theoretically optimal bid-ask spread:

```
δ = γ * σ² * (T - t) + (2/γ) * ln(1 + γ/k)
```

Where:
- `k` = order book depth parameter
- Other parameters as above

**Intuition**: Spreads widen when:
- More time remains (more uncertainty)
- Volatility is higher (more risk)
- Risk aversion is higher (more conservative)

#### 3. **Bid and Ask Prices**

```
bid = r - δ/2
ask = r + δ/2
```

The reservation price becomes the midpoint of your quotes, with the optimal spread determining the distance from this midpoint.

### Implementation Enhancements

This implementation extends the base model with:

#### **Dynamic Gamma** (Risk Aversion)
```python
γ_dynamic = γ_base * exp(-|q / q_max|)
```
Risk aversion increases exponentially as position approaches limits, widening spreads to slow down adverse inventory accumulation.

#### **Inventory Skew Factor**
```python
skew = q * inventory_skew_factor * s
r = s + skew - q * γ * σ² * (T - t)
```
Additional adjustment to push quotes away from accumulating more adverse inventory.

#### **Asymmetric Spread Adjustments**

When long (q > 0):
- Tighten bid spread (encourage buying out of position less)
- Widen ask spread (encourage selling out of position more)

When short (q < 0):
- Widen bid spread
- Tighten ask spread

#### **Position-Based Order Sizing**

Order quantities scale based on distance from position limits, becoming more conservative as exposure increases.

### Prerequisites

- Python 3.8+
- Kalshi API credentials (demo or production)
- RSA private key file for authentication

### Installation

1. **Clone the repository**
```bash
git clone https://github.com/yourusername/Kalshi-MM.git
cd Kalshi-MM
```

2. **Install dependencies**
```bash
pip install -r requirements.txt
```

3. **Set up credentials**

Create a `.env` file:
```env
DEMO_KEYID=your-api-key-id
DEMO_KEYFILE=./your-pem-file
KALSHI_ENVIRONMENT=DEMO  # or PROD
```

Place your RSA private key PEM file in the project directory.

### Configuration

Edit `config.yaml` to define your market making strategies:

```yaml
STRATEGY_NAME:
  api:
    market_ticker: "KXNCAAF-26-ORE"  # Kalshi market ticker
    trade_side: "yes"                 # "yes" or "no" side

  market_maker:
    # Position Management
    max_position: 5                   # Maximum contracts to hold
    position_limit_buffer: 0.1        # Safety margin (10%)

    # Avellaneda-Stoikov Parameters
    gamma: 0.1                        # Risk aversion (higher = wider spreads)
    k: 1.5                            # Order book depth parameter
    sigma: 0.001                      # Volatility estimate
    T: 28800                          # Time horizon (seconds, 8 hours)
    min_spread: 0.0                   # Minimum spread floor

    # Inventory Management
    inventory_skew_factor: 0.001      # How much inventory affects quotes

    # Order Management
    order_expiration: 28800           # Order lifetime (seconds)

  dt: 2.0                             # Time between iterations (seconds)
```

#### Parameter Tuning Guide

**Risk Aversion (`gamma`)**:
- Lower (0.01-0.1): Tighter spreads, more aggressive
- Higher (0.5-2.0): Wider spreads, more conservative
- Default: 0.1

**Volatility (`sigma`)**:
- Estimate from historical price movements
- Higher σ → wider spreads
- For prediction markets: typically 0.001-0.01

**Time Horizon (`T`)**:
- Should match your trading session duration
- Affects how quickly spreads converge as time passes
- Typical: 8 hours (28800 seconds)

**Order Book Depth (`k`)**:
- Higher k → tighter spreads (assuming more liquidity)
- Lower k → wider spreads (assuming less liquidity)
- Typical: 1.0-2.0

### Running

```bash
python runner.py
```

This will:
1. Load all strategies from `config.yaml`
2. Initialize Kalshi API connections
3. Start market making threads for each strategy
4. Log all activity to individual strategy log files

**Monitor logs:**
```bash
tail -f STRATEGY_NAME.log
```

## Prediction Market Model (Feil & Nendel, 2026)

A second quoting model, built for contracts that settle to 0 or 1: [Optimal Market Making in Prediction Markets](https://arxiv.org/abs/2607.17991). Set `strategy.model: pmm` to use it; the default stays `avellaneda`. Everything around the quotes (risk limits, order management, journal, report) is shared.

**What it changes from A-S.** The price is a probability moving on a log-odds scale, so its volatility shrinks near 0 and 1. Fills depend on time, price and quote through an intensity. Settlement variance `q^2 p(1-p)` is priced at the end. There is no closed form: `src/pmm.py` solves a value function `V(t, p, q)` numerically, and quotes come from maximizing `intensity * (edge - z)`, where `z` is the value lost per contract by taking on the fill.

| file | role |
|---|---|
| `src/pmm.py` | the model, the HJB solver, quote lookup, the paper's Monte Carlo |
| `src/pmm_calibrate.py` | offline series fits and the online volatility and flow estimators |
| `src/pmm_quoter.py` | one market's estimators and solved surface, used by the session and by replay |
| `pmm_tool.py` | `reproduce`, `calibrate`, `surface`, `replay` |
| `pmm_calibration.json` | fits for the recorded weather series, measured from data recorded in July 2026 |
| `pmm_config.yaml`, `pmm_prod_config.yaml` | session configs for demo and production |

```bash
python pmm_tool.py reproduce                  # the solver against the paper's figures and table 2
python pmm_tool.py surface --series KXHIGHNY  # the quotes a parameter set implies
python pmm_tool.py replay --series KXHIGHNY   # A-S vs this model on recorded data, simulated fills
python demo.py --config pmm_config.yaml run --dry-run --duration 600
```

**Where this departs from the paper.** With the paper's settings none of these change its results.

- *Solver.* The paper's fixed-point iteration only converges for time steps below about `1 / (4 * intensity)`, which at Kalshi's flow means tens of thousands of steps. This uses policy iteration, which is stable at any step and takes about a second per market.
- *Time.* A live market has no start date, so everything runs on time left, and `T` is the window over which volatility, activity and `k` ramp up before close.
- *Stand-down.* The model's terminal time is when the bot stops quoting, not the market's close: what is held then goes to settlement, which is what the terminal penalty prices.
- *Fees.* Maker fees come off the edge on series that charge them.
- *Quote bound.* `min_offset` stops a quote from going through the mid, where the paper's intensity grows without bound.
- *Queue position.* `queue_aware` scales the intensity by `1 / (queue ahead + size)` when choosing the final price. This is the one change to behavior at normal settings: without it the model joins a long queue in a wide market where stepping inside is what gets filled.

**What it does not do.**

- *Adverse selection.* The model assumes fills carry no information, so this is its biggest gap on Kalshi. Recorded weather tape was heavily adverse close to resolution, so the stand-down, not the model, handles that (8 hours before close in `pmm_prod_config.yaml`).
- *Calibration.* The paper's table 1 is illustrative and it does not address calibration. The fits and the online estimators here are this repository's own choices, including `A` from `contract flow / (queue + size)` and `k` from how far orders reach past the touch.
- *Risk aversion.* `gamma` and `gamma_T` are preferences, not estimates. Use `surface` to see what a value does before changing it.

## Demo Trading Harness

`demo.py` runs the strategy live on the Kalshi **demo** exchange across several
markets at once, journals every decision, and writes a diagnostic report.

```bash
python demo.py preflight --order-test        # reachability, auth, clock, rate tier, order path
python demo.py select                        # pick liquid + illiquid markets
python demo.py run --dry-run --duration 600  # live books, simulated orders
python demo.py run --duration 3600           # live demo orders -> runs/<id>/report.md
python demo.py report runs/<id>              # rebuild a report from its journal
```

**Running locally** (keys in a `.env` at the repo root, run from the repo root):

```
DEMO_KEYID=<your API key id>
DEMO_KEYFILE=./your-key.pem
```

**Credentials** (environment or `.env`): `DEMO_KEYID` plus either
`DEMO_PRIVATE_KEY` (the PEM text; `\n`-escaped or base64 is fine for
single-line secrets) or `DEMO_KEYFILE` (path). RSA and Ed25519 keys work.
**Network**: the harness talks only to `external-api.demo.kalshi.co`.
Production needs `--env prod --allow-prod`.

The test plan lives in `session_config.yaml` (selection criteria, strategy
parameters, execution policy, risk limits). Architecture:

| Module | Role |
|---|---|
| `src/credentials.py` | load key id + private key, build the client |
| `src/clients.py` | signed REST client: V2 `/portfolio/events/orders`, token-bucket pacing per Kalshi read/write budgets, 429/5xx retry (never blind-retries a create), error bodies, request stats |
| `src/gateway.py` | normalizes `_fp`/`_dollars` fields and V2 `book_side` direction; `DryRunGateway` simulates writes |
| `src/selector.py` | scores open markets on volume / OI / spread / depth, confirms against live books, picks N liquid + N illiquid (one per event) |
| `src/session.py` | one loop for all markets: batched reads (books, orders, positions, fills), own orders netted out of the mid, `AvellanedaMarketMaker` math for quotes, post-only cross policy |
| `src/execution.py` | one order per side; amend instead of cancel/replace, queue-preserving size-down and expiry refresh, journaled latency and rejects |
| `src/risk.py` | per-market loss halt, gross position cap, session loss / error / balance kill switches |
| `src/journal.py` | append-only JSONL of every cycle, decision, order write, fill, public trade |
| `src/diagnostics.py` | rebuilds the report from the journal: quoting time, touch %, spread decomposition (inventory / liquidity / premium, vol scaling), fills, edge, markouts and adverse selection, market share, inventory, rejects, API health, rule-based findings, liquid vs illiquid comparison |

Safety: every quote is post-only with a 5-minute expiration (refreshed by a
queue-preserving amend), `cancel_order_on_pause` is set, and shutdown (normal,
Ctrl-C, SIGTERM, kill switch or crash) cancels all of the session's orders and
verifies none remain.

Tests: `python -m unittest discover -s tests` (the session engine is
exercised end to end against an in-memory exchange in `tests/fake_exchange.py`).

## Example Output

```
2025-11-25 14:32:15 - Running Avellaneda market maker at 0.00
2025-11-25 14:32:15 - Current mid price for yes: 0.0700, Inventory: 0
2025-11-25 14:32:15 - Reservation price: 0.0700
2025-11-25 14:32:15 - Computed desired bid: 0.0635, ask: 0.0765
2025-11-25 14:32:15 - Placed new buy order. ID: 96f6..., Price: 0.0635, Size: 5
2025-11-25 14:32:15 - Placed new sell order. ID: 3899..., Price: 0.0765, Size: 1

[After buying 2 contracts]
2025-11-25 14:35:20 - Current mid price for yes: 0.0705, Inventory: 2
2025-11-25 14:35:20 - Reservation price: 0.0693  # Lower than mid!
2025-11-25 14:35:20 - Computed desired bid: 0.0620, ask: 0.0755
# Notice: quotes shifted down to encourage selling
```

### Built-in Safeguards

1. **Position Limits**: Hard caps on maximum exposure
2. **Position Buffers**: Order sizes reduce near limits
3. **Rate Limiting**: 100ms minimum between API calls
4. **Order Expiration**: All orders have maximum lifetime
5. **Dynamic Risk Aversion**: Gamma increases with extreme positions

### Kalshi API Documentation

- [Official API Docs](https://trading-api.readme.io/reference)
- Authentication: RSA-PSS signed requests
- Rate Limits: Be mindful of request frequency
- Market Data: Real-time bid/ask quotes
- Order Management: Create, cancel, query orders