"""Run the locked favorite strategy (taker + maker variants) on fresh Telonex days
(2026-07-08+), which no analysis in this project has ever touched. Quotes/trades only
(no bookcurves) — taker fills use fresh-BBO with size check.

Usage: python3 bt_favorite_telonex.py 2026-07-08 2026-07-14
"""
import sys, pathlib, warnings
import numpy as np
import pandas as pd
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import taker_buy, maker_fill, taker_fee, ROOT

warnings.filterwarnings("ignore")
TDIR = ROOT / "data/telonex/5m"


def run_day(date):
    wins = pd.read_parquet(ROOT / "master/windows_all.parquet")
    wins = wins[(wins.family == "5m") & (wins.date == date) & (wins.up_won >= 0)]
    qf = TDIR / "quotes" / f"{date}.parquet"
    tf = TDIR / "trades" / f"{date}.parquet"
    if not qf.exists():
        return []
    q_all = pd.read_parquet(qf).sort_values(["wts", "timestamp_us"]).reset_index(drop=True)
    for c in ["bid_price", "bid_size", "ask_price", "ask_size"]:
        q_all[c] = pd.to_numeric(q_all[c], errors="coerce").astype("float64")
    t_all = pd.read_parquet(tf).sort_values(["wts", "timestamp_us"]).reset_index(drop=True) if tf.exists() else None
    if t_all is not None:
        for c in ["price", "size"]:
            t_all[c] = pd.to_numeric(t_all[c], errors="coerce").astype("float64")
    out = []
    for w in wins.itertuples():
        wts = int(w.wts)
        lo, hi = np.searchsorted(q_all.wts.values, wts, "left"), np.searchsorted(q_all.wts.values, wts, "right")
        q = q_all.iloc[lo:hi]
        if len(q) < 2:
            continue
        t = None
        if t_all is not None:
            tl, th = np.searchsorted(t_all.wts.values, wts, "left"), np.searchsorted(t_all.wts.values, wts, "right")
            t = t_all.iloc[tl:th]
        sig_us = (wts + 60) * 1_000_000
        qts = q.timestamp_us.values
        i = np.searchsorted(qts, sig_us, "right") - 1
        if i < 0:
            continue
        row = q.iloc[i]
        if not (np.isfinite(row.bid_price) and np.isfinite(row.ask_price)):
            continue
        mid = (row.bid_price + row.ask_price) / 2
        if 0.85 <= mid <= 0.97:
            side = "up"
            bid_own = row.bid_price
        elif 0.85 <= 1 - mid <= 0.97:
            side = "dn"
            bid_own = 1 - row.ask_price
        else:
            continue
        win = (w.up_won == 1) if side == "up" else (w.up_won == 0)
        # taker entry (fresh BBO w/ size check)
        px, ok, _ = taker_buy(None, q, sig_us + 500_000, side, 20)
        if ok and np.isfinite(px) and px < 1:
            fee = taker_fee(px, w.fee_rate)
            pnl = (1 - px - fee) if win else (-px - fee)
            out.append(dict(date=date, wts=wts, style="taker", price=float(px), win=bool(win), pnl=float(pnl)))
        # maker entry
        if t is not None and len(t):
            shares = 20 / bid_own
            filled, fts = maker_fill(t, q, sig_us + 500_000, side, bid_own, shares, (wts + 90) * 1_000_000)
            if filled > 0:
                pnl = (1 - bid_own) if win else (-bid_own)
                out.append(dict(date=date, wts=wts, style="maker", price=float(bid_own), win=bool(win), pnl=float(pnl)))
    return out


def main():
    import datetime as dt
    start, end = sys.argv[1], sys.argv[2]
    dates = []
    d = dt.date.fromisoformat(start)
    while d <= dt.date.fromisoformat(end):
        dates.append(d.isoformat())
        d += dt.timedelta(days=1)
    rows = []
    with ProcessPoolExecutor(max_workers=3) as ex:
        for r in ex.map(run_day, dates):
            rows.extend(r)
    df = pd.DataFrame(rows)
    df.to_parquet(pathlib.Path(__file__).resolve().parent.parent / "results/trades_favorite_5m_JULY_HOLDOUT.parquet", index=False)
    for style in ["taker", "maker"]:
        s = df[df["style"] == style]
        if len(s):
            print(f"JULY HOLDOUT {style}: n={len(s)} win={s.win.mean():.4f} EV={s.pnl.mean()*100:+.2f}c/sh "
                  f"trades/day={len(s)/len(dates):.1f}")


if __name__ == "__main__":
    main()
