"""Backtest: model-market divergence buyer (generalized underreaction).

Scan the window on a grid; when the Binance/oracle model says P(side) >= pm_min
while the market mid still prices it <= pm - gap, taker-buy the side and hold to
settlement. At most one trade per window (first trigger).

Usage: python3 bt_divergence.py 5m train --pm_min 0.90 --gap 0.08
"""
import sys, pathlib, argparse, warnings
import numpy as np
import pandas as pd
from concurrent.futures import ProcessPoolExecutor
from scipy.stats import norm

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import DayData, taker_buy, taker_fee, ROOT, DAILY
from bt_close_sniper import load_binance_sec, load_oracle
from splits import split_of

warnings.filterwarnings("ignore")
DUR = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}


def run_day(args):
    family, date, pm_min, gap, guard, tail_guard, latency_ms, notional, grid_s = args
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
    ots, opx = orc if orc is not None else (None, None)

    def spot(sec):
        i = int(sec) - kbase
        return kv[i] if 0 <= i < len(kv) else np.nan

    out = []
    for w in wins.itertuples():
        wts = int(w.wts)
        close_s = wts + dur
        q, t, b = day.window(wts)
        if q is None or not len(q):
            continue
        qts = q.timestamp_us.values
        s_open = spot(wts - 1)
        use_oracle = ots is not None and np.isfinite(w.open_oracle)
        if not use_oracle and not np.isfinite(s_open):
            continue
        for T in range(wts + guard, close_s - tail_guard, grid_s):
            s_now = spot(T - 1)
            if not np.isfinite(s_now):
                continue
            if use_oracle:
                i = np.searchsorted(ots, T * 1_000_000, "right") - 1
                if i < 0 or T * 1e6 - ots[i] > 10e6:
                    continue
                d = (opx[i] + (s_now - spot(ots[i] // 1_000_000))) - w.open_oracle
            else:
                d = s_now - s_open
            rem = close_s - T
            i0, i1 = int(T - 60) - kbase, int(T) - kbase
            if i0 < 1:
                continue
            lr = np.diff(np.log(kv[i0 - 1:i1]))
            sig1s = np.sqrt(np.mean(lr * lr))
            sigma_rem = max(sig1s * np.sqrt(rem) * s_now, 1e-9)
            pm_up = norm.cdf(d / sigma_rem)
            side, pm = ("up", pm_up) if pm_up >= 0.5 else ("dn", 1 - pm_up)
            if pm < pm_min:
                continue
            qi = np.searchsorted(qts, T * 1_000_000, "right") - 1
            if qi < 0:
                continue
            row = q.iloc[qi]
            if not (np.isfinite(row.bid_price) and np.isfinite(row.ask_price)):
                continue
            mid = (row.bid_price + row.ask_price) / 2
            mid_side = mid if side == "up" else 1 - mid
            if mid_side > pm - gap:
                continue
            exec_ts = int(T * 1_000_000 + latency_ms * 1000)
            px, ok, ets = taker_buy(b, q, exec_ts, side, notional)
            if not ok or not np.isfinite(px) or px <= 0 or px >= 1 or px > pm - gap / 2:
                continue
            fee = taker_fee(px, w.fee_rate)
            win = (w.up_won == 1) if side == "up" else (w.up_won == 0)
            pnl = (1.0 - px - fee) if win else (-px - fee)
            out.append(dict(date=date, wts=wts, side=side, t_rel=T - wts, pm=float(pm),
                            mid=float(mid_side), cost=float(px), fee=float(fee),
                            win=bool(win), pnl=float(pnl)))
            break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("family")
    ap.add_argument("split", choices=["train", "val", "test"])
    ap.add_argument("--pm_min", type=float, default=0.90)
    ap.add_argument("--gap", type=float, default=0.08)
    ap.add_argument("--guard", type=int, default=30)
    ap.add_argument("--tail_guard", type=int, default=90)
    ap.add_argument("--latency_ms", type=int, default=500)
    ap.add_argument("--notional", type=float, default=20)
    ap.add_argument("--grid_s", type=int, default=5)
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    dates = sorted(p.stem for p in (DAILY / a.family / "quotes").glob("*.parquet"))
    dates = [d for d in dates if split_of(a.family, d) == a.split]
    jobs = [(a.family, d, a.pm_min, a.gap, a.guard, a.tail_guard, a.latency_ms,
             a.notional, a.grid_s) for d in dates]
    rows = []
    with ProcessPoolExecutor(max_workers=3) as ex:
        for r in ex.map(run_day, jobs):
            rows.extend(r)
    df = pd.DataFrame(rows)
    outdir = pathlib.Path(__file__).resolve().parent.parent / "results"
    outdir.mkdir(exist_ok=True)
    tag = a.tag or f"pm{a.pm_min}_g{a.gap}"
    df.to_parquet(outdir / f"trades_diverg_{a.family}_{a.split}_{tag}.parquet", index=False)
    if len(df):
        by_m = df.assign(ym=df.date.str[:7]).groupby('ym').pnl.agg(['count', lambda x: 100 * x.mean()])
        by_m.columns = ['n', 'EV_c']
        print(f"{a.family} {a.split} {tag}: n={len(df)} win={df.win.mean():.4f} "
              f"EV={df.pnl.mean()*100:+.2f}c cost={df.cost.mean():.3f} t_rel_med={df.t_rel.median():.0f}s "
              f"trades/day={len(df)/max(len(dates),1):.1f}")
        print(by_m.round(2).to_string())
    else:
        print(f"{tag}: no trades")


if __name__ == "__main__":
    main()
