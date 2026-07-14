"""Backtest: pre-open maker scalp (the user's manual strategy, generalized).

Per window:
  1. At open - place_lead seconds, choose side by `sider` and rest a maker buy at
     `entry` (side's own price terms). Cancel if unfilled by open + buy_abort.
  2. On fill, immediately rest a maker sell at `target`.
  3. If sell unfilled by open + exit_abort: exit taker (fee) at book, or hold to
     settlement if no book. Settlement pays 1/0, no fee.

Maker fills replayed against the trade tape with queue-join-back. No maker fees.

Usage: python3 bt_preopen_scalp.py 5m train --sider mid --entry 0.51 --target 0.55
"""
import sys, pathlib, argparse, warnings
import numpy as np
import pandas as pd
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import DayData, taker_buy, maker_fill, taker_fee, ROOT, DAILY
from splits import split_of

warnings.filterwarnings("ignore")
DUR = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}


def pick_side(sider, q_slice, t_slice, place_ts, prev_outcome, ml_p=None, ml_margin=0.0):
    """Return 'up', 'dn', 'both', or None using only info available at place_ts."""
    if sider == "ml":
        if ml_p is None or not np.isfinite(ml_p):
            return None
        qts = q_slice.timestamp_us.values
        i = np.searchsorted(qts, place_ts, "right") - 1
        if i < 0:
            return None
        row = q_slice.iloc[i]
        if not (np.isfinite(row.bid_price) and np.isfinite(row.ask_price)):
            return None
        if ml_p >= 0.5:
            entry_ref = row.bid_price
            return "up" if ml_p - entry_ref >= ml_margin else None
        entry_ref = 1.0 - row.ask_price
        return "dn" if (1 - ml_p) - entry_ref >= ml_margin else None
    if sider == "both":
        return "both"
    if sider in ("mid", "anti_mid"):
        qts = q_slice.timestamp_us.values
        i = np.searchsorted(qts, place_ts, "right") - 1
        if i < 0:
            return None
        row = q_slice.iloc[i]
        if not (np.isfinite(row.bid_price) and np.isfinite(row.ask_price)):
            return None
        mid = (row.bid_price + row.ask_price) / 2
        if abs(mid - 0.5) < 1e-9:
            return None
        fav = "up" if mid > 0.5 else "dn"
        return fav if sider == "mid" else ("dn" if fav == "up" else "up")
    if sider == "flow":
        if t_slice is None or not len(t_slice):
            return None
        tts = t_slice.timestamp_us.values
        i1 = np.searchsorted(tts, place_ts, "right")
        i0 = np.searchsorted(tts, place_ts - 120_000_000, "right")
        if i1 <= i0:
            return None
        seg = t_slice.iloc[i0:i1]
        sgn = np.where(seg["side"].values == "buy", 1.0, -1.0)
        f = (sgn * seg["size"].values).sum()
        if f == 0:
            return None
        return "up" if f > 0 else "dn"
    if sider == "prev_rev":
        if prev_outcome is None:
            return None
        return "dn" if prev_outcome == 1 else "up"
    raise ValueError(sider)


