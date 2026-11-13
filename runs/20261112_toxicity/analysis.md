# Adverse selection on the tape, and restarting data collection (2026-11-12)

Read-only research on prod public data. No orders were placed anywhere.
Everything here is reproducible with `toxicity.py` (see the commands at the end);
raw outputs are in `analysis/`.

## What was done

1. **Recorder fixed and restarted.** It had not died on Jul 31: it ran until
   Aug 19 polling the market list it expanded at startup, and those daily
   markets had all closed. It now re-expands the series every 10 minutes.
2. **Tape recovered for the gap.** `backfill.py` reads Kalshi's `/historical`
   archive as well as the live API, so the Aug-Sep tape that had aged out of
   the live window is back. Only the books for Jul 31 - Nov 12 are lost.
3. **Per-print markout dataset** (`src/markouts.py`): one row per aggressive
   order with the maker's edge, markouts at 10s/60s/5min/30min and to
   settlement, and features known before the order arrived.
4. **Toxicity study** (`toxicity.py`): markout curves, time buckets split by
   period, and a walk-forward toxicity forecast.

## How to read the numbers

- Cents per contract, from the side of **a small maker resting at the touch**:
  the fill price is the best level the aggressive order consumed, and each
  order counts for `min(size, 10)` contracts.
- Fill price band 0.10-0.90.
- Brackets are 95% intervals from resampling **whole event dates**. The number
  of event dates is next to every figure.
- This is the P&L of the pool of touch makers, not of our strategy. Our
  quotes are a subset of that pool, selected by where and when we quote.

## The tape mid is usable from 5 minutes out

Most history has no books, so mid-based markouts use a mid built from the tape
(average of the last taker-buy and last taker-sell print). July 2026 has both
books and tape, so the same 31,783 prints were marked against each
(`analysis/tape_vs_book.out.txt`, 4 event dates, all seven high series,
rows where both references exist):

| reference | edge | 10s | 60s | 5min | 30min |
|---|---|---|---|---|---|
| tape mid | +1.03 | +0.76 | +0.42 | -0.12 | -0.23 |
| book mid | +0.89 | +0.66 | +0.14 | -0.24 | -0.25 |

The tape mid lags the book, so it flatters the maker by about 0.3c at 60s and
by 0.1c or less from 5 minutes. **Use the 5-minute and 30-minute columns; treat
60s as an upper bound.** In thin books only 7-30% of prints have a fresh
two-sided tape mid, so there the settlement markout is the one to trust.

## Finding 1: at the touch, the liquid highs pay the maker almost nothing

`analysis/curves_highs.out.txt`. KXHIGHNY, four periods:

| period | dates | edge | 5min | 30min | settlement |
|---|---|---|---|---|---|
| Apr 27 - May 31 | 35 | +1.34 | +0.22 [+0.07,+0.36] | +0.12 [-0.15,+0.44] | -0.40 [-1.17,+0.41] |
| Jun 1 - Jul 1 | 31 | +1.20 | +0.24 [+0.10,+0.36] | +0.28 [-0.03,+0.61] | +1.58 [+0.23,+2.86] |
| Aug 9 - Sep 14 | 37 | +1.33 | +0.21 [+0.06,+0.36] | +0.23 [-0.12,+0.60] | +0.35 [-0.95,+1.67] |
| Sep 15 - Nov 11 | 58 | +1.24 | +0.21 [+0.12,+0.32] | -0.06 [-0.32,+0.17] | +0.56 [-0.38,+1.51] |

- The maker is paid about 1.3c at the fill and keeps about 0.2c after five
  minutes. That is stable across all four periods, 161 event dates.
- At 30 minutes and at settlement no period is distinguishable from zero
  except June at settlement.
- The other six highs (Apr 27 - Jul 1, 66 dates each) are no better: 5-minute
  markout between -0.06 and +0.29 except KXHIGHPHIL, and settlement point
  estimates negative for AUS (-1.1 to -1.5), DEN and LAX.
- This agrees with the replay result in `runs/20261105-000744_demo/analysis.md`
  (fills earn about +0.9c and lose about 1.5c in 60s). The replay's 34s book
  bias is not what makes it lose: the pool itself keeps almost nothing.

An earlier cut of the same data, marked at each order's VWAP instead of the
touch, showed KXHIGHNY makers +0.94c to settlement. That gain belongs to the
makers resting behind the touch who are paid when an order walks the book; a
small maker at the touch does not get it. (The +0.96c for KXHIGHNY in the July
tape study counted every print at its own price, deep ones included, so it
has the same lift.)

## Finding 2: the 16h stand-down direction replicates; its size does not justify the rule alone

