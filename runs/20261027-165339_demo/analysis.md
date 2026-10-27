# A-S market maker on Kalshi demo: diagnostic write-up

Run `runs/20261027-165339_demo`: 1 hour of live demo orders on 3 liquid and 3 illiquid
markets, 27 Oct 2026, 16:53-17:53 UTC. Supporting dry run: `runs/20261027-164220_demo_dry`.
Auto-generated numbers: `report.md` / `report.json`. Everything else below was computed from
`events.jsonl` by the scripts in `analysis/` (their outputs are saved next to them).

## Summary

- **The bot works end to end on demo.** Every preflight step passes, including
  create/verify/amend/cancel/confirm. The 1-hour session placed 762 order writes with 4
  failures, all from a demo-side 503 incident that the bot rode out. There were 0 position
  mismatches, and the shutdown cancelled everything and **verified 0 resting**. Quotes never
  crossed: 0 violations of bid < ask, bid < best ask and ask > best bid over 10,632 ticks.
- **0 fills, because nobody traded.** None of the six markets printed a single public trade
  during the hour (checked directly against the tape, not just the poller). No quote
  placement could have been filled.
- **Even with flow, the liquid quotes would not have filled.** TEN@CIN's ask was lifted about
  once a minute at 0.18 from 04:30 to 15:30 UTC. Our A-S ask sat at 0.19 all hour, one tick
  behind. Payrolls sat 18 ticks behind because its k estimate collapsed to the clamp (5).
- **In the illiquid markets we were the market**: at or inside the touch 32-56% of the time,
  with nobody there to trade against.
- **Three structural findings drive the recommendations.**
  1. The depth-fit k is unstable and collapses to 5, giving a 36c liquidity term.
  2. The inventory term is 2-second realized vol × a 24h horizon. It is negligible in quiet
     markets (0.13c per contract) and explodes on quote flicker (up to 132c per contract).
  3. Outward tick rounding means A-S can never join the touch in a 1c market whose mid is on a
     half tick.
- **Fixed before this run:** the order listing was blind. An earlier 5-minute live run this
  morning orphaned 987 resting orders (cleaned up, no fills, no positions). See Incidents.

## Steps 1-5

| step | result |
|---|---|
| 1. tests | 98 → 104, all green (8 new or changed tests fail on the pre-fix code) |
| 2. `preflight --order-test` | all 15 steps PASS. Listing lag measured: create 1.02 s, amend 0.41 s, cancel 1.01 s |
| 3. `select` | first pass picked dead books as "illiquid" (1c bid / 97c ask, zero volume and OI); selector fixed, re-selected (below) |
| 4. dry run, 600 s | 0 invariant violations in 1,800 ticks; 215 writes; cycle p50 116 ms; shutdown verified 0 |
| 5. live run, 3600 s | `duration_reached`; 762 writes; shutdown verified 0 resting; preflight afterwards: 0 resting, 0 positions |

Selection used (`runs/selection_20261027-124153.json`), as seen at selection time.
Session averages are in the competitiveness table further down.

| class | market | mid | spread | 24h vol | OI |
|---|---|---|---|---|---|
| liquid | KXNFLGAME-26NOV01TENCIN-TEN | 0.17 | 1c | 495 | 23,905 |
| liquid | KXNFLGAME-26NOV02CHISEA-SEA | 0.60 | 2c | 861 | 6,521 |
| liquid | KXPAYROLLS-26OCT-T70000 | 0.52 | 1c | 75 | 257,309 |
| illiquid | KXTRUMPMENTIONB-26OCT27-ILHA | 0.86 | 13c | 18 | 11 |
| illiquid | KXCPI-26OCT-T0.2 | 0.60 | 11c | 5 | 52,974 |
| illiquid | KXNFLSPREAD-26NOV01MINDET-MIN8 | 0.15 | 17c | 50 | 50 |

## First-contact checks

