# Running Results Table — every test, win or lose

Conventions: acc = P(predicted side wins). EV in cents/share after fees unless noted.
All rows TRAIN split unless marked. Nothing here has touched test data.

| # | Hypothesis / test | Family | Split | Result | Verdict |
|---|---|---|---|---|---|
| 1 | H4 prev-outcome repeat rate | 5m | train | P(repeat)=0.491 (n=18k, −2.3σ) | weak reversal signal, keep as feature |
| 2 | H4 prev-outcome repeat rate | 15m | train | P(repeat)=0.477 (n=13.5k, −5.3σ) | REAL reversal; alone ≈52.3% acc buying anti-streak side |
| 3 | H4 prev-outcome repeat rate | 1h | train | P(repeat)=0.478 (n=3.7k, −2.6σ) | same direction |
| 4 | H4 prev-outcome repeat rate | 4h | train | P(repeat)=0.480 (n=854, −1.2σ) | same direction, underpowered |
| 5 | H4 after 2-streak | 15m/4h | train | continue=0.466/0.439 | reversal strengthens with streak |
| 6 | H8 hour-of-day Up bias | 5m | train | max dev 2.7% ≈1.4σ | DEAD |
| 7 | H1 Binance momentum carry (sign of ret 5-300s at o−10) | 5m | train | acc 0.492-0.499 all ≤50% | DEAD as momentum; weak reversal tilt |
| 8 | H3 pre-open flow imbalance sign | 5m | train | flow120: acc 0.513 (3.4σ) | real but small |
| 9 | H2 book imbalance sign at o−10 | 5m | train | acc 0.492 (−2.2σ) | anti-informative (passive side gets hit); weak |
| 10 | H6 pre-open mid deviation sign | 5m | train | acc 0.523 (6.1σ) | strongest single pre-open signal |
| 11 | H6 calibration: is pre-open price exploitable? | 5m | train | mid 0.44-0.56: taker edge ≤0, market well calibrated. Tails (mid>0.56 / <0.44): P(win)=0.60-0.68 vs ask → +2-3c raw taker edge | tails underconfident; but see #12 |
| 12 | Tail momentum-follow stability | 5m | train | edge by month: Feb −0.2c, Mar +1.5c, Apr +2.4c (o−10); taker fee ~1.7c kills most | MARGINAL — maker-entry variant still open |
| 13 | H31 oracle-sign vs result consistency | all | all | 83/38.7k mismatches → 9/11 non-1h fixed by last-at-or-before oracle read; 2 irreducible (0.005%) | resolution rule pinned: last Chainlink tick ≤ boundary; fixed in windows_all |
| 14 | 1h family contamination (daily/candle markets in slug set) | 1h | — | 10,400 → 6,615 windows after joining on vault slug↔wts | data fix, done |
| 15 | H16/H24 late-window model-vs-market snapshot | 5m | train | at c−120..c−10: market ≥ model (Gaussian model overconfident, fat tails). At c−2: model-certain side wins 95.6% but asks avg 0.90 → +5.3c snapshot EV on $200 book curves, stable by month | led to #16 |
| 16 | Close-sniper tick backtest (lead 2s, 300ms lat, cap 0.95) | 5m | train | n=905, win 72.8%, EV +2.43c/sh, 14/day; but by month: Feb −1.6c / Mar +7.6c / Apr −3.5c; dies at lat 1000ms (−2.5c) and lead 3s (−0.6c) / 5s (−6.4c) | FRAGILE — rejected in this form. Note: stale-fill bug fixed in engine (BBO freshness + size checks) |
| 17 | Late-window snapshot calibration | 15m/1h/4h | train | snapshot edges +3..+15c at c−120..c−2 on model-certain side | led to #18 — beware |
| 18 | Late-convergence taker replay ($200) | 15m | train | ALL leads negative (−2.4..−9.7c): big-size demands select informed quotes; win rate drops 0.95→0.84 | DEAD — adverse selection eats it |
| 19 | Late-convergence taker replay ($20) | 15m | train | still negative (−1.3..−3.8c) | DEAD on 15m |
| 20 | Late-convergence taker replay ($20) | 1h | train | L120 +3.1c (n=217), L60 −1.6, L30 +0.0, L10 +1.8 | inconsistent across leads — not trustworthy as-is |
| 21 | Pre-open scalp replay, user shape (mid-sider, 51→55, abort 60s) | 5m | train | EV −1.32c (n=11.9k); aborts avg −19c overwhelm +4c targets | user shape unprofitable unconditioned — selectivity is the missing piece |
| 22 | Pre-open straddle 48/48 both sides | 5m | train | EV −1.95c | DEAD as-is |
| 23 | H18b late-window maker bid on certain side | 1h | train | L60 −3.9c (n=294), L120 −1.6c (n=371); win-given-fill ≈ price | DEAD — fills arrive exactly when outcome flips |
| 24 | H18b late-window maker bid on certain side | 15m | train | L60 +0.2c (n=1934), L30 −3.6c | DEAD — same conditional-fill efficiency |
| 25 | H25 contested-zone oracle taker | 5m | train | L3 +3.6c (n=1401) but L5 −2.8c, L10 −5.8c; pred_edge 27c vs realized ≪ → model useless in genuine toss-ups | DEAD except speed-game L3; adopting realism rule: reject edges that exist only at lead ≤3s |
| 26 | H25 contested-zone oracle taker | 15m | train | L5 −8.6c, L10 −6.6c, L30 −4.7c | DEAD |
| 27 | H15/H27 sharp-move event study (6c/5s, 10c/5s) | 5m | train | moves CONTINUE: +0.9c at t+10s, +0.8c at settle (n=91k/51k events); P(revert)≈0.43-0.46 | fade DEAD; crowd underreacts. Continuation < round-trip cost → no taker strategy either |
| 28 | H15/H27 sharp-move event study (8c/10s) | 15m | train | same: +0.8c continuation, settle +1.0c (n=87k) | fade DEAD on 15m too |
| 29 | H7 ML side-selection walk-forward (5m, pre-open feats) | 5m | train+val WF | acc 52.3%, AUC 0.534; overconfident tails; taker edge negative | signal real but small; taker DEAD |
| 30 | ML-gated maker entry (join bid, margin 0.04, hold) | 5m | train | +1.06c/sh but Mar +0.7 / Apr +3.1 (April-driven) | MARGINAL, unstable — same regime-dependence as tail-follow |
| 31 | Favorite-longshot calibration across life stages | all | train | favorites (0.85-0.97) systematically cheap EARLY in window on 5m; 1h late-window hints | led to #32 |
| 32 | **Favorite-buyer replay: 5m open+60s, mid 0.85-0.97, taker, hold** | 5m | train | **+3.84c/sh, win 94.4%, 8.2/day, ALL months + (2.9/5.9/2.4), latency-proof to 2s, both sides, all hour-blocks, 75% days +** | CANDIDATE #1. Decays: o120 +0.4, o180 −0.7 → the bias is the first-minute repricing lag. $200 size → +0.38c (capacity ≈ $50-100/clip) |
| 33 | Favorite-buyer 15m analogue (o180) | 15m | train | −0.52c, months mixed | does not transfer to 15m |
| 34 | Favorite-buyer 1h analogue (o720) | 1h | train | +4.09c (n=94) | promising but thin → val |
| 35 | Favorite-buyer 4h analogue (o2880) | 4h | train | n=14, noise | UNTESTABLE at 4h sample |
| 36 | 1h close−120 favorite (0.80-0.97) | 1h | train | +0.43c, months alternate sign | DEAD |
| 37 | VAL GATE: 5m o60 favorite (locked config) | 5m | **val** | **+1.48c/sh, win 92.3%, n=155, 10.3/day** | **SURVIVES VALIDATION** |
| 38 | VAL GATE: 1h o720 favorite (locked config) | 1h | **val** | −5.03c (n=54; Mar −15c) | FAILS validation — rejected |
| 39 | H22 cross-market partial-order arb (5m vs 15m, same close) | 5m/15m | train(Apr) | violations ~1% of ticks, ~2c gross, needs 2 taker legs + racing | real arb, unprofitable at our scale — bots welcome to it |
| 40 | Model-market divergence buyer (pm≥0.90/0.95, gap 8-10c) | 5m | train | −1.61c / −0.95c, win 74% vs claimed 90%+ | DEAD — market beats model everywhere; confirms edge #32 is the market's own bias, not model superiority |
| 41 | Maker-entry variant of #32 (rest at fav bid o60→o90, hold) | 5m | train | **+5.59c/sh, win 93.9%, 7.5 fills/day, all months + (4.9/7.9/3.5)** — win-given-fill does NOT collapse (profit-taker flow, not informed) | CANDIDATE #1-M |
| 42 | VAL GATE: maker variant | 5m | **val** | **+2.47c/sh, win 91.2%, n=113** | SURVIVES |
| 43 | **TEST GATE (untouched): taker o60 b85** | 5m | **test** | +1.01c/sh (n=148, win 91.2%; May +1.63, Jul −1.67 n=28) | survives, weak |
| 44 | **TEST GATE (untouched): maker o60 b85** | 5m | **test** | **+3.57c/sh (n=131, win 91.6%; May +3.17, Jul +5.80)** | **FOUND — survives untouched test** |
| 45 | Weekend slice of #32 | 5m | train | weekday +3.4c / weekend +5.4c | no skip needed; weekends better (thinner MM coverage) |
| 46 | Capacity of maker variant | 5m | train | median $3.5k qualifying flow/window beyond queue; $100 clip fills fully in 87% of signals; taker path decays at $200 (+0.38c) | practical clip $50-100 |
| 47 | Bankroll sim, TEST trades, $5/clip reinvested | 5m | test | $100 → $128.21 in 14 days (+28.2%), maxDD −21%, 71% days positive | loss tail = −88c/share on 8.4% of trades |
| 48 | Maker-rebate pool estimate | 5m/15m | all | ≈$48k/day (5m) + $10k/day (15m) rebate pool | context: the pro game is MM+rebates; out of scope for $100 bot |
| 49 | Fill-model paranoia on TEST maker: queue×2 / below-price-only | 5m | test | +3.47c / +2.66c (vs +3.57 base) | edge survives most conservative fill assumptions |
| 50 | **JULY HOLDOUT (Telonex 07-08→07-13, never touched): maker** | 5m | holdout | **+3.66c/sh, win 91.9%, n=62, 10.3/day** | **CONFIRMED — 4th independent period positive** |
| 51 | JULY HOLDOUT: taker | 5m | holdout | −1.33c/sh (n=69, win 88.4%; BBO-only cost model) | taker variant demoted to fallback-only |