def run_day(args):
    (family, date, sider, entry, target, place_lead, buy_abort, exit_abort,
     notional, hold_if_no_exit, ml_margin) = args
    dur = DUR[family]
    wins = pd.read_parquet(ROOT / "master/windows_all.parquet")
    wins = wins[(wins.family == family) & (wins.date == date) & (wins.up_won >= 0)]
    wins = wins.sort_values("wts")
    if not len(wins):
        return []
    ml_map = {}
    if sider == "ml":
        mlf = ROOT / f"master/ml_side_{family}.parquet"
        ml = pd.read_parquet(mlf)
        ml_map = dict(zip(ml.wts.values, ml.p_hat.values))
    day = DayData(family, date)
    prev_map = dict(zip(wins.wts.values[1:], wins.up_won.values[:-1]))
    out = []
    for w in wins.itertuples():
        wts = int(w.wts)
        open_us = wts * 1_000_000
        place_ts = open_us - int(place_lead * 1e6)
        q, t, b = day.window(wts)
        if q is None or not len(q):
            continue
        prev = prev_map.get(w.wts)
        side = pick_side(sider, q, t, place_ts, prev, ml_map.get(wts), ml_margin)
        if side is None:
            continue
        sides = ["up", "dn"] if side == "both" else [side]
        for s in sides:
            entry_px = entry
            if sider == "ml":  # join the favored side's current bid
                qts = q.timestamp_us.values
                i = np.searchsorted(qts, place_ts, "right") - 1
                row = q.iloc[i]
                entry_px = row.bid_price if s == "up" else 1.0 - row.ask_price
                if not np.isfinite(entry_px) or entry_px <= 0.02:
                    continue
            shares = notional / entry_px
            filled, fts = maker_fill(t, q, place_ts, s, entry_px, shares,
                                     open_us + int(buy_abort * 1e6))
            if filled < shares - 1e-9:
                continue
            win = (w.up_won == 1) if s == "up" else (w.up_won == 0)
            if target is None or target <= 0:
                pnl = (1.0 - entry_px) if win else (0.0 - entry_px)
                res = "held_settle"
                out.append(dict(date=date, wts=wts, side=s, entry=entry_px, res=res,
                                win=bool(win), pnl=float(pnl), fill_ts_rel=(fts - open_us) / 1e6))
                continue
            sf, sts = maker_fill(t, q, fts, "dn" if s == "up" else "up",
                                 1.0 - target, shares, open_us + int(exit_abort * 1e6))
            if sf >= shares - 1e-9:
                pnl = (target - entry_px)
                res = "target"
            else:
                ex_ts = open_us + int(exit_abort * 1e6)
                px, ok, _ = taker_buy(b, q, ex_ts, "dn" if s == "up" else "up", notional)
                if ok and np.isfinite(px):
                    sell_px = 1.0 - px
                    fee = taker_fee(sell_px, w.fee_rate)
                    pnl = sell_px - entry_px - fee
                    res = "abort_taker"
                elif hold_if_no_exit:
                    pnl = (1.0 - entry_px) if win else (0.0 - entry_px)
                    res = "held_settle"
                else:
                    continue
            out.append(dict(date=date, wts=wts, side=s, entry=entry_px, res=res,
                            win=bool(win), pnl=float(pnl), fill_ts_rel=(fts - open_us) / 1e6))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("family")
    ap.add_argument("split", choices=["train", "val", "test"])
    ap.add_argument("--sider", default="mid",
                    choices=["mid", "anti_mid", "flow", "prev_rev", "both", "ml"])
    ap.add_argument("--ml_margin", type=float, default=0.02)
    ap.add_argument("--entry", type=float, default=0.51)
    ap.add_argument("--target", type=float, default=0.55)  # <=0 means hold to settlement
    ap.add_argument("--place_lead", type=float, default=10)
    ap.add_argument("--buy_abort", type=float, default=0)
    ap.add_argument("--exit_abort", type=float, default=60)
    ap.add_argument("--notional", type=float, default=10)
    ap.add_argument("--hold", action="store_true")
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    dates = sorted(p.stem for p in (DAILY / a.family / "quotes").glob("*.parquet"))
    dates = [d for d in dates if split_of(a.family, d) == a.split]
    jobs = [(a.family, d, a.sider, a.entry, a.target, a.place_lead, a.buy_abort,
             a.exit_abort, a.notional, a.hold, a.ml_margin) for d in dates]
    rows = []
    with ProcessPoolExecutor(max_workers=3) as ex:
        for r in ex.map(run_day, jobs):
            rows.extend(r)
    df = pd.DataFrame(rows)
    outdir = pathlib.Path(__file__).resolve().parent.parent / "results"
    outdir.mkdir(exist_ok=True)
    tag = a.tag or f"{a.sider}_e{a.entry}_t{a.target}_pl{a.place_lead}_ba{a.buy_abort}_ea{a.exit_abort}{'_hold' if a.hold else ''}"
    df.to_parquet(outdir / f"trades_preopen_{a.family}_{a.split}_{tag}.parquet", index=False)
    if len(df):
        byres = df.res.value_counts().to_dict()
        print(f"{a.family} {a.split} {tag}: n={len(df)} fillrate/day={len(df)/max(len(dates),1):.1f} "
              f"EV={df.pnl.mean()*100:+.2f}c win={df.win.mean():.3f} {byres}")
    else:
        print(f"{tag}: no trades")


if __name__ == "__main__":
    main()