| check | result |
|---|---|
| `GET /markets/orderbooks?tickers=A&tickers=B` shape | as documented (`orderbooks[].orderbook_fp.{yes_dollars,no_dollars}`) |
| `GET /portfolio/orders?ticker=A,B&status=resting` | **different.** `ticker` filters one market; `A,B` silently returns nothing and `tickers` is ignored. The listing also lags writes by ~0.4-1.0 s. Fixed in `774388c` (account-wide listing + write overlay) |
| order fields `book_side`, `yes_price_dollars`, `remaining_count_fp`, `fill_count_fp`, `expiration_time` | as documented |
| `GET /portfolio/fills` fields | as documented, verified with a real fill: `fill_id`, `book_side`, `count_fp`, `yes_price_dollars`, `fee_cost` (dollar string), `is_taker`, `ts` (**epoch seconds**), plus `market_ticker` and `order_id` |
| positions fields | `position_fp`, `realized_pnl_dollars`, `fees_paid_dollars`, `market_exposure_dollars`; parsed correctly |
| `GET /markets?tickers=A,B` status | tradable markets report `active` |
| `GET /account/limits` | `{usage_tier: basic, read: {refill_rate 200, bucket_capacity 600}, write: {100, 100}}` |
| post-only cross rejection code | **not observed.** The clamp policy kept every quote one tick inside, so no post-only reject occurred; the real error code is still unverified |
| does an amend keep `post_only` | **not observable.** With `aggressive_reprice: replace`, the bot only amends away from the market or for size/expiry, never toward it, so an amend cannot cross. No fills, so no `is_taker` check |

To verify fill parsing before the live hour, I made one 1-lot IOC round trip on TEN@CIN
(buy 0.18, sell 0.17) at 16:53 UTC, before the session started. It cost $0.0303 (1c spread +
2.03c taker fees). The exchange-side realized P&L for TEN@CIN includes it; the session's own
P&L does not. Balance: 2453.24 → 2453.2097.

## Incidents

1. **Orphaned orders (earlier run `20261027-161635_demo`, not this one).** `ticker=A,B` never
   matched, so that session saw no resting orders. It re-created both sides of every market
   each cycle (1,668 creates, 0 amends or cancels in 5 minutes), never netted its own orders out
   of the mid, and reported "0 left resting" while 987 were live. I cancelled them (556
   cancelled, the rest had expired); there were no fills and no positions. That run's numbers
   are contaminated by its own orders and should not be used. In this run: exactly 12 resting
   orders (one per side per market) at both mid-run checks, and 0 duplicates.
2. **Demo 503 incident, about 17:24 UTC.** There were 24 × 503s: 6 on `GET /exchange/status`
   and 18 on amends, of which 3 still failed after retries. The orderbooks endpoint peaked at
   9.7 s and one cycle took 37.5 s. During the incident the exchange cancelled both MIN-8
   orders: they show `canceled` at 17:24:23 with 0 fills, and the bot sent no cancel. That is
   consistent with `cancel_order_on_pause`. The bot's next amend got a 404, which it treated as
   "order gone" (not a reject), and it re-created both sides 7 s later. No halts and no kill
   switch.

## Inventory control

The bot never held inventory, so the live inventory metrics are empty: q ≡ 0, max |q| = 0, 0%
of time at the limit, 0 round trips, correlation of skew with q undefined. What *was*
verified:

- **Direction.** Replaying all 10,632 recorded states through `compute_quotes` with
  q ∈ {±5, ±10} gives 42,528 checks and 0 violations: the reservation sits below mid when long
  and above it when short, and bid < ask. With q = 0 the reservation equals mid exactly.
- **Magnitude.** Per-contract skew γσ²h at the live sigma:

