# Final Report — Polymarket BTC Up/Down Strategy Research

Session date: 2026-07-14. Data: 9 months of Polymarket BTC Up/Down microstructure
(5m/15m/1h/4h families), Chainlink oracle 1s feed, Binance 1s klines. 48 logged
experiments (`results/RESULTS.md`), all backtests tick-replayed with honest fills
(queue-join maker model, bookcurve/size-checked taker fills, real per-epoch fees).

---

## RANKED VIABLE STRATEGIES

### #1 — Early-Favorite Continuation, maker entry ("EFC-M") — FOUND ✅

**The one strategy that survived every validation attack.**

| | |
|---|---|
| Market | Polymarket `btc-updown-5m` (every 5 minutes, 288/day) |
| Signal | At **open + 60s**, the market's own mid is in **[0.85, 0.97]** for either side (mirrored for Down) |
| Entry | **Maker**: rest a limit buy on the favorite at the favorite's current best bid; cancel if unfilled by open+90s |
| Exit | **Hold to settlement** (≈4 min). Winner redeems $1.00, fee-free |
| Fees | Zero (maker leg + redemption). Fee-proof by construction |
| Side model | None needed — the market's own price is the signal. Symmetric on Up/Down |

**Out-of-sample record (c/share):**

| Split | Period | EV/share | Win rate | Fills/day |
|---|---|---|---|---|
| train | 2026-02-12 → 04-15 | +5.59¢ | 93.9% | 7.5 |
| validation | 2026-04-16 → 04-30 | +2.47¢ | 91.2% | 7.5 |
| **test (untouched)** | 2026-05-01→05-12 + 07-06/07 | **+3.57¢** | **91.6%** | 9.4 |
| **July holdout (Telonex, never touched)** | 2026-07-08 → 07-13 | **+3.66¢** | **91.9%** | 10.3 |

- Positive **every month** Feb→Jul (worst single split-month +2.4¢; test-July subset +5.8¢).
- Latency-proof: identical EV at 500ms and 2000ms reaction. Not a race.
- Both sides symmetric (+3.4¢ up / +4.2¢ dn), all hour-blocks positive, weekends *better* (+5.4¢ vs +3.4¢), 75% of days positive.
- **Why it exists**: the crowd underreacts to the first decisive move of each window
  (confirmed independently by a 91k-event study: sharp moves *continue* +0.8-1.0¢, never revert).
  Favorites at minute 1 are priced ~0.90 when their true win rate is ~0.94.
  Fills come from early longshot-buyers and profit-takers — measurably NOT informed flow
  (win-given-fill 91.6% ≈ unconditional 92%; every other maker strategy we tested collapsed on this metric).
- **Economics per trade**: avg entry ≈ 0.88 → ~4.1% return per filled trade, ~8-9 fills/day.
- **Capacity**: median $3.5k of qualifying flow crosses the level during the 30s fill window;
  $100 clips fill fully in 87% of signals. Practical size **$50-100/clip** ($400-900/day deployed).
  Taker-path capacity decays: +0.38¢ at $200 clips.
- **Bankroll path (test period, $5 clips reinvested)**: $100 → $128.21 in 14 days (+28.2%), max drawdown −21%.
  Loss tail: 8.4% of trades lose ~88¢/share — size accordingly; $5-10 clips on $100 keeps ruin risk negligible.
- **Max drawdown driver**: clustered losses on violent reversal minutes; no skip filter tested removed them without killing EV (vol filters hurt).

**Execution playbook**
1. Subscribe to the 5m market's book (WSS). At `wts+60s` compute mid.
2. If mid ∈ [0.85, 0.97] (favorite = Up) or mid ∈ [0.03, 0.15] (favorite = Down): place a GTC limit buy on the favorite token at its current best bid, $50-100 notional.
3. Cancel at `wts+90s` if unfilled (≈9% of signals). Optional taker fallback (still +1.0¢ EV, pays ~0.6¢ fee).
4. Hold to resolution; redeem. No sell leg, no fee, no race.

### #2 — Early-Favorite Continuation, taker entry ("EFC-T") — fallback only

Same signal; cross the spread at open+60s instead of resting. Train +3.84¢ → val +1.48¢ → test +1.01¢ but **July holdout −1.33¢** (BBO-only cost model on holdout data). The taker fee + spread consume the edge in the current regime. Use strictly as an occasional fallback for unfilled maker orders (≈9% of signals), or skip unfilled signals entirely.

