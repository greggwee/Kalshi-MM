# Observation-driven fair value for the lows

Written 2026-11-13. Code: `src/obs.py`, `src/fairvalue.py`, `fetch_obs.py`,
`runs/20261113_fairvalue/analysis/fv_test.py` (output in `fv_test.out.txt`).

## Verdict

The fair value is built and it is well calibrated, but **it does not give us an
edge at any delay we can assume today**. The market already contains what the
station has reported by about three minutes after each report. The station
data is worth money only to someone who has it within about a minute.

| question | answer | evidence |
|---|---|---|
| Is the station model right about what the station knows? | Yes | calibration table, 2,938 station-days out of sample |
| Does it find profitable orders by itself, 3 minutes after a report? | No | +0.78c [-1.13, +2.61] after fee, 121 dates |
| Does it add to the price, 3 minutes after a report? | No | weight in the blend is 0.00 to 0.06 |
| Does it add to the price with no delay at all? | Yes | blend +6.29c against price alone +3.73c |
| Is "favourites win more than priced" a standing mispricing we could take? | No | the taker on the favourite earns, the maker left holding the favourite loses |

So the +4c the informed takers earn in the lows (the holdout result in
`runs/20261112_toxicity/clock_edge.md`) is a **speed edge**, and possibly a
forecast edge. It is not something a slower reader of the same reports gets.

## What was built

**Station data.** `fetch_obs.py` downloads every report for the 15 settlement
stations from the public Iowa Environmental Mesonet archive into
`data/obs.db`: 831,000 reports since 2026-04-28. Three kinds of information:

- 5-minute readings, whole degrees C (Central Park has none);
- the hourly report at about :51 to :54, to 0.1 C;
- four times a day, the 6-hour minimum to 0.1 C, the only public number that
  sees between readings.

The settled low is within 1F of the lowest report of the day on 97.7% of
station-days; on 1.8% it settles more than 1F lower (the station saw a dip
between reports).

**The model.** The low can only end at or below the lowest temperature seen so
far, so the model is the distribution of the further drop,
`D = settled low - running low`, read off earlier days with the same hour of
the day, the same gap between the current temperature and the running low, and
the same precision of the running low. A market that pays for a low between
`lo` and `hi` is worth the share of past days whose drop lands in that range.
No forecast goes in.

**The blend.** A logistic regression of the outcome on the traded price and
the station model, fitted separately for five blocks of the day. The weight on
the station model is how much it knows that the price does not.

**The test.** 372,011 aggressive orders in the 15 low series on 121 event dates
from 2026-07-14, each scored with models fitted only on earlier dates (trailing
90 days, refitted weekly). The price is the touch the order took. P&L is to
settlement, after the taker fee, weight min(contracts, 10), with 95% intervals
from resampling event dates.

## Results

### 1. The value of the reports dies in about three minutes

Taker P&L after the fee on the orders each fair value liked by 2c or more.
"Delay" is the assumed time between a report's observation time and the moment
we know it (hourly report / 5-minute reading).

| delay | station model alone | price alone | price + station | station weight, hours 4-8 |
|---|---|---|---|---|
| 0 / 0 | +2.40 [+0.77, +4.00] | +3.73 [+2.31, +5.15] | +6.29 [+4.70, +7.90] | 0.20 |
| 1 min / 5 min | +1.52 [-0.25, +3.25] | +3.70 [+2.28, +5.10] | +5.18 [+3.59, +6.76] | 0.12 |
| 3 min / 15 min | +0.78 [-1.13, +2.61] | +3.95 [+2.50, +5.35] | +3.89 [+2.28, +5.49] | 0.06 |
| 10 min / 30 min | +0.12 [-1.80, +2.07] | +4.04 [+2.62, +5.44] | +3.25 [+1.56, +4.86] | 0.03 |

Read down the "price + station" column against "price alone": the station
data adds 2.6c with no delay, 1.5c at one minute, nothing at three minutes.

### 2. The model is calibrated; it is the market that knows more

Every 10 minutes of every station-day, out of sample: predicted chance that the
low ends at least 1F below the running low, against how often it did.

| predicted | hours 0-4 | hours 4-8 | hours 8-12 | hours 12-18 | hours 18-24 |
|---|---|---|---|---|---|
| 2-10% | | 9% | 10% | 9% | 5% |
| 10-30% | | 21% | 21% | 19% | 21% |
| 30-50% | 42% | 40% | 36% | 36% | 44% |
| 50-70% | 62% | 60% | 49% | 47% | 63% |
| 70-90% | 83% | 78% | | | 75% |
| 90-98% | 94% | | | | |

At the moments people chose to trade, the same model is confidently wrong far
more often. Examples from the output: at 9 am the model gave 99% to Washington
staying above 66F; the market priced it at 12c and the low came in at 65F that
evening. The market had the forecast. The station model cannot.

### 3. "Favourites win more than priced" is the taker's information

The recalibrated price likes takers who take the favourite side, and those
orders earned +3.95c [+2.50, +5.35] after the fee. If that were a standing
mispricing, a resting order that ended up holding the favourite would earn it
too, at a better price. It does not:

| price band | taker took the favourite | maker left holding the favourite |
|---|---|---|
| 0.20-0.40 | +2.60 [+0.77, +4.39] | -2.63 [-4.19, -1.04] |
| 0.60-0.80 | +2.01 [-0.29, +4.21] | -6.42 [-9.93, -3.00] |

(P&L before fees.) Whoever chooses the moment wins; the side does not matter.
An order of ours sent at an arbitrary moment has no claim on the +3.95c.

The +3.95c has also been shrinking: +6.93c in August, +2.01c in September,
+2.09c in October, -4.69c [-7.78, -1.47] over 11 dates in November.

## What this does not establish

- **How fast the reports really are public.** The delays above are
  assumptions. The archive stamps each report with its observation time, not
  the time it reached the public feeds. This is the number that decides whether
  the speed edge is open to us.
- **Whether the informed takers use the station at all.** The edge at zero
  delay is consistent with it. A faster private feed, the Weather Company's own
  page (which is the settlement source), or forecast updates would look the
  same on the tape.
- **Our own fills.** Every row is someone else's order at a price that existed
  for them. Nothing here is a simulation of orders we would have sent.
- **Highs.** Only the lows were tested.

## Data-quality notes

- A few 6-hour minimum groups in the archive are sensor glitches (Central Park,
  2026-09-19: a 55F minimum on a day that never went below 63F). `DayState`
  drops any 6-hour minimum more than 2 C below every reading so far.
- A 6-hour minimum counts only when its six hours lie inside the climate day.
- The climate day is midnight to midnight local standard time all year.

## What I would do next

1. **Measure the real publication delay.** A small logger that polls the
   public report feed every few seconds and stores when each report first
   appears, running beside the recorder. Two weeks gives the delay per station
   and, set against the recorded quotes, how many seconds the price takes to
   move after a report is public. If quotes stay stale for 30 seconds or more
   after publication, the speed edge is open to a script; if they move within
   a few seconds, it is not.
2. **Use the model defensively in the Central Park markets.** The model says
   when a strike is nearly decided. A maker there can stop quoting the side the
   station has already ruled out. This needs recorded books to test.