| market | skew c/contract p50 | p95 | at q = 10 (p50) | binary vol scale |
|---|---|---|---|---|
| TEN@CIN (liquid) | 0.14 | 0.14 | 1.4c | 0.76 |
| CHI@SEA (liquid) | 0.23 | 0.23 | 2.3c | 0.98 |
| Payrolls (liquid) | 0.24 | 0.24 | 2.4c | 1.00 |
| ILHA (illiquid) | 1.48 | 132 | 15c | 0.66 |
| CPI (illiquid) | 3.43 | 27.7 | 34c | 0.98 |
| MIN-8 (illiquid) | 0.13 | 0.13 | 1.3c | 0.72 |

  In the quiet markets (all three liquid ones plus MIN-8), sigma sits at its floor after
  warm-up. Even a full 10-contract position moves the reservation by only 1-2 ticks, so
  inventory control there would come from the size limits, not from skew. In the flickery
  illiquid markets the same term runs to 15-34c at q = 10 and saturates against the 0/1
  clamps at p95.
- **Cold start**, which the brief predicted, was confirmed. For the first 9 cycles sigma is
  `calibration_constant` × scale (0.027-0.040), so q = 5 would shift the reservation 8-19c
  (19.2c on Payrolls). That is about 16× the warmed-up value in the quiet markets:
  (0.04 / 0.01)², cold constant over sigma floor.

## Quote competitiveness

| market | class | market spread | our spread (mean) | at or inside touch, bid / ask | ticks behind (p50) | k p50 (range) | σ p50 (2 s) | liq term | inv term | dominant term |
|---|---|---|---|---|---|---|---|---|---|---|
| TEN@CIN | liquid | 1.0c | 6.8c (p50 3.0) | 0% / 0% | 1 / 1 | 76 (5-76) | 0.0076 | 2.6c | 0.14c | liquidity 100% |
| CHI@SEA | liquid | 2.0c | 4.0c | 0% / 0% | 1 / 1 | 69 | 0.0098 | 2.9c | 0.23c | liquidity 99% |
| Payrolls | liquid | 1.0c | 37.0c | 0% / 0% | 18 / 18 | **5.0** | 0.010 | **36.5c** | 0.24c | liquidity 100% |
| ILHA | illiquid | 6.7c | 24.0c (p50 13) | 32% / 32% | 15 / 6 | 40 (5-200) | 0.025 (p95 0.23) | 4.9c (p95 36.5) | 1.5c (p95 132) | liquidity 56%, inventory 44% |
| CPI | illiquid | 8.4c | 12.8c (p50 11) | 33% / 33% | 3 / 3 | 40 (15-160) | 0.038 (p95 0.11) | 4.9c | 3.4c (p95 27.7) | liquidity 52%, inventory 48% |
| MIN-8 | illiquid | 9.9c | 6.1c | 56% / 56% | 2 / 2 | 30 | 0.0072 | 6.5c | 0.13c | liquidity 100% |

The resolution premium was about 0.02c everywhere (every market closes 7-14 days out).
`min_spread` (2c) never bound.

**Why liquid quotes sit exactly one tick behind.** The quote is
`floor(r − s/2)` / `ceil(r + s/2)`. On a half-tick mid such as 0.175, any model spread above
1c rounds out to 3 ticks (0.16/0.19). With k capped at 200, the liquidity term alone is
≥ 1.0c, so this rounding rule cannot join the touch in a 1c market at any parameter setting.
Example from a recorded tick: model spread 1.04c, raw 0.1698/0.1802, quoted 0.16/0.19.

**Why Payrolls sat 18 ticks behind.** `estimate_k_from_depth` fits ln(cumulative depth)
against distance. A deep book with most of its size at the top level has a flat cumulative
curve, so the slope comes out near zero and is clamped to k = 5. That puts
(2/γ)·ln(1 + γ/k) at 36.5c on a 1c market with 257k open interest. TEN@CIN's fit also dropped
to 5 at some refreshes, which is why its mean spread (6.8c) is far above its median (3.0c).

**Why illiquid spreads swing.** ILHA's mid changed 38 times in the hour, mostly a 22-lot
bid hopping between 0.84 and 0.85. Each half-cent mid change, sampled every 2 s, enters the realized variance
as diffusion and is scaled to $/√hour, then multiplied by the 24h horizon. That pushed the
inventory term to 132c at p95. I checked that these mid moves are other participants', not
our own orders leaking: wherever the netted best bid sat at our price, the depth there was
22, 25, 5 or 1 contracts, never our 2.

