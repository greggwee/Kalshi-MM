# The observation clock: out-of-sample test (2026-11-12)

Read-only, public tape. Question: is there a part of each hour in which resting
quotes lose money, and does a "no quotes then" rule hold on dates it was not
fitted on?

**Answer: yes for KXHIGHNY, clearly. It also transfers to the other series,
smaller in the other highs and larger in the lows.** Whether it helps our own
quotes, rather than the pool of touch makers, is not yet tested.

Figures are cents per contract for a small maker at the touch, weight
`min(size, 10)`, fill price 0.10-0.90, 95% intervals from resampling event
dates. "Outcome window" means 8-16h before close for highs and 16-24h for
lows.

## Method

1. Split each series' event dates in half by calendar order.
2. On the first half only, bin fills by second of the hour (30s bins) and take
   the contiguous run of bins, at most 5 minutes, whose fills had the most
   negative total five-minute P&L (`src/clock.py`).
3. On the second half, compare fills inside that window with fills outside it.
4. Placebo: slide a window of the same length to all 120 positions of the hour
   on the test dates and rank the fitted one.

## KXHIGHNY

Fitted on 95 dates: **:52:00-:57:00**. On the 95 dates it never saw
(`analysis/clock_all.out.txt`):

| | five-minute markout |
|---|---|
| fills inside the window (13% of fills, 8% of the hour) | **-0.82 [-1.23,-0.37]** |
| fills outside | +0.31 [+0.22,+0.39] |

- Skipping the window raises the pool's five-minute P&L in the outcome window
  by 78% (from 1,008 to 1,796 per event date in weighted cents; the gain is
  +788 [+351,+1,185]).
- Placebo rank 3 of 120: only two other positions of the hour did worse, and
  both overlap the fitted window.

It does not depend on the choices made (`analysis/clock_kxhighny_variants.out.txt`):

| variant | window found | test dates | inside | outside |
|---|---|---|---|---|
| window capped at 2 minutes | :52:00-:54:00 | 95 | -1.49 [-2.00,-0.84] | +0.27 |
| fitted on 30% of dates | :52:00-:56:30 | 133 | -1.01 [-1.38,-0.60] | +0.24 |
| fitted on 70% of dates | :51:30-:54:30 | 57 | -1.26 [-1.77,-0.78] | +0.28 |
| 30-minute markout | :52:00-:54:30 | 95 | **-1.76 [-2.58,-0.86]** | +0.15 |
| 60-second markout | :52:00-:54:00 | 95 | -0.45 [-0.88,+0.02] | +0.68 |
| last 8h before close | :14:00-:19:00 | 27 | -0.22 [-2.75,+1.81] | +0.89 |
| 16-48h before close | :04:30-:05:00 | 99 | +0.23 [-0.07,+0.48] | +0.31 |

- In the outcome window the fitted window starts at :52 (once :51:30) whatever
  the split, length cap or horizon, and ranks worst or near-worst of the 120
  placebo positions each time.
- The loss is still there at 30 minutes, so it is information, not a temporary
  push. At 30 minutes the pool loses money in the outcome window without the
  rule (-94 per date) and makes money with it (+938).
- Outside the outcome window the fit lands on unrelated minutes and nothing
  holds out of sample: an observation only matters while the day's high is
  still being decided.
- Settlement P&L inside against outside is -0.52 against +0.80, with intervals
  that overlap. Settlement cannot confirm it on one series.

## Does it transfer?

KXHIGHNY's window was applied unchanged to every other series. None of their
dates were used to choose it, so all of them are out of sample
(`analysis/clock_transfer.out.txt`).

| group | measure | inside | outside | difference |
|---|---|---|---|---|
| other highs pooled (9 series, 198 dates) | 5 min | -0.24 | +0.06 | -0.30 [-0.59,+0.01] |
| | 30 min | -0.81 | -0.27 | -0.54 [-1.18,+0.06] |
| | settlement | -1.18 | -0.31 | **-0.87 [-1.69,-0.03]** |
| lows pooled (9 series, 196 dates) | 30 min | -4.54 | -1.17 | **-3.37 [-6.05,-0.36]** |
| | settlement | -6.98 | -3.44 | **-3.54 [-4.65,-2.39]** |

- **Other highs:** same sign on all three measures, about a quarter of
  KXHIGHNY's size, and significant only at settlement. KXHIGHCHI and KXHIGHMIA
  are significant at five minutes on their own; KXHIGHLAX shows nothing.
- **Lows:** the largest effect of all. A fill in those five minutes costs about
  3.5c more at settlement than a fill in the rest of the hour, on 14,142 fills.
  KXLOWTNYC is the cleanest: -6.63 inside, +0.21 outside.
- For lows the five-minute tape markout is not usable (5-17% of prints have a
  two-sided tape mid), so settlement and 30 minutes are the measures.
- The per-series rows in the output are 57 comparisons; a few will cross zero
  by chance. The three pooled groups are the test.

Fitting each series' own window instead works less well. Most of the other highs have
only 33 test dates each until their Jul-Nov tape finishes downloading, and in
the lows the five-minute target used for fitting is the unreliable one. Seven
of the nine lows still put their fitted window between :51:30 and :58:30.

## What it means

- The hourly observation is a scheduled information release, and it is traded
  on within one to two minutes at :52-:54 past the hour.
- A clock rule captures it with no speed and no model: no quotes from about
  :51:30 to :57:00 during the outcome window. A narrower :52:00-:54:00 gets
  most of it for KXHIGHNY.
- It matters most where the edge thesis lives: in the thin lows it is worth
  several cents per fill.
- In Avellaneda-Stoikov terms this is variance arriving in lumps. The remaining
  variance is not `sigma^2 (T - t)` but a sum of hourly jumps, so reservation
  price and spread should step, not glide.

## Not established

- **Our fills.** All of this is the pool of touch makers. A replay with
  per-fill logging is needed to see what the rule does to our own quotes.
- **Queue cost.** Pulling quotes for five minutes an hour gives up queue
  position. In KXHIGHNY, where queues are long, that may cost more than in the
  lows, where they are short. Not measurable from the tape.
- **Which report it is.** I have not matched the window to station
  observation timestamps. The start at :52 is consistent with hourly airport
  observations taken shortly before the hour, but that is inference from the
  price data.
- **A sharper rule.** A reading should matter only when the running maximum
  is within a degree or two of a strike. That needs the stations' observation
  history, which is public, and would let the rule stand down on fewer hours.
- **Other minutes.** Only one window per hour was fitted. Five-minute
  automated readings and special reports could add smaller windows.

## Commands

```bash
python toxicity.py build --out data/markouts_all.npz --series <all weather series>
python toxicity.py clock --data data/markouts_all.npz
python toxicity.py clock --data data/markouts_all.npz --series KXHIGHNY --horizon 1800
python runs/20261112_toxicity/analysis/clock_transfer.py data/markouts_all.npz
```