`analysis/buckets_kxhighny.out.txt`. The 16h cut was chosen on July data. Here
it is on the 66 dates before it and the 95 dates after:

| hours to close | Apr 27 - Jul 1: 5min | settlement | Aug 9 - Nov 11: 5min | settlement |
|---|---|---|---|---|
| 8-16h | +0.16 [+0.02,+0.29] | +0.54 [-0.24,+1.31] | +0.17 [+0.07,+0.27] | +0.59 [-0.14,+1.31] |
| 16-24h | +0.41 [+0.35,+0.46] | +1.08 [-0.51,+2.73] | +0.35 [+0.30,+0.41] | +0.92 [-0.33,+2.24] |
| 24-36h | +0.35 [+0.24,+0.46] | +0.21 [-1.11,+1.49] | +0.09 [-0.11,+0.26] | -0.52 [-1.97,+0.87] |

- 16-24h before close is better than 8-16h by about 0.2c at five minutes in
  both periods, so the direction holds out of sample.
- 8-16h is still not negative for the pool. The replay's loss in that window
  came from two event dates and one strategy.
- Worst hours are 15-18 UTC (+0.11 and +0.02 at five minutes): late morning to
  early afternoon in New York, when the day's high is forming.
- The settlement columns cannot separate any of these buckets: with about 95
  dates the interval is about +/-1.3c wide.

## Finding 3: the wide spreads in thin books are not free money

The series ranking on Nov 12 (in `recorder_config.yaml`) shows the original
highs are no longer illiquid (KXHIGHNY about 290k contracts/day at 1-2c), while
the daily lows have 5-19c touch spreads on 2k-60k contracts/day. First thin
series through the backfill, KXLOWTAUS (`analysis/kxlowtaus.out.txt`):

| period | dates | edge | settlement |
|---|---|---|---|
| Apr 30 - Aug 14 | 107 | +5.25 | **-2.50 [-4.05,-0.98]** |
| Aug 15 - Nov 11 | 89 | +5.78 | -0.15 [-2.18,+1.74] |

- The maker is paid 5-6c at the fill and gives it all back by settlement.
- The loss sits 16-24h before close: -3.14 [-4.83,-1.47] on 19,508 orders
  (the 03-12 UTC rows are the negative ones). That is the local night and dawn
  when the low is being set. It is the same mechanism as the highs' afternoon,
  at a different clock time.
- So a stand-down keyed on a fixed number of hours before close is the wrong
  shape. It should be keyed on when the outcome is realized: local afternoon
  for highs, local night to dawn for lows.
- Outside that window no bucket is distinguishable from zero.
- Eleven more thin series are still downloading; this is one series.

## Finding 4: toxicity is forecastable from pre-trade information

Walk-forward on KXHIGHNY: fit on earlier event dates, score the next block,
four folds, 97 test dates, 145,762 test orders. Features are only what is known
before the order arrives (time to close, hour, distance from 0.5, tape spread,
quote distance from the tape mid, recent move, recent flow in the direction
that would hit the quote, recent volume). Gradient boosting;
`analysis/model_kxhighny_300_quote.out.txt`.

| forecast decile | forecast 5min | realized 5min |
|---|---|---|
| 1 (most toxic) | -1.42 | -0.99 [-1.39,-0.61] |
| 2 | -0.23 | -0.15 [-0.30,+0.00] |
| 5 | +0.14 | +0.15 [+0.07,+0.22] |
| 9 | +0.73 | +0.76 [+0.65,+0.87] |
| 10 (least toxic) | +1.46 | +1.01 [+0.70,+1.30] |

| no-quote filter | fills kept | realized 5min | realized 30min |
|---|---|---|---|
| none | 100% | +0.22 [+0.14,+0.31] | +0.07 [-0.14,+0.26] |
| drop worst 10% | 90% | +0.36 [+0.29,+0.44] | +0.19 [-0.01,+0.38] |
| drop worst 30% | 70% | +0.48 [+0.40,+0.56] | +0.27 [+0.04,+0.50] |
| drop worst 50% | 50% | +0.61 [+0.53,+0.70] | +0.38 [+0.09,+0.66] |

(The 30-minute column is from a model trained on the 30-minute target.)

- The forecast is calibrated and monotone out of sample. All of the pool's
  five-minute loss is in the worst decile.
- The two features that carry it (ridge, cents per standard deviation):
  **same-direction volume in the last 60s (-0.33)** and **how far the quote
  sits from the tape mid (+0.34)**. A quote that is about to be hit by a run of
  same-side flow is the one that loses.
