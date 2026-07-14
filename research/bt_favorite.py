"""Backtest: buy the favorite (pure price signal, no model).

At a fixed time in the window's life (rel to open or close), if the book mid is
inside [band_lo, band_hi] (or mirrored for the Down side), taker-buy the favorite
and hold to settlement. No Binance/oracle input at all — tests whether quoted
favorites are systematically underpriced (short-horizon underreaction).

Usage: python3 bt_favorite.py 5m train --at o60 --band 0.80 0.97
"""
import sys, pathlib, argparse, warnings
import numpy as np
import pandas as pd
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import DayData, taker_buy, taker_fee, ROOT, DAILY
from splits import split_of

warnings.filterwarnings("ignore")
DUR = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}


def run_day(args):
    family, date, at, band_lo, band_hi, latency_ms, notional = args
    dur = DUR[family]
    wins = pd.read_parquet(ROOT / "master/windows_all.parquet")
    wins = wins[(wins.family == family) & (wins.date == date) & (wins.up_won >= 0)]
    if not len(wins):
        return []
    day = DayData(family, date)
    rel = float(at[1:])
    out = []
    for w in wins.itertuples():
        wts = int(w.wts)
        base = wts if at[0] == "o" else wts + dur
        sig_us = int((base + rel) * 1_000_000)
        q, t, b = day.window(wts)
        if q is None or not len(q):
            continue
        qts = q.timestamp_us.values
        i = np.searchsorted(qts, sig_us, "right") - 1
        if i < 0:
            continue
        row = q.iloc[i]
        if not (np.isfinite(row.bid_price) and np.isfinite(row.ask_price)):
            continue
        mid = (row.bid_price + row.ask_price) / 2
        if band_lo <= mid <= band_hi:
            side = "up"
        elif band_lo <= 1 - mid <= band_hi:
            side = "dn"
        else:
            continue
        exec_ts = sig_us + latency_ms * 1000
        px, ok, ets = taker_buy(b, q, exec_ts, side, notional)
        if not ok or not np.isfinite(px) or px <= 0 or px >= 1:
            continue
        fee = taker_fee(px, w.fee_rate)
        win = (w.up_won == 1) if side == "up" else (w.up_won == 0)
        pnl = (1.0 - px - fee) if win else (-px - fee)
        out.append(dict(date=date, wts=wts, side=side, mid=float(mid), cost=float(px),
                        fee=float(fee), win=bool(win), pnl=float(pnl)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("family")
    ap.add_argument("split", choices=["train", "val", "test"])
    ap.add_argument("--at", default="o60")  # o60 = open+60s, c-120 = close-120s
    ap.add_argument("--band", type=float, nargs=2, default=[0.80, 0.97])
    ap.add_argument("--latency_ms", type=int, default=500)
    ap.add_argument("--notional", type=float, default=20)
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    dates = sorted(p.stem for p in (DAILY / a.family / "quotes").glob("*.parquet"))
    dates = [d for d in dates if split_of(a.family, d) == a.split]
    jobs = [(a.family, d, a.at, a.band[0], a.band[1], a.latency_ms, a.notional) for d in dates]
    rows = []
    with ProcessPoolExecutor(max_workers=3) as ex:
        for r in ex.map(run_day, jobs):
            rows.extend(r)
    df = pd.DataFrame(rows)
    outdir = pathlib.Path(__file__).resolve().parent.parent / "results"
    outdir.mkdir(exist_ok=True)
    tag = a.tag or f"{a.at}_{a.band[0]}-{a.band[1]}_lat{a.latency_ms}"
    df.to_parquet(outdir / f"trades_favorite_{a.family}_{a.split}_{tag}.parquet", index=False)
    if len(df):
        by_m = df.assign(ym=df.date.str[:7]).groupby('ym').pnl.agg(['count', lambda x: 100*x.mean()])
        by_m.columns = ['n', 'EV_c']
        print(f"{a.family} {a.split} {tag}: n={len(df)} win={df.win.mean():.4f} "
              f"EV={df.pnl.mean()*100:+.2f}c cost={df.cost.mean():.3f} "
              f"trades/day={len(df)/max(len(dates),1):.1f}")
        print(by_m.round(2).to_string())
    else:
        print(f"{tag}: no trades")


if __name__ == "__main__":
    main()
