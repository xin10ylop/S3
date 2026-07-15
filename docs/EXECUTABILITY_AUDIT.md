# Executability Audit — EFC-M on live Polymarket (2026-07-14)

Verified against the LIVE exchange (gamma-api, clob, data-api all probed this session).

## Confirmed live facts

| Item | Live value | Impact on strategy |
|---|---|---|
| Market exists & discoverable | `GET gamma-api/markets?slug=btc-updown-5m-{wts}` returns the market ~24h ahead | discovery trivial, no race |
| Orders accepted | `accepting_orders: true`, from ~24h before window | resting at open+60s fine |
| Min order size | **5 shares** (≈$4.50 at 0.90) | $5 clips are the practical floor |
| Tick size | **0.01** on live 5m markets | bot quotes at book prices — always on-grid |
| Order types | GTC / GTD / FOK / FAK | GTC + cancel at +90s = our shape |
| neg_risk | false (binary market) | plain CTF exchange orders |
| Resolution | Chainlink BTC/USD data stream; **ties resolve Up** ("greater than or equal") | matches backtest labels (0 ties in 38.7k windows anyway) |
| Book access | `GET clob/book?token_id=` public, no auth, includes tick/min-size | paper mode needs no keys |
| Trade tape | `GET data-api/trades?market={condition_id}` public, per-outcome prints, 1s timestamps | paper fill sim identical in spirit to backtest (1s vs µs granularity noted) |
| Fees | Docs: taker-only 0.07·p·(1−p), makers free + 20% rebate share. ⚠️ live market object shows `maker_base_fee: 1000` (an on-chain max, not the charged rate) | **go-live checklist: verify $0 fee on first real maker fill** |
| Liquidity rewards | `rewards: min_size 50, max_spread 4.5` on these markets | resting ≥50 shares within 4.5¢ of mid may ALSO earn daily rebates — unmodeled upside |
| Heartbeat | Exchange cancels resting orders without heartbeat (~10-15s tolerance) | live executor runs a 4s heartbeat thread |
| Clock | `GET clob/time` for skew check | bot refuses to start at >1.5s skew; installer enables chrony |

## Deltas between backtest and live paper bot

1. Tape timestamps: research data had µs; data-api has 1s. Boundary-second trades
   (placement second) are counted — mildly generous by ≤1s of tape. The fill window
   is 29s, so the effect is small; watch fill-rate parity in the report.
2. Queue model identical (join behind displayed size; below-price prints always count).
3. Book snapshot for placement is REST-polled (~100-300ms newer than the backtest's
   250ms grid) — slightly MORE realistic.
4. Sub-second placement latency in live mode depends on order signing (~100-300ms) —
   the strategy tolerates 2000ms, margin is huge.

## Sizing (the "max bet" answer)

Tape-replay EV by clip (before own-impact effects, 75 days train+test):

| clip | full-fill % | EV ¢/share | $/day (8.8 signals) |
|---|---|---|---|
| $5 | 91.6 | 5.13 | 2.3 |
| $20 | 90.6 | 5.07 | 9.2 |
| $50 | 89.3 | 5.01 | 22.5 |
| $100 | 88.0 | 4.93 | 43.8 |
| $200 | 85.4 | 4.84 | 84.6 |
| $400 | 82.5 | 4.74 | 162 |
| $800 | 77.2 | 4.53 | 299 |
| $1500 | 69.3 | 4.30 | 501 |
| $3000 | 53.4 | 3.95 | 820 |

EV/share decays slowly; in-model the profit-maximizing clip is >$3000. BUT the model
cannot price the market's reaction to our own standing bid (signaling, queue games,
MMs adjusting). Treat $400+/clip as unproven. Bot policy: adaptive clip =
min(hard cap, 10% of bankroll, 30% of live-measured qualifying flow), hard cap
raised manually 5→20→50→100→… only while live telemetry stays inside the
backtest bands. Bankroll is NOT the binding constraint (win 92% / loss −0.9 →
full Kelly ≈ 20%/trade; we cap at 10%).

## Residual risks (cannot be resolved without live fills)
- Own-impact/adversarial adaptation to a persistent o60 bid (the big unknown).
- Maker-fee assumption (verify first fill = $0 fee).
- data-api tape completeness vs on-chain fills (spot-check during paper week).
- Regime death: bias could fade — kill switch pauses at trailing-100 EV < 0.
