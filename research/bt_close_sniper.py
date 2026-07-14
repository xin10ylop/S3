"""Backtest: end-of-window stale-quote sniper.

Signal at close - lead seconds: distance of estimated final oracle print vs strike,
in units of remaining vol. If near-certain (pm >= cutoff) and the taker cost at
(signal + latency) is below price_cap, take `notional` USDC of the certain side.
Hold to settlement. Taker fee applied. One trade max per window.

Usage: python3 bt_close_sniper.py 5m train [--lead 2] [--latency_ms 300]
Writes per-trade rows to results/trades_close_sniper_{family}_{split}_{tag}.parquet
"""
import sys, pathlib, argparse, warnings
import numpy as np
import pandas as pd
from concurrent.futures import ProcessPoolExecutor
from scipy.stats import norm

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import DayData, taker_buy, taker_fee, ROOT, DAILY
from splits import split_of

warnings.filterwarnings("ignore")
DUR = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}


def load_binance_sec(date):
    d0 = pd.Timestamp(date)
    frames = []
    for d in [d0 - pd.Timedelta(days=1), d0]:
        f = ROOT / "data/processed/binance/klines_1s" / (d.strftime("%Y-%m-%d") + ".parquet")
        if f.exists():
            frames.append(pd.read_parquet(f, columns=["open_time", "close"]))
    if not frames:
        return None
    k = pd.concat(frames)
    k["sec"] = (k.open_time // 1_000_000).astype("int64")
    k = k.drop_duplicates("sec").set_index("sec")["close"]
    full = np.arange(k.index.min(), k.index.max() + 1)
    return k.reindex(full).ffill()


def load_oracle(date):
    f = DAILY / "crypto_prices" / f"{date}.parquet"
    if not f.exists():
        return None
    cp = pd.read_parquet(f, columns=["timestamp_us", "price"]).sort_values("timestamp_us")
    return cp.timestamp_us.values, cp.price.values


def run_day(args):
    family, date, lead, latency_ms, cutoff, price_cap, notional = args
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
        s_now = spot(sig_s - 1)
        s_open = spot(wts - 1)
        if not np.isfinite(s_now) or not np.isfinite(s_open):
            continue
        if orc is not None and np.isfinite(w.open_oracle):
            ots, opx = orc
            i = np.searchsorted(ots, sig_s * 1_000_000, "right") - 1
            if i >= 0 and sig_s * 1e6 - ots[i] < 30e6:
                ch_last = opx[i]
                ch_sec = ots[i] // 1_000_000
                d = (ch_last + (s_now - spot(ch_sec))) - w.open_oracle
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
        exec_ts = int((sig_s + latency_ms / 1000.0) * 1_000_000)
        px, ok, ets = taker_buy(b, q, exec_ts, side, notional)
        if not ok or not np.isfinite(px) or px > price_cap or px <= 0:
            continue
        fee = taker_fee(px, w.fee_rate)
        win = (w.up_won == 1) if side == "up" else (w.up_won == 0)
        pnl = (1.0 - px - fee) if win else (-px - fee)
        out.append(dict(date=date, wts=wts, side=side, pm=float(pm_up), cost=float(px),
                        fee=float(fee), win=bool(win), pnl=float(pnl),
                        shares=notional / px, exec_lag_us=int(ets - sig_s * 1e6)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("family")
    ap.add_argument("split", choices=["train", "val", "test"])
    ap.add_argument("--lead", type=float, default=2)
    ap.add_argument("--latency_ms", type=int, default=300)
    ap.add_argument("--cutoff", type=float, default=0.97)
    ap.add_argument("--price_cap", type=float, default=0.95)
    ap.add_argument("--notional", type=float, default=200)
    ap.add_argument("--tag", default="")
    a = ap.parse_args()

    dates = sorted(p.stem for p in (DAILY / a.family / "quotes").glob("*.parquet"))
    dates = [d for d in dates if split_of(a.family, d) == a.split]
    jobs = [(a.family, d, a.lead, a.latency_ms, a.cutoff, a.price_cap, a.notional) for d in dates]
    rows = []
    with ProcessPoolExecutor(max_workers=3) as ex:
        for r in ex.map(run_day, jobs):
            rows.extend(r)
    df = pd.DataFrame(rows)
    outdir = pathlib.Path(__file__).resolve().parent.parent / "results"
    outdir.mkdir(exist_ok=True)
    tag = a.tag or f"lead{a.lead}_lat{a.latency_ms}_cut{a.cutoff}_cap{a.price_cap}"
    f = outdir / f"trades_close_sniper_{a.family}_{a.split}_{tag}.parquet"
    df.to_parquet(f, index=False)
    if len(df):
        print(f"{a.family} {a.split} {tag}: n={len(df)} win={df.win.mean():.4f} "
              f"EV={df.pnl.mean()*100:+.2f}c/sh median_cost={df.cost.median():.3f} "
              f"trades/day={len(df)/max(len(dates),1):.1f} "
              f"pnl_per_trade=${(df.pnl*df.shares).mean():+.2f}")
    else:
        print("no trades")


if __name__ == "__main__":
    main()
