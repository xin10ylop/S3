"""Backtest: late-window maker bid on the model-certain side (H18b).

At close - lead seconds, if the model says a side is near-certain (pm >= cutoff),
rest a maker BUY of that side at the current best bid (optionally improved by
`improve`), capped at price_cap. Fill from the trade tape (queue join-back), no fee.
Filled shares are held to settlement (winner pays 1.0).

The fill source is impatient winner-holders cashing out before settlement — flow
that is NOT informed against us (the outcome is already ~determined).

Usage: python3 bt_late_maker.py 1h train --lead 60 --cutoff 0.97 --price_cap 0.97
"""
import sys, pathlib, argparse, warnings
import numpy as np
import pandas as pd
from concurrent.futures import ProcessPoolExecutor
from scipy.stats import norm

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import DayData, maker_fill, ROOT, DAILY
from bt_close_sniper import load_binance_sec, load_oracle
from splits import split_of

warnings.filterwarnings("ignore")
DUR = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}


def run_day(args):
    family, date, lead, cutoff, price_cap, improve, notional = args
    dur = DUR[family]
    wins = pd.read_parquet(ROOT / "master/windows_all.parquet")
    wins = wins[(wins.family == family) & (wins.date == date) & (wins.up_won >= 0)]
    if not len(wins):
        return []
    k = load_binance_sec(date)
    if k is None:
        return []
    orc = load_oracle(date)
    day = DayData(family, date)
    kv = k.values
    kbase = k.index[0]

    def spot(sec):
        i = int(sec) - kbase
        return kv[i] if 0 <= i < len(kv) else np.nan

    out = []
    for w in wins.itertuples():
        wts = int(w.wts)
        close_s = wts + dur
        sig_s = close_s - lead
        s_now, s_open = spot(sig_s - 1), spot(wts - 1)
        if not np.isfinite(s_now) or not np.isfinite(s_open):
            continue
        if orc is not None and np.isfinite(w.open_oracle):
            ots, opx = orc
            i = np.searchsorted(ots, sig_s * 1_000_000, "right") - 1
            if i >= 0 and sig_s * 1e6 - ots[i] < 30e6:
                d = (opx[i] + (s_now - spot(ots[i] // 1_000_000))) - w.open_oracle
            else:
                d = s_now - s_open
        else:
            d = s_now - s_open
        i0, i1 = int(sig_s - 60) - kbase, int(sig_s) - kbase
        if i0 < 1:
            continue
        lr = np.diff(np.log(kv[i0 - 1:i1]))
        sig1s = np.sqrt(np.mean(lr * lr))
        sigma_rem = max(sig1s * np.sqrt(lead) * s_now, 1e-9)
        pm_up = norm.cdf(d / sigma_rem)
        if pm_up >= cutoff:
            side = "up"
        elif pm_up <= 1 - cutoff:
            side = "dn"
        else:
            continue
        q, t, b = day.window(wts)
        if q is None or not len(q):
            continue
        place_ts = int((sig_s + 0.3) * 1_000_000)  # 300ms to observe + place
        qts = q.timestamp_us.values
        i = np.searchsorted(qts, place_ts, "right") - 1
        if i < 0:
            continue
        row = q.iloc[i]
        if side == "up":
            if not np.isfinite(row.bid_price):
                continue
            price = min(row.bid_price + improve, price_cap)
        else:
            if not np.isfinite(row.ask_price):
                continue
            price = min((1.0 - row.ask_price) + improve, price_cap)
        if price <= 0:
            continue
        shares = notional / price
        filled, fts = maker_fill(t, q, place_ts, side, price, shares, close_s * 1_000_000)
        if filled <= 0:
            continue
        win = (w.up_won == 1) if side == "up" else (w.up_won == 0)
        pnl = (1.0 - price) if win else (-price)
        out.append(dict(date=date, wts=wts, side=side, pm=float(pm_up), price=float(price),
                        filled=float(filled), full=bool(filled >= shares - 1e-9),
                        win=bool(win), pnl=float(pnl),
                        fill_lag_s=(fts - place_ts) / 1e6 if fts else np.nan))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("family")
    ap.add_argument("split", choices=["train", "val", "test"])
    ap.add_argument("--lead", type=float, default=60)
    ap.add_argument("--cutoff", type=float, default=0.97)
    ap.add_argument("--price_cap", type=float, default=0.97)
    ap.add_argument("--improve", type=float, default=0.0)
    ap.add_argument("--notional", type=float, default=20)
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    dates = sorted(p.stem for p in (DAILY / a.family / "quotes").glob("*.parquet"))
    dates = [d for d in dates if split_of(a.family, d) == a.split]
    jobs = [(a.family, d, a.lead, a.cutoff, a.price_cap, a.improve, a.notional) for d in dates]
    rows = []
    with ProcessPoolExecutor(max_workers=3) as ex:
        for r in ex.map(run_day, jobs):
            rows.extend(r)
    df = pd.DataFrame(rows)
    outdir = pathlib.Path(__file__).resolve().parent.parent / "results"
    outdir.mkdir(exist_ok=True)
    tag = a.tag or f"L{a.lead}_cut{a.cutoff}_cap{a.price_cap}_imp{a.improve}"
    df.to_parquet(outdir / f"trades_late_maker_{a.family}_{a.split}_{tag}.parquet", index=False)
    if len(df):
        # weight PnL by filled shares (partial fills count)
        wpnl = (df.pnl * df.filled).sum() / df.filled.sum()
        print(f"{a.family} {a.split} {tag}: fills={len(df)} ({df.full.mean()*100:.0f}% full) "
              f"win={df.win.mean():.4f} EV={wpnl*100:+.2f}c/sh price={df.price.mean():.3f} "
              f"fills/day={len(df)/max(len(dates),1):.1f}")
    else:
        print(f"{tag}: no fills")


if __name__ == "__main__":
    main()