### Not ranked — rejected at validation
- 1h analogue (open+720s favorite): train +4.09¢ but **val −5.03¢** → rejected.
- Everything else below.

---

## WHAT WAS TESTED AND RULED OUT (48 experiments, full table in RESULTS.md)

**Side-selection signals (pre-open):** Binance momentum at 6 horizons — dead (≤50% acc; the crowd already prices it). Pre-open flow imbalance 51.3% acc — real, too small. Book imbalance — weakly anti-informative. Previous-outcome reversal (P(repeat)=0.477 on 15m, 5σ) — real, too small to cross costs. Hour-of-day — dead. ML ensemble (GBM, walk-forward): 52.3% acc / AUC 0.534 — taker-dead; maker version +1.1¢ but regime-unstable (April-driven). Pre-open price itself is the best predictor (52.3% acc) and correctly calibrated mid-range.

**Pre-open trading:** the user's manual scalp shape (buy 51¢ → sell 55¢, abort) replayed on all 18k train windows: **−1.3¢/trade** — aborts (−19¢ avg) overwhelm +4¢ targets. Two-sided 48/48 straddle: −2.0¢. Pre-open tails (mid>0.56) are genuinely underpriced +2-3¢ raw but month-unstable and fee-eaten. Selectivity (ML-gated maker entries) reaches +1.1¢ but April-dependent → not FOUND.

**Late-window / settlement:** snapshot "edges" of +3-15¢ on near-certain sides at close−120..−2s across all families are **fill mirages** — honest replay: 15m all-leads negative, 1h inconsistent, 5m only positive at lead≤2s with <1s latency (colocation race, rejected by realism rule). Late maker bids on the certain side: fills arrive exactly when the outcome flips (win-given-fill ≈ price). Settlement window: books converge; nothing executable.

**Oracle/model edges:** resolution rule pinned (last Chainlink tick ≤ boundary; 2/38.7k irreducible mismatches). Chainlink−Binance basis (−$18±13) modeled explicitly: contested-zone oracle model −1 to −9¢ everywhere beyond lead 3s. Model-market divergence buyer (model ≥0.90 vs mid ≤0.82): **−1.6¢, win 74% vs claimed 90%** — the market beats every model we built; its only exploitable flaw is the early-favorite discount (#1).

**Cross-market:** 5m↔15m same-close partial-order violations exist (~1% of ticks, ~2¢) — true arb, but two taker legs + racing make it unprofitable at our scale.

**Microstructure:** sharp-move fade (H15/H27): moves *continue* (+0.8¢ at 10-60s, 91k events) — fade dead, and continuation < round-trip cost. Thin-book fade: subsumed (same result). 15m/4h favorite analogues: 15m flat/negative; 4h untestable (14 signals in 5 months).

**Families:** 15m — most bot-efficient, nothing survived. 1h — negative/inconsistent everywhere tested. 4h — too few windows for any conclusion at these signal rates.

**Context:** maker-rebate pool ≈ $48k/day (5m) + $10k/day (15m) — the professional game on these markets is high-frequency market-making farming spread+rebate; that is not accessible to a $100 bankroll, but explains why naive taker edges are arbitraged out within seconds.

---

## VALIDATION ATTACKS APPLIED TO #1 (all passed)
1. Chronological splits; config locked on train; val used once; test untouched until final gate.
2. Latency 0.5s → 2s: EV unchanged (not a race).
3. Band/timing perturbation: positive plateau o45-o75; decays smoothly (o120 +0.4¢, o180 −0.7¢) — mechanistic, not a grid artifact.
4. Fee schedule: maker pays zero (fee-epoch-proof); taker variant charged per-window historical rate.
5. Fill honesty: maker requires tape prints through the level beyond the displayed queue (join-back); win-given-fill ≈ unconditional (no adverse selection).
6. Stale-quote artifacts: engine rejects stale BBO (2s) and unsized quotes; bookcurves used at 250ms grid.
7. Concentration: no month, day, side, or hour-block carries the result; 75% of days positive.
8. Second holdout: 7 fresh days pulled from an independent vendor (Telonex) after the test gate — see addendum.

## LIMITATIONS
- 5m data gap 2026-05-13 → 07-05 (collector outage): test = 12 days May + 2 days July + 7 Telonex July days.
- Backtest cannot model our own presence in the book (others may re-price around a persistent $100 bid at o60). Start at $5-20 clips and scale on live fill/win telemetry.
- The edge is a behavioral bias, not a law: monitor monthly EV; kill switch at trailing-200-trade EV < 0.
