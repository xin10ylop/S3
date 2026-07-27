# Final Report — Polymarket BTC Up/Down Strategy Research

Concluded 2026-07-27. 80 logged experiments (`results/RESULTS.md`), 9 months of
microstructure data, 6 days of live paper trading, and a full second research pass
after the first strategy failed.

---

## BOTTOM LINE

**Do not fund anything. No strategy tested clears its costs in the current market.**

Total cost of reaching this conclusion: **$8.79 of paper money, $0 real.**

One thread remains genuinely open (ETH 5m) and needs ~2 more months of data before
it can be called either way. Everything else is dead, and the reasons are measured
rather than guessed.

---

## 1. THE ORIGINAL STRATEGY (EFC-M) — DEAD, and now fully explained

**Rules**: on `btc-updown-5m`, at open+60s, if either side is priced 0.85-0.97, rest a
maker buy at that favorite's best bid, cancel at open+90s, hold fills to settlement.

### The research was correct
Replaying all **792 historical signals** through the exact order lifetime
(o60.5→o90, queue join-back) under four escalating queue assumptions:

| Fill assumption | Given-fill EV | Wilson lower bound |
|---|---|---|
| Base (real queue) | +4.69¢ | **+2.58¢** |
| Queue ×2 | +4.47¢ | +2.31¢ |
| Queue ×5 | +4.33¢ | +2.13¢ |
| At-level fills disallowed | +4.18¢ | **+1.87¢** |

The edge was real **as executed**, under every assumption. This was not a backtest error.

### What killed it
| | Historical (n=715 filled) | Current (n=240 filled) |
|---|---|---|
| Filled win rate | 93.01% | 86.25% |
| Entry price | 0.8825 | 0.8723 |
| Adverse-selection gap | 5.69pp | **11.6pp** |
| **Filled EV** | **+4.69¢** | **−0.98¢** |

Decline in filled win rate: **z=2.79, p=0.005**. Adverse selection was *always*
present (p=0.0004 historically) but the edge absorbed it. It has since roughly
doubled while the price discount narrowed — a 5.67¢ swing.

**The residual edge is structurally untradeable.** Windows where our bid never fills
still win 97.9% (+10.9¢); windows where it fills win 86.25%. You cannot identify the
former in advance — they are *defined* by price not coming to you. The taker route is
also negative: **−0.77¢/share** after fees.

### Two claims I made and retracted
- **"Market makers deepened books 20× and killed the edge"** — FALSE. A high-power test
  (n=822) found depth irrelevant: thinnest quartile +4.12¢ vs thickest +4.14¢ (p=0.995);
  117 historical signals already had 300+ share queues and won 90.6%. My "20×" figure
  compared a historical *median* to a handful of live observations in the historical *p90*.
- **"Fills are adversely selected"** — retracted at n=44, then reinstated at n=287
  (p=0.00015). The retraction was itself underpowered. Both flip-flops came from reading
  small samples; only the properly-powered measurements should be trusted.

---

## 2. SECOND RESEARCH PASS — every hypothesis tested

| # | Hypothesis | n | Result | Status |
|---|---|---|---|---|
| 1 | **Post-close maker @ 0.99** | 2,507 | +1.00¢/sh, **Wilson lower +0.85¢** — the only candidate whose worst case was profitable | **DEAD on capacity** (below) |
| 2 | Post-close taker (converged side) | 79 | 79/79 wins, +1.36¢ net | DEAD — fires in only 2.7% of windows; lower bound 95.4% vs 98.6% breakeven; needs ~280 straight wins |
| 3 | **ETH 5m early-favorite** | 347 | Uncond +4.95¢ (lo +1.74¢); **given fill +2.70¢ (lo −1.51¢)** | **UNPROVEN** — only live thread |
| 4 | SOL / XRP / DOGE 5m | 136/204/128 | Lower bounds −2.51¢ / −2.93¢ / −1.33¢ | DEAD |
| 5 | Cross-asset lead-lag (BTC → alts) | 13,298 pairs | Pooled gap +0.584pp, t=1.45, **p=0.146**; alts price themselves at p=0.70 | DEAD |
| 6 | All 15m variants (every asset) | 1-6 | At open+60s a 15m window is 6.7% elapsed — price never reaches the band | NOT A FINDING — missing data |
| 7 | Liquidity rewards on crypto up/down | 8,831 markets | **0 have rewards configured** | DEAD (exists on weather/sports/politics: median $0.68/day per $100) |
| 8 | Price-band sub-restriction | 287 | Every band's Wilson lower bound negative | DEAD |
| 9 | Book-depth conditioning | 822 | p=0.995 | DEAD (falsified the whole "thinner pond" thesis) |

