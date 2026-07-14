# Hypothesis Register — Polymarket BTC Up/Down

Rules: every hypothesis gets tested on TRAIN only; promising ones go to VALIDATION; final report numbers come from untouched TEST. Every result (win or loss) gets a row in `results/RESULTS.md`. Nothing is silently dropped.

## Data splits (chronological, enforced in code — `research/splits.py`)
| Family | Train | Validation | Test (untouched) |
|---|---|---|---|
| 5m | 2026-02-12 → 2026-04-15 | 2026-04-16 → 2026-04-30 | 2026-05-01 → 05-12, 07-06 → 07-07 (+ Telonex 07-08→14 if pulled) |
| 15m | 2025-10-11 → 2026-02-28 | 2026-03-01 → 2026-04-15 | 2026-04-16 → 05-12, 07-06 → 07-07 |
| 1h | 2025-10-11 → 2026-03-15 | 2026-03-16 → 2026-05-15 | 2026-05-16 → 2026-07-12 |
| 4h | 2025-10-15 → 2026-03-15 | 2026-03-16 → 2026-05-12 | 2026-07-06 → 07-07 + walk-forward folds |

Fee-epoch note: 15m/1h/4h were fee-free before 2026-01-05/03-06; all backtests apply the per-window `fee_rate` that was live at that time, and headline results are also reported under the CURRENT 0.07 schedule (worst case).

## A. Side selection (pre-open, which side to buy)
- **H1** Binance momentum carry-over: sign of spot return over last 15/30/60/120/300s before open predicts outcome (>52% acc).
- **H2** Book imbalance at t−10s (Up bid depth vs ask depth within 5c) predicts outcome.
- **H3** Pre-open signed taker flow (last 30/60/120s) predicts outcome.
- **H4** Previous-window outcome & streak length predict next outcome (momentum or reversal).
- **H5** Running 15m/1h market's live mid (strike already set, covering this 5m window) predicts the 5m outcome.
- **H6** Pre-open Up-token price itself deviates from 50c and is *informative* (crowd knows) — or *anti-informative* (crowd herds; fade it).
- **H7** ML ensemble (GBM) over H1-H6 features beats any single signal; report OOS accuracy and calibration.
- **H8** Time-of-day / day-of-week conditional side bias.

## B. Skip filters (when NOT to trade)
- **H9** Skip when pre-open book is deep/tight (heavy MM competition ⇒ signals already priced).
- **H10** Skip extreme Binance vol regimes (top decile 60s RV) — outcome noise dominates entry edge.
- **H11** Skip dead-volume windows (bottom decile pre-open trade count) — no exit liquidity for scalps.
- **H12** Edge concentrates in specific hours (e.g., US cash open, Asia) — trade only those.

## C. Lifecycle windows (map what prices do in each phase)
- **H13** Pre-open: prices should be ≈50c (strike not yet set ⇒ martingale). Fade any pre-open price >52c / <48c by buying the cheap side (maker).
- **H14** First 30-60s after open: underreaction to spot-vs-strike distance ⇒ fair-value model Φ(d/σ√τ) vs mid, trade divergence.
- **H15** Mid-life sharp-move reversion: Up-token moves ≥8c within ≤10s revert ≥3c within 60s (fade via maker).
- **H16** Last 60s convergence: model-prob >97% side still quoted ≤92c ⇒ buy (maker or taker after fee).
- **H17** Last 5s: taker-buy near-certain side (model prob >99.5%, price ≤97.5c); fee at p≈0.97 is only ~0.2c/share.
- **H18** Post-close/settlement: 25-54s between close and resolution with quotes still live — is the determined outcome ever quoted <99c?
- **H18b** Late-window MAKER bid on near-certain side: get filled by impatient winner-holders cashing out early (capital velocity sellers, not informed flow) — the adverse-selection-free version of H16/H17.

## D. Longer markets (independent searches, not ports)
- **H19** Full A+B+C search on 1h family (275 gap-free days; classic hourly ET markets).
- **H20** Full search on 4h family.
- **H21** 15m full search + fee-epoch A/B: did edges that existed pre-fee (before 2026-01-05) survive the fee?

## E. Cross-market & oracle
- **H22** 5m↔15m coherence: 15m implied prob vs composition of its three 5m constituents; trade violations.
- **H23** 1h↔4h and 15m↔1h disagreement.
- **H24** Chainlink lag: Binance leads the resolution feed by ~1-2s. In the final seconds, trade against quotes that haven't incorporated the Binance move the oracle is about to print.
- **H25** Chainlink−Binance basis (mean −$18, std $13, drifts): in CONTESTED windows (mid 0.3-0.7 near close), the basis decides the outcome — an oracle-aware d beats the crowd's Binance-only d.

## F. Game theory / microstructure
- **H26** Thin-book fade: when depth-within-5c < $200, single trades move price 3c+; those moves revert — post maker fade quotes.
- **H27** Overreaction to sharp Binance 1s candles mid-window: token overshoots model fair value; fade.
- **H28** Two-sided pre-open maker straddle: rest buy-Up and buy-Down at 47-49c; profit when both fill vs one-sided adverse selection cost.
- **H29** Stale-quote sniping generalized: measure MM reaction latency to spot moves; in slow regimes/hours taker-lift quotes that lag by >1s after a >5bps move (whole window, not just close).
- **H30** Queue-position value at 50c pre-open: how often does the pre-open 50c bid fill and what's the conditional outcome (adverse selection measure).

## G. Anomalies / data-driven
- **H31** Consistency sweep: windows where result contradicts oracle sign; duplicated/zero-volume windows; fee_rate oddities; DST effects on ET-defined 1h markets.
- **H32** Address-level competitor fingerprinting via onchain fills (Pro-gated on Telonex — parked).
- **H33** Volume migration: did 15m behavior change when 5m launched (2026-02-12)?
- **H34** Weekend vs weekday microstructure differences.
