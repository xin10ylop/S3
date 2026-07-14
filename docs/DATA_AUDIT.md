# Data Audit — Polymarket BTC Up/Down Research

Date: 2026-07-14. All data local under `data/` (gitignored, ~14GB).

## Sources

### 1. Backblaze B2 vault (`polybtc-data-nico`) — 2,538 objects, 12.9GB, synced clean
| Family | Path | Days | Range | Contents |
|---|---|---|---|---|
| 5m | `daily/5m/{quotes,trades,bookcurves}` | 92 | 2026-02-12 → 2026-07-07 (**gap 05-13→07-05**) | full microstructure |
| 15m | `daily/15m/{quotes,trades,bookcurves}` | 216 | 2025-10-11 → 2026-07-07 (same gap) | full microstructure |
| 1h | `daily/1h/{quotes,trades}` | 275 | 2025-10-11 → 2026-07-12, **no gap** | has `slug` col, no bookcurves |
| 4h | `daily/4h/{quotes,trades,bookcurves}` | 206 | 2025-10-15 → 2026-07-07 (gap + 4 early days) | full microstructure |
| crypto_prices | `daily/crypto_prices` | 97 | 2026-04-02 → 2026-07-07, no gap | Chainlink BTC 1s grid |
| klines_1s | `binance/klines_1s` | 79→272 | 2025-10-10 → 2026-01-01 (vault) | Binance BTCUSDT 1s OHLCV |
| windows | `windows.parquet` | — | 2025-10-11 → **2026-05-12 (stale)** | 47,623 windows: ids, outcome, chainlink open/close, volume, fee_rate |

### 2. Fetched this session
- **Binance 1s klines 2026-01-01 → 2026-07-13** from data.binance.vision (`scripts/fetch_binance.py`) → continuous 272-day coverage.
- **Telonex markets metadata** for all BTC families → `data/data/processed/telonex_btc_markets.parquet` (94,233 markets: slug, market_id, asset ids, result_id, settled_at, start/end). Fills windows.parquet staleness + gives 1h windows.

### 3. Telonex API (key works)
- Base `https://api.telonex.io/v1`, auth `Authorization: Bearer`, 302 → presigned parquet.
- `GET /downloads/polymarket/{channel}/{date}?slug=…&outcome=Up` per market-day: trades/quotes/book_snapshot_25 all allowed on this key. `all_onchain_fills` is Pro-gated (403). `crypto_prices` channel allowed (asset_id=btcusd), 404 before 2026-04-02 (feed did not exist).
- `GET /datasets/polymarket/markets` full metadata (used above). Coverage through 2026-07-14.
- Families on Telonex: btc-updown-{5m,15m,4h} + hourly `bitcoin-up-or-down-*-et`. **No btc-updown-1h exists.**

## Semantics (verified empirically)
- Quotes/trades/books all reference the **Up token** (asset_id_0; outcome_0="Up" in 94,232/94,233 markets). Down side = mirror (buy Down @ p ≡ sell Up @ 1−p).
- `result_id`: "0" ⇒ Up won, "1" ⇒ Down won. Perfectly consistent with sign(close_chainlink − open_chainlink) (288/288 sampled) and with final quote mids (0.82 vs 0.16).
- `wts` = window start (unix s). Market resolves on Chainlink price at `wts+duration` vs at `wts`.
- **Resolution rule pinned**: official open/close = last Chainlink feed tick at-or-before the boundary timestamp (fixes 9/11 sign mismatches produced by next-tick reads; 2 of 38.7k remain unexplained ≈ label noise 0.005%).
- **No ties observed** in 16k windows with both prices. Up win rate 50.26% overall.
- Markets are quotable **hours before window open** (median first quote ≈12h before). Pre-open trading fully supported.
- File grouping: 5m/15m/4h daily files contain rows where obs-date == window-date == file date (midnight window loses pre-open history). 1h files are observation-date grouped (stitch ±1 day when slicing windows).
- Quotes continue ≈2min past close; settlement lag after close: 5m median 25s (p10 18s, p90 54s), 15m/4h ≈37s.
- Price grid: 0.1 cent increments observed.
- Chainlink feed: 1s cadence, server lag median 1.14s (p90 1.55s); vs Binance 1s close: corr 0.99995, mean basis −$18 (drifts, std $13).

## Fees (official docs, docs.polymarket.com/trading/fees)
- **Taker only**: `fee = shares × feeRate × p × (1−p)`, crypto feeRate = **0.07** currently. History (vault fee_rate): 0 → 0.0624 (5m launch / 15m+1h 2026-01-05 / 4h 2026-03-06) → 0.072 (2026-03-30) → 0.07 (2026-05-07).
- **Makers pay zero** and earn from a 20% maker-rebate pool. Matches user's live experience.
- At p=0.50 taker fee = 1.75¢/share; at 0.95 → 0.33¢. Winning-share redemption at $1.00: no fee.

## Schemas
- `quotes`: timestamp_us (exchange), local_timestamp_us, bid/ask price+size (Up token BBO), wts [, slug for 1h].
- `trades`: timestamp_us, local_timestamp_us, price, size, side (taker side, buy/sell of Up), wts.
- `bookcurves` (250ms grid): bid/ask L0, cost-to-fill avg price + shares for $50/$200/$1000/$5000 notional both directions, exhaust flags, bid/ask depth within 5c.
- `crypto_prices`: timestamp_us (1s grid), server_timestamp_us, local_timestamp_us, price.
- `klines_1s`: standard Binance kline with taker_buy split, times in μs.
- `windows_all.parquet` (built): slug, wts, market_id, asset ids, result_id → up_won, settled_at_us, duration, family, date, fee_rate, open/close_oracle (asof-backward, 60s staleness guard).

## Known limitations
- 5m/15m/4h missing 2026-05-13 → 2026-07-05 (collector outage). 1h + crypto_prices unaffected. Telonex per-market pulls can fill targeted subsets (≈288 req/day for 5m).
- crypto_prices starts 2026-04-02 (feed birth). Earlier strikes: Binance approximation only (basis std $13 ≈ 1.7bp).
- Container restarts wipe non-repo state: keep everything reproducible via scripts; push commits immediately.