### Why the best candidate died
The post-close trade was genuinely attractive: after a window closes the outcome is a
**fact**, yet winners still sell tickets worth $1.00 for 99¢ to get their capital back
30 seconds early. Post-close flow runs **37,322 SELL vs 9,397 BUY**. The market's own
convergence identifies the winner 99.576% of the time with no oracle needed.

Then the live order book answered it:

| Measurement | Value |
|---|---|
| Resting queue at 0.99 post-close | **374,136 shares ($370,395)** |
| Incoming sell flow per window | 413 shares |
| Our $100 clip | 101 shares |
| Windows of flow to clear the queue | **906 (≈75 hours)** |
| Snapshots where we'd fill | **0 of 125** |

The 1¢ premium is real and it is **906× oversubscribed**. It is free money, which is
exactly why a third of a million dollars is already parked in front of us. On the taker
side the winning token has *no offers at all* — the 2.7% of windows where one appears
is a latency race we would lose.

---

## 3. THE META-LESSON

Every single edge found in this project was real, and every one lived in the part of
the distribution that cannot be transacted:

- Favorites that **don't fill** win 97.9%; the ones that fill win 86.25%.
- Post-close 99¢ tickets are a certainty — behind a **$370k queue**.
- Alt-asset markets have thin books and no mispricing to go with them.

That is not bad luck. **It is what an efficient market looks like from the inside.**
Prices are inefficient exactly where you cannot reach them, and efficient exactly where
you can. Any backtest on this venue that does not model queue position and fill
selection with hostility will produce a large, entirely fictitious edge — mine did, at
t=6.45, and it survived four validation gates before live paper trading exposed it.

**The specific methodological failure to carry forward:** a fill model that asks "did
trades occur at my price?" instead of "would trades have reached *me* at my place in
the queue?" The first question flatters every maker strategy. The second is the only
one that matters.

---

## 4. RECOMMENDATION

**Fund nothing today.** No strategy has a positive lower bound *and* accessible capacity.

**The one live thread — ETH 5m.** Unconditional +4.95¢ (lower bound +1.74¢) is real, but
the tradeable given-fill figure is +2.70¢ with a lower bound of −1.51¢, and it shows the
same adverse-selection signature as BTC (filled 90.6% vs unfilled 98.9%, z=3.90).
Resolving it needs ~470 filled trades ≈ **2 months** of paper data. It costs nothing to
collect: point the existing bot at ETH in paper mode and check monthly. Do not fund it
before the given-fill lower bound clears zero.

**If you want to keep hunting**, the honest ranking of what is left:
1. **Non-crypto liquidity rewards** — the only measured, *structurally accessible* income
   found (rewards are actually configured there, unlike crypto). Median $0.68/day per $100
   deployed, best-case $20.91/day. Requires modeling adverse selection, which was not done.
2. **Longer-duration markets with correct signal timing** — the 15m/1h failures were an
   artifact of reusing a 60-second offset built for 5m windows. A properly re-derived
   signal time (e.g. 20% into the window) has never actually been tested.
3. Everything else in this venue is exhausted.

**If you want my actual opinion:** this venue is efficiently priced at retail scale.
Two weeks of systematic work found one genuine historical edge that decayed, and one
genuine current edge that is 906× oversubscribed. That is a market working correctly.
The professional participants here are earning spread and rebates at size with
infrastructure a $100 account cannot replicate. I would stop trading this venue and
apply the same methodology — hostile fill modeling, Wilson lower bounds, paper-first
validation — somewhere less contested.

The process worked. It cost $8.79 to learn all of this instead of $100.
