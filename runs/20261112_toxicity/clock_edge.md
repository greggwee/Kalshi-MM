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
- **The lows result held on six series never examined** (all four
  pre-registered hypotheses passed; see the holdout section).
- **Central Park is the exception**, and the station schedules explain why
  (section 5): it reports once an hour, the airports every five minutes.

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
direction, all six series pooled.

### Result: all four pass

201,164 orders, 196 event dates, 6,527 orders inside the window
(`analysis/clock_holdout.out.txt`):

| | hypothesis | result | |
|---|---|---|---|
| H1 | maker settlement, inside minus outside, is negative | -2.35 [-4.26,-0.51] (inside -7.34, outside -4.98) | pass |
| H2 | takers inside the window earn after the fee | +4.01 [+2.07,+5.87] | pass |
| H3 | takers in the rest of the outcome window earn after the fee | +2.50 [+1.48,+3.44] | pass |
| H4 | a touch maker skipping the window still loses over the whole day | -2.38 [-2.95,-1.82] | pass |

- The taker edge in the lows is not an artefact of looking: on unseen series
  it is +4.0c inside the window and +2.5c in the rest of the outcome window,
  after the fee.
- Passive touch making in airport-station lows loses about 2.4c per fill at
  settlement even with the clock rule. That is settled too.
- Per series (not part of the test) the taker edge inside the window is
  positive in all six and significant alone in Atlanta, Houston and
  Minneapolis.

## 5. Why New York is different: the stations' reporting schedules

Exploratory, found after the tests above (`analysis/clock_stations.out.txt`).
From the public NWS station feeds, over the last week:

- **Central Park (KNYC)** reported at :51 in 124 of 171 observations, and
  almost never in between.
- **Every airport station checked** (Midway, Miami, LAX, Austin, Denver,
  Philadelphia, Seattle, Newark, Boston) reports hourly at :51-:54 and also
  every five minutes.

So at Central Park the temperature is public once an hour; at the airports it
is public continuously. That predicts the Central Park markets are uninformed
for most of the hour, and that the clock rule should work best there.

Maker settlement P&L over the whole day with the clock rule applied:

| market | every order | level-clearing orders only | 30 min, every order |
|---|---|---|---|
| KXHIGHNY (Central Park) | +0.60 [+0.01,+1.18] | -0.17 [-0.77,+0.42] | +0.23 [+0.11,+0.34] |
| KXLOWTNYC (Central Park) | +0.41 [-0.49,+1.21] | +0.25 [-0.71,+1.20] | **+1.16 [+0.62,+1.66]** |
| 17 airport series | -6.4 to +0.15 | -11.0 to -0.4 | mostly negative |

- The two Central Park markets are the only ones where the maker is not
  negative at settlement, and KXLOWTNYC is not negative even for
  level-clearing fills.
- No five-minute rhythm shows up in the airport markets: their five-minute
  markout is flat at about zero through each five-minute block. Their
  information arrives smoothly, so there is no window to avoid.
- This comparison was made after seeing the schedules, on two markets. Treat
  it as a hypothesis: **markets settled at stations that report only hourly
  are where a clock-aware maker can work.** It is testable on other series by
  looking up each station's schedule first and predicting before measuring.

## Where this leaves the edge question

| what | where | evidence | size |
|---|---|---|---|
| Take on the observation | airport-station lows | holdout passed | +4.0c per contract after fee in the window, +2.5c in the rest of the outcome window, for takers who know the direction |
| Make with the clock rule | Central Park markets | in-sample, two markets | +0.2 to +1.2c at 30 min; settlement positive but intervals touch zero |
| Make passively | everything else | holdout passed (lows), 200 dates (highs) | negative |

The taker numbers are what informed takers earned. Earning them requires the
station reading and a view of what it does to each strike; that is the next
thing to build, and KXLOWTNYC with the clock rule is the place to test making.
