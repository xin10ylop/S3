# Final Report — Polymarket BTC Up/Down Strategy Research

Concluded 2026-07-27. 81 logged experiments (`results/RESULTS.md`), 9 months of
microstructure data, 6 days of live paper trading, a 120-day / 34,560-window
independent sweep, and a second full research pass.

---

## BOTTOM LINE

**The strategy is UNPROVEN, not dead — and the honest answer is still: do not fund it.**

The current tradeable number (EV given fill, last 60 days) is **+1.68¢/share on 565
fills, 95% CI [−0.96, +4.21]**. The point estimate is positive. The confidence interval
includes zero. That is the whole project in one line.

Even in the branch where the edge is entirely real, at a $100 bankroll it earns
**~$0.54/day** at responsible sizing — with a daily standard deviation 6.4× larger than
the daily edge.

Cost of reaching this conclusion: **$8.79 paper, $0 real.**

---

## 1. THREE CORRECTIONS TO EARLIER CONCLUSIONS

This project produced four confident claims that later analysis overturned. All four
came from the same error: concluding from small samples.

### Correction A — the historical baseline was inflated by a data gap
`5m_master.parquet` is **missing 54 consecutive days** (2026-05-13 → 07-05). The n=822
"historical" sample was **82% February-April**. "Robust across months" was never tested
on mid-May through June. On exactly those missing days the same method measures
**+2.48¢, not +5.5¢**. The famous "+5.56¢, t=6.45" was a survivorship artifact of a hole
in the data file.

Corrected historical figure: **+4.97¢ given fill** (the headline used a mid−0.01 entry
proxy; resting at the actual best bid costs 0.33¢ more).

### Correction B — "book depth killed it" was FALSE
High-power test (n=822): thinnest queue quartile +4.12¢ vs thickest +4.14¢, **p=0.995**.
117 historical signals already had 300+ share queues and won 90.6%. My "20× deepening"
compared a historical *median* to live observations in the historical *p90*. Fill rate is
flat across the whole period (82.3% first 60 days vs 83.1% last 60).

### Correction C — "the strategy is DEAD" was premature
That verdict came from a 30-day window (n=240 fills, −0.98¢). Properly powered:

| Period | n fills | Win rate | EV/share | 95% CI |
|---|---|---|---|---|
| Last 30 days | 244 | 85.66% | −1.61¢ | [−6.13, +2.62] |
| **Last 60 days** | **565** | **89.03%** | **+1.68¢** | **[−0.96, +4.21]** |
| Full 120 days | 1,031 | 89.62% | +2.15¢ | [+0.23, +3.98] |

Every one of these is consistent with the others. The 30-day slice was noise.
**Verdict: UNPROVEN.** Your −$8.79 live result at n=29 is **1.15σ** — it never could
have settled anything.

### What *is* confirmed
- **Adverse selection is real**: given-fill 89.62% vs not-filled 96.74%, gap −7.12pp,
  **z=−3.30, p=0.0010**, survives a price control and appears in every price band. My
  n=44 retraction of this was itself underpowered noise. It costs ~1.2¢ of headline EV.
- **The historical edge was genuine as executed**: survives every stress variant —
  queue×2 +4.78¢, queue×5 +4.67¢, below-only-fills +4.52¢, last-in-queue +4.80¢. Not a
  paper artifact.
- **What actually changed**: not depth, not fill rate. The one monotone variable is
  **tape activity** — median prints backing the signal price fell from 175-190 (April)
  to 88-113 (late July). Volume roughly halved. Decay is gradual drift
  (−0.0056 log-odds/day, p=0.072), **not** a regime break (changepoint scan gives a
  permutation-corrected p=0.14).

---

## 2. EVERY HYPOTHESIS TESTED

