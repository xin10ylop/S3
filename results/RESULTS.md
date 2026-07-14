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
