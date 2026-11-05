# Feil-Nendel model: verification

Model: "Optimal Market Making in Prediction Markets" (arXiv:2607.17991), `strategy.model: pmm`.
Everything here is a demo order, a simulated fill or a read of public data. No real-money order was placed.

## Summary

- The solver reproduces the paper's figures and table 2.
- It runs live on the demo exchange without order or position problems, and it fixes the last run's zero fills: it quotes at the touch, where A-S sat behind it.
- On recorded production data it does what the paper says relative to its benchmark: same mean, less dispersion.
- It does not make money there. Replay is slightly negative at best, on only two event dates.
- The cause is the paper's central assumption, that fills carry no information. Fills went against us by more than the edge captured.

## 1. Against the paper

`pmm_tool.py reproduce`, full output in `analysis/reproduce.out.txt`. Table 2, 10,000 paths:

| | mean P&L | std | mean abs q | VaR 5% | ES 5% |
|---|---|---|---|---|---|
| paper, optimal | 12.39 | 10.34 | 15.23 | 4.20 | 9.68 |
| ours, optimal | 12.38 | 10.44 | 15.44 | 4.41 | 9.72 |
| paper, baseline | 12.47 | 28.11 | 49.37 | 32.41 | 40.50 |
| ours, baseline with the inventory bound | 12.18 | 27.81 | 48.54 | 32.66 | 40.15 |
| ours, baseline without it | 12.40 | 35.58 | 58.77 | 45.64 | 62.55 |

Figure 1 spreads match to within 0.0002 and figure 2 skews to within 0.0001. The solution also satisfies the HJB equation by finite differences, and the closed forms at settlement hold (`tests/test_pmm.py`).

The paper's footnote 4 says its baseline has no inventory bound. Its table only matches the bounded run.

## 2. Live on demo

Two sessions on one account (`20261105-000744_demo`, `20261105-000857_demo`), 1h and 57min, sized 2 contracts with a limit of 10. Markets were picked by hand from recent prints, because the selector's page limit still misses most of them.

| check | result |
|---|---|
| orders left resting | 0, verified by the sessions and by listing the account afterwards |
| position mismatches | 0 |
| crossed quotes | 0 |
| fills | 11 (the last A-S hour had 0) |
| net P&L | -$0.23 and -$0.15, too few fills to mean anything |
| skew vs inventory correlation | -0.97, -1.00, -0.68, -0.92 where inventory moved |
| cycle time | median 160 ms, worst 9.3 s across a demo outage |
| solve time | at most 2.1 s, 26 solves |

| market | quoting | at or inside touch, bid / ask | our spread | market spread | prints | fills |
|---|---|---|---|---|---|---|
| NBA MIL@BOS (MIL) | 100% | 100 / 100% | 1.1c | 1.2c | 80 | 5 |
| NBA PHI@DET (PHI) | 100% | 100 / 77% | 1.2c | 1.0c | 71 | 1 |
| NFL BUF@MIN total 48 | 100% | 100 / 100% | 1.0c | 1.0c | 0 | 0 |
| NFL BUF@MIN total 53 | 100% | 100 / 100% | 1.0c | 3.0c | 0 | 0 |
| NFL TB@CHI total 45 | 100% | 100 / 100% | 1.0c | 3.0c | 0 | 0 |
| high temp OKC 67 | 99% | 98 / 21% | 4.5c | 6.2c | 2 | 1 |
| NCAAF TOL@SAC total 35 | 27% | 100 / 41% | 3.8c | 5.1c | 12 | 4 |

- The two KXRAINSHARD2 markets rejected every order with `insufficient_shard_balance`, and the reject-rate halt stopped each after 12. No market field marks these in advance.
- One market lost its book and stood down as designed (`no_two_sided_book`).
- A demo outage (HTTP 503, 01:02 UTC) failed some writes in both sessions, and both continued.
- Short positions stayed open at the end, since `flatten_on_exit` is off: 6.16 on TOL@SAC 35 and 2 on OKC 67, both on demo.