**Effect of the vol scaling sqrt(p(1-p))/0.5.** It multiplies the inventory term by scale²:
0.58 on TEN@CIN (p = 0.175), 0.44 on ILHA (p ≈ 0.87), and ≈ 1 near p = 0.5. Where the
inventory term is 0.1-0.2c, that changes no rounded quote. The one place it mattered was ILHA,
where it halved the median inventory term (1.48c vs 3.66c per contract unscaled). Note for
later: when sigma is measured from the contract's own price, that price already shows the
compression near 0 or 1, so applying the scale on top of data-driven sigma double-counts it.
It is principled for the cold-start constant and the floor (proposal only; nothing changed).

## Fills, edge, markouts, adverse selection

None: 0 fills, 0 public prints, so edge, markouts, adverse selection and maker % are all
undefined. The tape does say what flow looks like when it exists. TEN@CIN's ~20 prints per
30 minutes earlier in the day were all one-sided: takers buying YES at 0.18, about 1 contract
each, at a price that never moved. A maker at the ask would have been filled short repeatedly.
That is a good inventory-control test, and adverse selection would have been near zero, since
the price never moved. But it needs our ask at the touch, behind a queue of thousands of
contracts (5,563 within 5c of mid), so even a touch quote would have had a tiny share.

## P&L, fees, rejects, API health

- **P&L**: $0.00 session P&L and $0 fees. All six series use the `quadratic` fee type, so
  maker fills would have been fee-free. The probe's taker fee was $0.0104 per contract at
  p = 0.18.
- **Writes**: 762 journaled writes: 235 creates, 304 amends (4 failed), 223 cancels (all ok).
  Liquid markets made 144 writes per hour, mostly expiry-refresh amends, about one per order
  every 200 s (made when a third of the 300 s expiry is left). Illiquid markets made 618, mostly repricing after other participants' quote
  changes.
- **Rejects**: 0 post-only, 0 balance, 0 rate-limit (429). There were 4 failed writes: 3 × 503
  after retries and 1 × 404 for the order the exchange cancelled. Write failures count toward
  the per-market reject-rate halt (12 per minute), so a longer 503 burst would halt a market.
  That is conservative but arguably wrong (see recommendations).
- **API**: 10,315 requests (~2.9 per second against a Basic-tier budget of 200 read /
  100 write tokens per second), 25 errors (24 × 503 in the incident, 1 × 404), 20 retries,
  0 s spent pacing. Latency p50 ≈ 100 ms and p95 ≈ 150-185 ms on every endpoint.
- **Cycle time**: p50 461 ms, p95 1.48 s, 1.4% of cycles over 2 s, max 37.5 s (the 503
  incident).

## Liquid vs illiquid

| | liquid | illiquid |
|---|---|---|
| market spread | 1.33c | 8.34c |
| our spread | 15.95c (3-7c excluding Payrolls) | 14.98c (7-13c p50) |
| at or inside touch | 0% | 32-56% |
| public prints during the hour | 0 | 0 |
| mid changes per hour | 0 | 0-38 |
| dominant spread term | liquidity (k), always | liquidity ≈ inventory (σ flicker) |
| writes per hour | 144 | 618 |
| what blocked fills | quotes 1-18 ticks behind + no flow | no flow |

In liquid markets the model is too wide to compete: the touch is held by large queues and
demo flow is about one contract a minute. In illiquid markets the model quotes inside the
market and would be the first to trade, but its width and skew swing wildly with quote
flicker. Both classes need real taker flow before edge and adverse selection can be measured.

## Parameter recommendations

Each recommendation below was replayed against the recorded states (`analysis/counterfactual.out.txt`).
Spreads are p50s; "touch" means at or inside the touch.