- Adding the incoming order's own size and sweep raises the lift (drop worst
  10% gives +0.51c), but a maker cannot refuse a fill on those, so they are
  diagnostic only. They do say: orders that walk the book are the informed
  ones, so quote small at the touch.
- Settlement P&L cannot confirm or refute the filter: its interval is
  +/-0.8c on 97 dates and every filter row sits at about +1.0 to +1.2.

## What this changes

1. **Decision 2 (adverse-selection cost on the edge): yes, and make it
   state-dependent, not per-series.** The forecast above is that cost in cents.
   Proposed shape: `edge_net = half_spread - maker_fee - E[adverse move | state]`,
   quote a side only when `edge_net` is positive, with the state being the
   pre-trade features. This goes in `src/pmm_quoter.py` as a filter on each
   side; no change to A-S math in `helper.py` and no risk limit is touched.
   Not implemented yet: it needs a replay test first.
2. **The stand-down should follow the outcome clock**, per series type, and is
   better learned (time-to-close and hour are model inputs) than fixed at 16h.
3. **Stop treating KXHIGHNY as the target because of its tape P&L.** At the
   touch it is a 0.2c market. The thin lows pay more at the fill and lose more
   after; whether a filter rescues them is the next question once their tape
   is in.
4. **A fair-value signal is now the main missing piece.** Both the afternoon
   highs and the dawn lows lose to people watching the thermometer. Filters
   only avoid that flow; an NWS-observation fair value could quote through it.

## What is still unknown

- **Strategy vs pool.** Every number is the pool of touch makers. Whether our
  quotes do better needs the replay with per-fill logging.
- **Queue position.** A small maker joining the touch is behind the queue and
  is filled more often by the large orders that clear the level. The
  `min(size, 10)` weighting does not model that.
- **Books.** The filter was tested on tape features only. The recorder now
  stores the touch every 3s and depth on change, so book features (queue size,
  imbalance, quote age) can be added in a few weeks.
- **Settlement power.** 97 event dates give about +/-0.75c on KXHIGHNY's
  settlement P&L, and +/-1.3c inside one time bucket. To see a 0.3c edge at
  settlement would take on the order of 600 dates of one series, or pooling
  many series with date-clustered errors. Five-minute
  and 30-minute markouts are the workable test statistics; settlement is a
  sanity check.
- **One series.** The model is KXHIGHNY only. It has not been fitted on other
  highs or on lows.

## Evaluation rules adopted

- Event date is the unit: bootstrap and walk-forward splits are by date, and
  the date count is printed with every figure.
- A rule chosen on one period is reported separately on dates it did not see.
- Features that describe the incoming order are kept out of anything used to
  decide whether to quote (`QUOTE_FEATURES` vs `ORDER_FEATURES`).
- Minimum before believing a sign: the date-bootstrap interval excludes zero
  on a period the rule was not chosen on. For settlement P&L that currently
  means nothing passes, and the write-up says so.

## Data and collection state

- `data/kalshi_backfill.db`: was 2.8M trades to Jul 3; now also KXHIGHNY to
  Nov 12 and KXLOWTAUS from Apr 30. Still downloading in the background when
  this was written: 11 more thin series from May 1, then the Jul-Nov gap for
  the other six highs. KXHIGHNY Jul 3 - Aug 9 has not been requested yet.
- `data/kalshi_recorder.db`: recording again since Nov 12, 252 markets in 21
  series. Touch quotes every 3s (table `tickers`), depth on change and at
  least every 5 minutes, full tape. Outcomes for the July markets were filled
  in.
- The recorder is a foreground process started from the research session. It
  stops when that session or the machine does. To run it yourself from a
  checkout of this branch: `python record.py`.
- Keyless API limits found today: 429s start somewhere below 8 requests/s in
  total across all processes on the machine. The recorder is capped at 5/s and the
  backfill at 3/s.

## Commands

```bash
python backfill.py --since 2026-07-01 --rps 3            # tape + outcomes, incl. /historical
python toxicity.py build --db data/kalshi_backfill.db --series KXHIGHNY,KXHIGHCHI,KXHIGHMIA,KXHIGHLAX,KXHIGHAUS,KXHIGHDEN,KXHIGHPHIL
python toxicity.py curves --split 26JUN01 26AUG01 26SEP15
python toxicity.py buckets --series KXHIGHNY --split 26AUG01
python toxicity.py model --series KXHIGHNY --horizon 300
python toxicity.py model --series KXHIGHNY --horizon 300 --features all
python runs/20261112_toxicity/analysis/tape_vs_book.py data/kalshi_recorder.db
```

`toxicity.py model` needs scikit-learn (added to `requirements.txt`).