| Hypothesis | Status | n | EV/share | Reason |
|---|---|---|---|---|
| **EFC-M btc 5m given fill, 60d** | **UNPROVEN** | 565 | **+1.68¢ [−0.96,+4.21]** | Positive point estimate, CI straddles zero |
| EFC-M btc 5m given fill, 120d | MARGINAL | 1,031 | +2.15¢ [+0.23,+3.98] | Just clears zero; includes stronger April regime |
| ETH 5m given fill, 30d | UNPROVEN | 255 | +2.70¢ [−1.51,+5.70] | Best of the alts, same CI problem |
| DOGE / SOL / XRP 5m given fill | DEAD | 166/223/334 | +0.12¢ / −1.36¢ / negative | All CIs include or sit below zero |
| **Post-close maker @ 0.99** | **DEAD on capacity** | 4,997 fills | +0.96¢ tape, **0¢ real** | Live book: 105k-1.18M shares already resting; **0 of 70 snapshots had room at the top tick, in any asset**. ~1000:1 oversubscribed |
| Post-close taker (converged) | DEAD | 79 | +1.36¢ | Fires in 2.7% of windows; winning token has no offers — a latency race |
| Cross-asset lead-lag | DEAD | 13,298 pairs | — | Pooled gap p=0.146; alts price themselves at p=0.70 |
| Late resting bids below touch | DEAD | — | −0.6¢ to −7.5¢ | Systematically toxic at every level below the top tick |
| All 15m variants | NOT A FINDING | 1-6 | — | At open+60s a 15m window is 6.7% elapsed — price never reaches the band |
| Crypto liquidity rewards | DEAD | 8,831 markets | — | Zero have rewards configured |
| Book-depth conditioning | DEAD | 822 | — | p=0.995 — falsified the "thinner pond" thesis |
| Price-band sub-restriction | DEAD | 287 | — | Every band's lower bound negative |

**Useful side-discovery:** tick size is **dynamic** — 0.01 inside [0.10, 0.90], **0.001
outside**. Bots already rest at 0.999 post-close, which is why that trade is unreachable.

---

## 3. THE FUNDING ARITHMETIC (why "unproven but positive" still means "don't fund")

Assume the edge is entirely real at +1.68¢/share:

- Kelly at 89.03% win / 0.8735 entry = 13.3% of bankroll. **Quarter-Kelly = $3.33/trade.**
- At 8.5 fills/day: **$0.54/day expected**, daily σ **$3.47** — 6.4× the edge.
- Over 60 days: expected +$32, σ $27, **~12% chance of being underwater** even if real,
  with a realistic 20-25% drawdown along the way.
- **To earn $10/day at responsible sizing you need ~$1,800, not $100.** At $100 the
  honest ceiling is **$15-20/month** in the branch where it works.
- Calibrated probability the true current EV given fill exceeds zero: **~70-75%**;
  exceeds +1¢: ~50%; exceeds +3¢ (worth your time): **~20%**.

A "0.5%/day return" sounds spectacular. $0.54/day is what it actually is.

**The one defensible exception** — and it is research, not income: risk **$25-30**
placing real 3-4 share orders purely to calibrate the fill model against your simulator.
Your paper fill logic is the least-validated component in the project and cannot be
validated any other way. Budget it as an expense you expect to lose.

---

## 4. WHAT WOULD CHANGE THE ANSWER

1. **Cross-validate the two fill models** (~2 hours, no new data). The book-based model
   rests at the actual best bid (entry 0.8820, 88.3% fill, −0.18pp adverse selection);
   the API proxy rests at trade-median−0.01 (entry 0.8749, 82.7% fill, −7.12pp). These
   are *different orders, 0.7¢ apart, never run on the same windows*. Reconciling them
   could move the estimate by more than a cent in either direction.
2. **More paper data.** Halving the current CI needs ~1,340 fills ≈ 5-6 months at 8.5/day.
3. **Real micro-fills** ($25-30) to validate queue behaviour empirically.
4. **Untested angles**: non-crypto liquidity rewards (actually configured, unlike crypto —
   median $0.68/day per $100), and longer-duration markets with a *correctly re-derived*
   signal time (the 15m failures were my artifact of reusing a 5m offset, not a real test).

---

## 5. THE METHODOLOGICAL LESSON

Every edge this project found was real, and each lived in a place you cannot reach:
favorites that *don't* fill win 96.7%; the ones that fill win 89.6%. Post-close 99¢
tickets are a certainty behind a **$370k queue**. Alt markets have thin books and no
mispricing to go with them.

That is what an efficient market looks like from the inside — inefficient exactly where
you cannot transact, efficient exactly where you can.

The specific trap: a fill model that asks *"did trades occur at my price?"* rather than
*"would trades have reached me, at my place in the queue?"* The first flatters every
maker strategy.

And the recurring human error, mine, four times over: **concluding from small samples.**
n=29, n=44, n=79, n=240 each produced a confident verdict that a properly powered
measurement later overturned — in both directions. The only numbers in this report worth
trusting are the ones with n in the hundreds and a stated confidence interval.