## 3. Read-only on production data

`analysis/prod_readonly.py` with `pmm_prod_config.yaml`: an unsigned client with no keys, so it cannot place an order. It selected KXHIGHNY-26NOV05-T70, took the calibration and the 16h stand-down, and held the 0.48 / 0.49 touch for all 120 cycles. An earlier 7-minute check on NY and LAX with the 8h config was at the touch 100% of the time, with 2 markets and a 1.04c spread.

## 4. Replay on recorded production data

Seven weather series recorded in July, 134 markets, sized 2 with a limit of 10. Simulated fills on a tape our quotes did not influence. "Myopic" is the paper's benchmark, the same model with both risk aversions at zero. Fees are zero: none of these series charged makers when recorded.

Standing down 8h before close, queue fill model:

| strategy | fills | net | per fill |
|---|---|---|---|
| A-S | 372 | -$34.34 | -9.2c |
| model | 19,368 | -$411.59 | -2.1c |
| myopic | 19,638 | -$429.93 | -2.2c |

Where that loss came from (`analysis/fill_autopsy.out.txt`, 34,919 contracts, per contract, after the no-book stand-down):

| hours to close | contracts | edge at fill | 60s markout | to settlement | total |
|---|---|---|---|---|---|
| 8-12 | 15,283 | +1.04c | -0.87c | -1.64c | -$251 |
| 12-16 | 10,458 | +0.81c | -0.47c | -1.18c | -$124 |
| 16-20 | 4,927 | +0.72c | -0.41c | +0.16c | +$8 |
| 20-24 | 1,270 | +0.75c | -0.32c | -0.52c | -$7 |
| 24-48 | 2,976 | +0.73c | -0.31c | -0.95c | -$28 |

Fills 8-16h out are 93% of the loss. The worst stretch is 17-22 UTC, the US afternoon, when the day's high is being observed.

Standing down 16h before close, calibration refitted to match:

| strategy, fill model | fills | net | per fill | std per market | worst market |
|---|---|---|---|---|---|
| A-S, queue | 238 | -$11.33 | -4.8c | $0.41 | -$2.55 |
| model, queue | 4,885 | -$28.88 | -0.6c | $1.64 | -$7.41 |
| myopic, queue | 5,212 | -$28.70 | -0.6c | $2.31 | -$9.84 |
| A-S, strict | 154 | -$7.92 | -5.1c | $0.47 | -$2.50 |
| model, strict | 3,670 | -$27.76 | -0.8c | $1.80 | -$7.90 |
| myopic, strict | 3,872 | -$19.64 | -0.5c | $2.51 | -$10.22 |

By series, model, queue: NY +$10.93, PHIL +$5.45, DEN +$0.12, MIA -$1.09, AUS -$8.13, CHI -$15.51, LAX -$20.64.

How far to trust this:

- **Two event dates.** Only 26JUL03 and 26JUL30 have fills, and strikes of one date are correlated. This cannot establish a sign, or that NY is better than LAX.
- **The 16h was chosen from this same data.** The improvement from moving the stand-down is in-sample.
- **Biased against quoting at the touch.** The recorded book refreshes about every 34s against the session's 2s, so in replay our quotes sit stale through moves. Live adverse selection should be smaller, by an unknown amount.
- **Biased in favor elsewhere.** Replay assumes our orders change nothing about others' behavior.

## 5. What this means for production

The model is verified as an implementation, not as a source of profit. Before risking more than a small test:

1. Measure real markouts at the smallest size. The replay's biases can only be resolved by real fills.
2. If they stay negative, the next model change is an adverse-selection cost on the edge, built the same way as the maker fee. In a 1c market with a half-cent of edge, the model would then stop quoting whenever expected adverse movement exceeds it.
3. Widen past KXHIGHNY only with more event dates.

`pmm_prod_config.yaml` is a small starting point: 1 contract, limit 5, session stop $10, market stop $5, 16h stand-down, KXHIGHNY. These are not recommendations.
