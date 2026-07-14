"""Backtest: contested-zone oracle-model taker (H25).

In the last seconds of a window, when the market is CONTESTED (both quotes in
[lo, hi]), compute an oracle-aware probability of the final Chainlink print:
  d = (last chainlink tick + Binance move since that tick) - strike
  pm = Phi(d / sigma_remaining)
Buy the side where pm - cost - fee >= min_edge as taker. Hold to settlement.

The bet: the crowd prices the Binance view; the oracle print differs by the basis
and its own tick timing, which we model explicitly. Only runs on dates with
crypto_prices coverage (2026-04-02+); requires oracle strike.

Usage: python3 bt_contested_oracle.py 5m train --lead 5 --min_edge 0.03
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
    family, date, lead, latency_ms, band_lo, band_hi, min_edge, notional = args
    dur = DUR[family]
    wins = pd.read_parquet(ROOT / "master/windows_all.parquet")
    wins = wins[(wins.family == family) & (wins.date == date) & (wins.up_won >= 0)]
    if not len(wins):
        return []
    k = load_binance_sec(date)
    orc = load_oracle(date)
    if k is None:
        return []
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
        sig_s = close_s - lead
        s_now = spot(sig_s - 1)
        if not np.isfinite(s_now):
            continue
        if ots is not None and np.isfinite(w.open_oracle):
            i = np.searchsorted(ots, sig_s * 1_000_000, "right") - 1
            if i < 0 or sig_s * 1e6 - ots[i] > 10e6:
                continue
            ch_last = opx[i]
            ch_sec = ots[i] // 1_000_000
            d = (ch_last + (s_now - spot(ch_sec))) - w.open_oracle
            oracle_mode = 1
        else:
            s_open = spot(wts - 1)
            if not np.isfinite(s_open):
                continue
            d = s_now - s_open
            oracle_mode = 0
        i0, i1 = int(sig_s - 60) - kbase, int(sig_s) - kbase
        if i0 < 1:
            continue
        lr = np.diff(np.log(kv[i0 - 1:i1]))
        sig1s = np.sqrt(np.mean(lr * lr))
        sigma_rem = max(sig1s * np.sqrt(lead) * s_now, 1e-9)
        pm_up = norm.cdf(d / sigma_rem)

        q, t, b = day.window(wts)
        if q is None or not len(q):
            continue
        # contested filter on the CURRENT market quotes (as-of signal time)
        qts = q.timestamp_us.values
        qi = np.searchsorted(qts, sig_s * 1_000_000, "right") - 1
        if qi < 0:
            continue
        qrow = q.iloc[qi]
        if not (np.isfinite(qrow.bid_price) and np.isfinite(qrow.ask_price)):
            continue
        mid = (qrow.bid_price + qrow.ask_price) / 2
        if not (band_lo <= mid <= band_hi):
            continue
        exec_ts = int((sig_s + latency_ms / 1000.0) * 1_000_000)
        # evaluate both sides for model edge
        for side, pm in [("up", pm_up), ("dn", 1 - pm_up)]:
            px, ok, ets = taker_buy(b, q, exec_ts, side, notional)
            if not ok or not np.isfinite(px) or px <= 0 or px >= 1:
                continue
            fee = taker_fee(px, w.fee_rate)
            edge = pm - px - fee
            if edge < min_edge:
                continue
            win = (w.up_won == 1) if side == "up" else (w.up_won == 0)
            pnl = (1.0 - px - fee) if win else (-px - fee)
            out.append(dict(date=date, wts=wts, side=side, pm=float(pm), cost=float(px),
                            fee=float(fee), edge_pred=float(edge), win=bool(win),
                            pnl=float(pnl), mid=float(mid), d=float(d), sigma=float(sigma_rem),
                            oracle_mode=oracle_mode))
            break  # one trade per window
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("family")
    ap.add_argument("split", choices=["train", "val", "test"])
    ap.add_argument("--lead", type=float, default=5)
    ap.add_argument("--latency_ms", type=int, default=300)
    ap.add_argument("--band_lo", type=float, default=0.20)
    ap.add_argument("--band_hi", type=float, default=0.80)
    ap.add_argument("--min_edge", type=float, default=0.03)
    ap.add_argument("--notional", type=float, default=20)
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    dates = sorted(p.stem for p in (DAILY / a.family / "quotes").glob("*.parquet"))
    dates = [d for d in dates if split_of(a.family, d) == a.split]
    jobs = [(a.family, d, a.lead, a.latency_ms, a.band_lo, a.band_hi, a.min_edge, a.notional)
            for d in dates]
    rows = []
    with ProcessPoolExecutor(max_workers=3) as ex:
        for r in ex.map(run_day, jobs):
            rows.extend(r)
    df = pd.DataFrame(rows)
    outdir = pathlib.Path(__file__).resolve().parent.parent / "results"
    outdir.mkdir(exist_ok=True)
    tag = a.tag or f"L{a.lead}_lat{a.latency_ms}_e{a.min_edge}"
    df.to_parquet(outdir / f"trades_contested_{a.family}_{a.split}_{tag}.parquet", index=False)
    if len(df):
        print(f"{a.family} {a.split} {tag}: n={len(df)} win={df.win.mean():.4f} "
              f"EV={df.pnl.mean()*100:+.2f}c/sh cost={df.cost.mean():.3f} "
              f"pred_edge={df.edge_pred.mean()*100:.1f}c trades/day={len(df)/max(len(dates),1):.1f}")
    else:
        print(f"{tag}: no trades")


if __name__ == "__main__":
    main()
