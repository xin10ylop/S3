# EFC-M bot — paper trading first, live later

Implements exactly the validated strategy (see `results/FINAL_REPORT.md`):
at each 5-minute BTC Up/Down market, 60s after open, if one side's mid is in
[0.85, 0.97], rest a limit buy at that side's best bid; cancel at +90s if
unfilled; hold fills to settlement.

Paper mode uses the SAME decision code as live and simulates fills against the
live public trade tape with the same conservative queue model as the research
backtests (join behind the displayed queue; below-price prints always fill;
at-price prints only after the queue clears).

## Install on a server (as root)

```bash
scp -r bot root@YOUR_SERVER:/root/efc-install
ssh root@YOUR_SERVER 'cd /root/efc-install && bash install.sh'
```

Runs as systemd service `efc-bot`, paper mode, journal in `/opt/efc-bot/data/`.

## Watch it

```bash
journalctl -u efc-bot -f
/opt/efc-bot/venv/bin/python3 /opt/efc-bot/report.py /opt/efc-bot/data
```

The report compares live telemetry to the backtest bands (fill rate ~91%,
win rate 87-97%, EV +1 to +8 ¢/share, 4-18 signals/day) and prints PASS/WARN.

## Sizing (automatic)

Clip per trade = min(
  `market_cap_usd` (hard ceiling, default $100),
  `kelly_cap_frac` × bankroll (default 10% ≈ half-Kelly),
  `flow_frac` × trailing-median qualifying flow measured live (default 30%)
), floor $5 (exchange minimum is 5 shares ≈ $4.50).

The flow term is the self-adjusting part: the bot measures how many dollars
actually trade through its price level during its own fill windows and scales
to a fraction of that. Backtest EV/share by clip size (tape replay, before
own-impact effects): $5 → 5.1¢, $100 → 4.9¢, $400 → 4.7¢, $1500 → 4.3¢,
$3000 → 4.0¢. Treat anything beyond ~$400/clip as unproven until live
telemetry confirms.

## Go-live checklist (do NOT skip)

1. Run paper ≥ 7 days. `report.py` must show PASS on all four bands.
2. Verify fill rate and EV are within bands; if fill rate ≫ backtest, the
   paper fill model may be too generous vs reality — investigate first.
3. Fund a Polymarket wallet; export its private key.
4. `echo "POLY_PRIVATE_KEY=0x..." > /opt/efc-bot/env && chmod 600 /opt/efc-bot/env`
   (if using a proxy/funder wallet set POLY_FUNDER and POLY_SIG_TYPE too).
5. In `/opt/efc-bot/config.json`: `"mode": "live"`,
   `"live_armed": "I-UNDERSTAND-REAL-MONEY"`, and start with
   `"market_cap_usd": 5` for the first day. `systemctl restart efc-bot`.
6. After the FIRST live fill: check on Polymarket that the fee charged was $0
   (maker). If any fee was charged, stop and re-check the fee schedule.
7. Compare live fill rate & win rate to paper daily for a week before raising
   `market_cap_usd` stepwise: 5 → 20 → 50 → 100. Raise only while EV stays
   in-band. The kill switch pauses trading automatically if the trailing-100
   EV goes negative (delete the flag in data/state.json to resume).

## Ops notes

- The bot refuses to start if clock skew vs the exchange exceeds 1.5s
  (installer enables chrony).
- Live mode keeps a 4s heartbeat thread (exchange cancels resting orders
  without one after ~10-15s).
- Every decision is journaled (JSONL); nothing is silently dropped.
- Signals overlap windows very rarely; capital use stays ≈ 1 clip.
