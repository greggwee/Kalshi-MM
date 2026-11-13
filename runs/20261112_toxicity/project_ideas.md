# Research project shortlist (2026-11-12)

Eight projects, ranked. Each one starts from a plain economic idea, says what
is new about applying it here, shows what your own data already says, and
names the test that would kill it. Probe outputs are in
`analysis/idea_probes.out.txt` (script: `analysis/idea_probes.py`).

Figures are cents per contract for a small maker at the touch, with 95%
intervals resampled by event date, unless stated otherwise.

## What shapes the list

**Your constraints as a retail trader**

- Small capital, so capital tied up per contract matters as much as edge.
- REST polling at a few seconds, so nothing that needs to be first by
  milliseconds.
- No queue priority in liquid books: 100k+ contracts already rest at the
  touch in KXHIGHNY's dead strikes.
- No ability to out-forecast: a September 2026 paper finds the Kalshi
  temperature market beats the best public forecast (the National Blend of
  Models) by about 10% in error from its first hour, and that combining public
  forecasts does not beat the market
  ([arXiv:2609.23969](https://arxiv.org/abs/2609.23969)).

**Your advantages**

- Zero maker fees on weather series.
- You can be selective: quote one side, some minutes, some strikes. A firm
  with volume targets cannot.
- Edges worth a few hundred dollars a week are too small for firms.
- You now record 21 series with the touch every 3s, and have the full tape for
  about 200 event dates.

**What the probes say about the baseline**

- One-minute mid changes have autocorrelation +0.009 (47,520 pairs, July
  books). Prices neither bounce nor trend, so there is nothing to harvest from
  reversal. A maker earns only where the spread exceeds the information in the
  flow.
- So every idea below is a way of choosing *where, when or which side* that
  holds, not a better spread formula.

---

## 1. The observation clock: stand down when the thermometer reports

**Idea.** Scheduled news. Equity makers widen before earnings and macro
releases because flow right after a release is informed. Here the release is
the hourly station observation.

**Evidence (strong, NYC only).** KXHIGHNY, 14-22 UTC on the outcome day,
maker five-minute markout by minute of the hour:

| minutes | share of orders | 5-minute markout |
|---|---|---|
| :50-:54 | 13% (8% if uniform) | **-0.99 [-1.40,-0.57]** |
| every other 5-minute bin | | +0.10 to +0.47 |

- Holds before the Aug 12 settlement change (-0.97 [-1.62,-0.36], 63 dates)
  and after (-0.87 [-1.27,-0.43], 92 dates). The rest of the hour earns +0.34
  and +0.31.
- Outside the outcome window the same minutes are harmless (+0.03).
- The other six highs and three lows show nothing in that bin. Each station
  reports on its own minute, so their effect, if any, sits elsewhere.

**What is new.** A per-station release calendar for prediction markets, and
the question of how many minutes the informed window lasts.

**Project.**
1. Map each station's observation minute and publication lag; find each
   series' toxic minutes from the tape (minute-by-minute, not 5-minute bins).
2. Check the same for the lows' night window and for 5-minute ASOS data.
3. Rule: no quotes in the window. Compare maker P&L with and without, out of
   sample by date.

**Retail fit.** Excellent. It is a clock rule, needs no speed, and removes
about an eighth of fills that carry all of the loss.

**Kill test.** The gain disappears on dates after the rule was fitted, or
quotes pulled for five minutes lose queue position worth more than the gain.

## 2. Favourite-longshot bias as a one-sided quoting rule

**Idea.** Bettors overpay for longshots and underpay for favourites
(Thaler and Ziemba 1988; Snowberg and Wolfers 2010). Burgi, Deng and Whelan
(2026) document it on Kalshi, larger for takers than makers. A maker can
choose to be only the seller of longshots.

**Evidence.** Maker settlement P&L by the price of the contract the *taker*
bought, all ten weather series, 200 event dates:

| taker paid | maker P&L | maker return on stake |
|---|---|---|
| 0-3c | +0.97 [+0.80,+1.11] | +1.0% |
| 3-6c | +1.36 [+0.34,+2.19] | +1.4% |
| 6-10c | +1.70 [+0.29,+3.01] | +1.9% |
| 10-20c | +2.04 [+0.08,+3.90] | +2.4% |
| 20-50c | +0.8 to +1.0, interval spans zero | |
| 50-65c | **-2.04 [-3.71,-0.26]** | -4.9% |
| 65-97c | -0.3 to -1.4, interval spans zero | |
| 97-100c | **-0.57 [-0.86,-0.24]** | -38% |

- Makers who sold cheap contracts won; makers who sold expensive ones (which
  means they bought the longshot) lost.
- The last row is the trap in symmetric quoting: bidding 1-3c for a longshot
  loses over a third of the stake.

**What is new.** The bias conditional on hours to close and series, measured
at the touch with date-clustered errors, and turned into a quoting side rule
instead of a betting rule.

**Project.**
1. Calibration curve (win rate against price) by hours-to-close bucket, with
   isotonic regression and date-bootstrap bands.
2. Stability by month. Burgi et al. report the bias shrinking over time.
3. Rule: quote only the side that sells the contract priced below about 20c;
   never bid the longshot. Test walk-forward.

**Retail fit.** Mixed. The edge per contract is real but selling a 5c contract
ties up 95c for a day or two. The 0-3c bucket is unreachable: the queue there
is hundreds of thousands of contracts. The 3-20c range is the workable part.

**Kill test.** Out-of-sample interval spans zero, or the bias is confined to
price levels with unreachable queues.

## 3. Run a book across the ladder, not one market at a time

**Idea.** A bookmaker does not care which horse wins if liabilities are
balanced; the profit is the overround. An event's strikes are mutually
exclusive and exhaustive, so exactly one pays $1. Being short YES on every
strike is a locked payout of $1 against whatever was collected.

**Evidence.** July books, 20 events, 31,543 minute snapshots:

- Sum of mids across the six strikes averages **1.024**.
- Sum of bids exceeds 1.00 in 21% of minutes and 1.02 in 7%.
- Sum of asks is below 1.00 in 0.9% of minutes.
- Caveats: books were 34s stale and not synchronous across strikes; part of
  the excess is the 1c tick floor on dead strikes; taker fees (about 3-4c
  across six legs) remove the pure arbitrage. As a maker with no fees the
  overround is the edge.

**What is new.** Avellaneda-Stoikov and Feil-Nendel treat one market with
inventory risk in that market. In a ladder the risk is the worst-case
liability across outcomes, not the sum of positions. Extending the inventory
penalty to that is a genuine and natural extension, and I found no paper that
does it for prediction-market ladders.

**Project.**
1. With the new 3s touch data, measure the overround cleanly: size, time of
   day, which strikes carry it.
2. Model: quote YES asks on all strikes, skew each by its marginal effect on
   worst-case liability. Start with a rule, then put it in the Feil-Nendel
   objective.
3. Check how Kalshi margins mutually exclusive positions. The event object
   reports `collateral_return_type: MECNET`; whether short-all-strikes is
   netted decides the capital cost. This needs your account to confirm.

**Retail fit.** Good if netting applies: the hedge is built in, and risk is
bounded by construction.

**Kill test.** Overround net of adverse selection per completed set is not
positive on dates not used to design the rule, or collateral is not netted.

## 4. Price the thin market off its liquid sibling

**Idea.** Price discovery happens in the liquid market; related thin markets
follow with a lag (the lead-lag literature, Hasbrouck's information shares).
You cannot out-forecast the market, but you can borrow its forecast.

**Why here.** Central Park, Newark, Trenton and Philadelphia share an air
mass. KXHIGHNY trades about 290k contracts a day at 1-2c; KXHIGHTEWR and
KXHIGHTTTN trade 4k-9k at 8-13c spreads. The same holds for a city's high and
the next morning's low.

**What is new.** Using the ladder-implied temperature of one market as the
fair value for another. The September paper builds the implied mean for
single cities but does not look across them.

**Project.**
1. Convert each ladder to an implied mean temperature (mids as probabilities,
   as in the paper).
2. Regress the thin market's implied-mean change on lagged changes of the
   liquid one. Report the lag at which the liquid market stops helping.
3. If the lag exceeds a few seconds: quote the thin market around the
   sibling-implied value, and pull quotes when the sibling moves.

**Data.** Needs synchronous books. The recorder has covered these series
since today; three to four weeks gives about 25 event dates per pair.

**Retail fit.** Very good. The thin markets are too small for firms and the
signal is free.

**Kill test.** No predictive lag beyond about 5 seconds, or the thin market's
spread already reflects the sibling.

## 5. Did changing the settlement source change who is informed?

**Idea.** A natural experiment. On Aug 12 2026 Kalshi moved daily temperature
settlement from the NWS climate report to The Weather Company. If informed
flow came from the quirks of the NWS report (rounding, preliminary versus
final values), adverse selection should have fallen after the change.

**Evidence (suggestive only).** Maker settlement P&L, 0.10-0.90:

| series | before Aug 12 | after |
|---|---|---|
| KXLOWTAUS | -2.39 [-3.87,-0.90] | -0.41 [-2.26,+1.42] |
| KXLOWTLAX | -1.69 [-3.02,-0.33] | -1.01 [-2.41,+0.45] |
| KXLOWTNYC | -0.22 [-1.52,+0.94] | +0.80 [-0.38,+1.84] |
| KXHIGHNY | +0.63 [-0.15,+1.46] | +0.43 [-0.32,+1.22] |

All three lows improved by 1-2c; no single difference is significant, and
summer versus autumn is a confound.

**Why it matters beyond curiosity.** It decides whether pre-August data is
valid for backtests at all.

**Project.** Difference-in-differences across series with date-clustered
errors; pre-trend check; the same calendar window one year earlier from
`/historical` as a seasonal control.

**Retail fit.** Pure analysis on data you can already pull. No capital.

**Kill test.** Not applicable; either answer is useful.

## 6. Queue position as the asset

**Idea.** When the tick is large relative to the value at stake, the spread is
set by the tick and the profit goes to whoever is first in the queue
(the large-tick literature). Front-of-queue orders are filled by small
uninformed orders; back-of-queue orders are filled only when someone clears
the level, which is the informed case.

**Evidence so far.** In the toxicity model, orders that walk the book are the
most toxic feature (-0.50c per standard deviation). That is the back-of-queue
fill.

**Why you might have it.** Daily markets open fresh each morning, so the queue
resets every day and being early is free.

**Project.** From the recorder's depth and the tape: markout as a function of
the share of the level an order consumed, and fill probability as a function
of queue ahead. Then compare "post at the open and never move" against
"requote".

**Data.** Needs book depth, so a few weeks of recording. Small real fills of
your own would calibrate it properly.

**Kill test.** Markout does not depend on how much of the level was consumed.

## 7. Liquidity incentives: is the subsidy larger than the toxicity?

**Idea.** The exchange pays for resting liquidity in chosen markets. A subsidy
changes where passive quoting pays, independent of the spread.

**Facts.** Kalshi's programme scores resting orders once a second by size and
distance from a reference price, and splits a weekly pool by share. Pools on
Nov 12 ranged from about $1,800 to $12,900 per event, many labelled low
competition. It requires at least the target size (over 100 contracts) resting
on both sides.

**Blocker to check first.** The help page says international, non-US users are
ineligible for rewards. Whether you qualify depends on your account status,
which I cannot see.

**Project.** For each incentivised market: reward per dollar-hour of resting
depth, against the adverse selection measured the same way as in the toxicity
study. Record a handful of them with the existing recorder.

**Retail fit.** Good if eligible; pools are too small for firms.

**Kill test.** Ineligible, or reward per contract resting is below the
five-minute adverse move in those markets.

## 8. Put the toxicity forecast in the quoter (continuation)

Already shown out of sample on KXHIGHNY: dropping the worst-scored 30% of
fills lifts the five-minute markout from +0.22c to +0.48c. Remaining work is
the per-side filter in `src/pmm_quoter.py`, a replay test, and fitting it on
other series. Ideas 1 and 2 are special cases a fitted model should
rediscover; having them as explicit rules is the check that it does.

---

## Suggested order

1. **Observation clock (1)** and **settlement-source study (5)**: both run on
   the tape you already have, this week.
2. **Favourite-longshot side rule (2)**: tape only, one more week.
3. **Ladder book (3)** and **sibling pricing (4)**: start once three to four
   weeks of 3s touch data exist. Keep the recorder running.
4. **Queue position (6)** and **incentives (7)**: after that; both need books,
   and 7 needs the eligibility answer.

## How each result should be reported

- Event dates as the unit: date-bootstrap intervals, walk-forward splits,
  date count next to every number.
- A rule fitted on one period is judged on dates it did not see.
- Five-minute and 30-minute markouts are the test statistics. Settlement P&L
  needs about 600 dates of one series to resolve 0.3c, so use it as a sanity
  check or pool series.
- Each project has a kill test written before the data is looked at.

## Sources

- Burgi, Deng, Whelan (2026), "Makers and Takers: The Economics of the Kalshi
  Prediction Market", CESifo WP 12122.
- "Prediction Markets Beat the Weather Forecast on Tomorrow's High
  Temperature", arXiv:2609.23969.
- "The Favorite-Longshot Bias in Prediction Markets: Evidence from
  Polymarket", arXiv:2609.12878.
- "Unravelling the Probabilistic Forest: Arbitrage in Prediction Markets"
  (Polymarket ladder arbitrage).
- Kalshi help centre: Weather Markets; Liquidity Incentive Program.
