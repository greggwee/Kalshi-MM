# Is the observation clock an edge we can capture? (2026-11-13)

Follow-up to `observation_clock.md`. Read-only, public tape plus the July
recorded books. Outputs: `analysis/clock_edge.out.txt`,
`analysis/clock_queue_july.out.txt`, `analysis/clock_holdout.out.txt`.

## Verdict

- **The effect is real and stable.** It is present in every month from April
  to November, on both taker sides, and grows the closer the price is to 0.50.
- **As a rule for a passive maker it is necessary but not sufficient.**
  Skipping the window helps every kind of maker, but it only leaves a profit
  for one who is near the front of the queue in KXHIGHNY, and that profit is
  about 0.2-0.6c per contract.
- **A late joiner still loses.** The fills a maker at the back of the queue
  gets are the ones that clear the whole level, and those lose outside the
  window too.
- **In the thin lows the money goes to takers, not makers.** Takers inside the
  window earn about 3.4c per contract after the fee. That is the edge, and it
  belongs to whoever reads the observation, not to whoever avoids it.
- **So the project worth building is an observation-driven fair value**, used
  to requote or take when a reading settles a strike. The clock rule is the
  fallback for when that signal is absent.

Cents per contract for a maker at the touch unless stated, weight
`min(size, 10)`, fill price 0.10-0.90, 95% intervals from resampling event
dates. Window = :52:00-:57:00, chosen on KXHIGHNY's first half of dates and
not changed since. Outcome window = 8-16h before close for highs, 16-24h for
lows. The dataset now has about 200 event dates for KXHIGHNY, KXHIGHCHI and
KXHIGHMIA, 180 for KXHIGHLAX and 196 for each low.

## 1. The effect itself, with more data

Inside minus outside, in the outcome window:

| group | 5 min | 30 min | settlement |
|---|---|---|---|
| KXHIGHNY | -1.41 [-1.72,-1.08] | -1.32 [-1.84,-0.75] | -0.83 [-2.18,+0.47] |
| other highs (9 series) | -0.38 [-0.57,-0.19] | -0.55 [-0.91,-0.20] | -0.82 [-1.40,-0.21] |
| lows (9 series) | not usable | -3.37 [-6.05,-0.36] | -3.54 [-4.65,-2.39] |

With the Jul-Nov tape added the other highs are now significant on all three
measures (they were borderline before).

Stability, KXHIGHNY at five minutes:

- **By month:** -1.43, -1.53, -1.83, -1.35, -1.16, -1.41, -1.02, -1.15 for
  April to November. Every month's interval excludes zero.
- **By distance of the price from 0.50:** -1.95, -1.64, -1.21, -0.88 from the
  nearest fifth to the farthest. The same ordering holds for the lows at
  settlement: -8.33, -5.23, -2.35, -0.63.
- **By side:** -1.54 when the taker buys YES, -1.23 when the taker sells.

A contract near 0.50 is one whose strike is in play, so the reading matters
most there. That dose-response is what the mechanism predicts.

## 2. Queue position decides whether anything is left

We join the touch behind whoever is already there. A maker at the back is
reached only by orders that take the whole level. On the tape, those are
orders that printed at more than one price: 24-25% of orders in every group.

Outside the window, in the outcome window:

| group | fills | 5 min | 30 min | settlement |
|---|---|---|---|---|
| KXHIGHNY | every order (front of queue) | +0.31 [+0.26,+0.36] | +0.28 [+0.13,+0.43] | +0.62 [-0.01,+1.24] |
| KXHIGHNY | level-clearing only (back) | **-0.24 [-0.34,-0.15]** | **-0.23 [-0.43,-0.04]** | -0.44 [-1.09,+0.18] |
| other highs | every order | -0.01 [-0.07,+0.05] | -0.30 [-0.41,-0.18] | -0.43 [-0.74,-0.11] |
| other highs | level-clearing only | -0.70 [-0.77,-0.63] | -0.91 [-1.07,-0.75] | -1.25 [-1.60,-0.93] |
| lows | every order | not usable | -1.17 [-2.30,+0.01] | -3.44 [-4.20,-2.63] |
| lows | level-clearing only | not usable | -1.27 [-2.71,+0.19] | -3.58 [-4.63,-2.51] |

Over the whole day, per fill, without the rule and with it:

| group | fills | measure | no rule | with rule |
|---|---|---|---|---|
| KXHIGHNY | every order | 30 min | +0.10 [-0.04,+0.24] | **+0.23 [+0.11,+0.34]** |
| KXHIGHNY | every order | settlement | +0.53 [-0.01,+1.07] | +0.60 [+0.01,+1.18] |
| KXHIGHNY | level-clearing | 30 min | -0.49 [-0.65,-0.33] | -0.28 [-0.42,-0.15] |
| other highs | every order | settlement | -0.24 [-0.56,+0.08] | -0.19 [-0.52,+0.13] |
| lows | every order | settlement | -1.53 [-1.94,-1.12] | -1.34 [-1.76,-0.92] |

- The rule drops 8% of KXHIGHNY fills and moves the front-of-queue maker from
  "indistinguishable from zero" to clearly positive at 30 minutes.
- It does not turn the back-of-queue maker, the other highs or the lows
  positive.

The July books give the same ordering directly (`clock_queue_july.out.txt`):
maker P&L falls step by step from the front of the queue to the back, inside
and outside the window. That is only 3 event dates, in a month that was bad
for makers throughout, so it supports the direction and nothing more. Two
facts from it are useful: the median touch level in these markets was 21
contracts, and the median order took 35% of it. Queues at the active strikes
are short, so position is decided by who posts first after each price change.

## 3. No drift to lean on afterwards

If the market under-reacted to the reading, fills in the same direction as the
window's net flow would keep hurting the maker afterwards, and a maker could
quote one side.

- **KXHIGHNY:** the opposite, weakly. At settlement the maker does better on
  later fills that go with the window's flow (+2.7 to +3.4) than against it
  (-1.9 to -2.1); the difference is -4.8 [-9.2,-0.6] for the rest of the hour.
  That reads as partial reversal, but it is one marginal result among 36
  comparisons and the 5-minute numbers show nothing.
- **Other highs:** nothing consistent.
- **Lows:** the sign points to continuation over the next ten minutes
  (difference +4.0 [-1.6,+9.9] at settlement), not significant.

Nothing here is strong enough to trade.

## 4. Who is on the winning side

Taker P&L to settlement, after the taker fee (about 1.3c per contract at these
prices), paying the order's average price:

| group | takers | net P&L |
|---|---|---|
| KXHIGHNY | inside the window | -1.77 [-2.91,-0.72] |
| KXHIGHNY | rest of outcome window | -2.42 [-3.04,-1.78] |
| other highs | inside the window | -0.73 [-1.38,-0.11] |
| other highs | rest of outcome window | -1.47 [-1.78,-1.15] |
| **lows** | **inside the window** | **+3.38 [+2.03,+4.64]** |
| lows | next 10 min, with the window's flow | +2.17 [-0.71,+5.00] |
| lows | rest of outcome window | +0.85 [+0.01,+1.62] |

- In the highs nobody on the taking side covers the fee, even in the window.
  The fee is larger than the information. Makers and takers there are fighting
  over a spread that roughly equals the fee.
- In the lows, takers in the window clear the fee by more than 3c. Quotes
  there are wide and slow, and the reading moves the fair value by more than
  the fee.
- A taker without the reading cannot copy this: it is the average over orders
  that already knew the direction. Following the flow afterwards is positive
  but not significant.

## What this means for what to build

1. **Observation-driven fair value for the lows.** Read each station's
   observation as it is published, work out what it does to each strike, and
   either take quotes that are now wrong or move our own before they are hit.
   The tape says the gross edge is about 4.6c per contract against a 1.3c fee.
   Unknown: how fast the existing takers are, and how much size is available.
2. **Clock rule as default behaviour** in any maker we run: no quotes from
   about :51:30 to :57:00 in the outcome window, for every series.
3. **Queue discipline for KXHIGHNY** if we make there at all: post early, do
   not requote without a reason, never join a level late. The numbers say this
   is a 0.2-0.6c business at best.
4. **Do not make passively in the lows' outcome window.** Even outside the
   toxic minutes the pool loses 3.4c per fill at settlement.

## Holdout test (written before the data was downloaded)

The lows result in section 4 was found by looking, so it needs data that was
not looked at. Six low series never examined (Boston, Atlanta, Houston,
Seattle, Minneapolis, Phoenix) are being downloaded. The test is
`analysis/clock_holdout.py`, committed before their tape arrived, with the
window, outcome window, price band and weighting all fixed as above:

- **H1** Maker settlement P&L is lower inside the window than outside it.
- **H2** Takers inside the window earn money after the fee.
- **H3** Takers in the rest of the outcome window earn money after the fee.
- **H4** A touch maker who skips the window still loses at settlement over
  the whole day.

Each passes only if its date-bootstrap interval excludes zero in the stated
direction, all six series pooled. Results are appended below when the
download finishes.