| scenario | Payrolls | TEN@CIN | CHI@SEA | ILHA | CPI | MIN-8 |
|---|---|---|---|---|---|---|
| as run | 37c, 0% | 3c, 0% | 4c, 0% | 13c, 32% | 11c, 34% | 7c, 56% |
| k floor 40 | 7c, 0% | 3c, 0% | 4c, 0% | 7c, 42% | 8c, 48% | 7c, 56% |
| horizon 4 h, cold σ 0.02 | 37c, 0% | 3c, 0% | 4c, 0% | 7c, 42% | 7c, 65% | 7c, 56% |
| combined: k floor 40, liquid k 200 + min_spread 0.01, horizon 4 h, 60 s / 1 h sigma | 3c, 0% | 3c, 0% | **2c, 100%** | 7c, 63% | 6c, **94%** | 5c, 56% |

The combined row's inventory skew drops from 132c to 2.7c per contract at p95 on ILHA, and
from 27.7c to 0.9c on CPI.

1. **k.** Floor the depth-fit k at about **40**, or fix k per class: **liquid ≈ 200,
   illiquid 40**. The `[5, 200]` clamp lives in `helper.estimate_k_from_depth`; no config
   setting turns the fit off (`k_refresh_every` still refits on the first iteration), so this
   is a code change for you to approve. Longer term, k should come from trade or fill arrival
   versus distance (its A-S meaning), not from book shape.
2. **gamma.** Keep **1.0** for now; it is not the binding lever. For k ≫ γ the liquidity term
   is ≈ 2/k whatever γ is, and the inventory term is dominated by σ² and h. Revisit once there
   are fills, targeting a skew at max_position of about one market spread.
3. **`max_horizon_h`: 24 → 4.** The inventory term is linear in h. At 4 h with σ ≈ 0.04
   (typical illiquid), γ = 1 gives 0.64c per contract, so 6.4c at q = 10, about one illiquid
   spread, instead of 34c.
4. **sigma estimator.** Sample mids every ~60 s over a trailing hour instead of every 2 s over
   120 points. The 2 s estimator measures quote flicker, not diffusion. This is a code change
   for you to approve; the replay above uses it.
5. **cold-start sigma (`calibration_constant`): 0.04 → 0.02.** Warm sigma was at the floor
   (0.0076-0.010) in quiet markets and 0.025-0.038 in flickery ones. With h = 4, q = 5 at cold
   start would shift the reservation 0.8c instead of 19c. Also consider
   **`sigma_floor` 0.01 → 0.02** so quiet markets keep a minimal skew (0.16c per contract at
   h = 4, so 1.6c at q = 10).
6. **`min_spread`.** Keep **0.02** for illiquid markets. For 1c liquid markets, 0.01 together
   with k ≈ 200 reaches the touch only when the mid sits on a whole tick (CHI@SEA: 100%). For
   half-tick mids (TEN@CIN, Payrolls) the outward rounding keeps the quote one tick behind at
   any setting. Joining the touch there needs a rounding change, for example rounding each side
   to the nearest tick unless that would cross or reach the other side. That is a strategy
   change for you to approve.
7. **`cycle_s`: keep 2.0.** Cycles took 0.46 s (p50); books changed at most every 1-2 minutes;
   the ~1 s listing lag is covered by `listing_grace_s` = 5 s. A faster cycle only adds reads
   on demo; reconsider for production-speed flow.
8. **Not parameters, but next.**
   - (a) Count transient 5xx write failures separately from strategy rejects in the reject-rate
     halt.
   - (b) Select markets on prints in the last hour, not 24h volume, and schedule runs inside
     active windows. Demo flow is bot-driven and bursty: TEN@CIN had steady flow from 04:30 to
     15:30 UTC that stopped an hour before this run.
   - (c) A second live hour with items 1, 3 and 5 (plus 4 if approved), run during an active
     window, would finally exercise fills, inventory and markouts.

Nothing in `helper.py` or the risk limits was changed. Items 1, 4 and 6 need code changes in
the strategy; items 3, 5 and 7 are config-only.
