"""Event study: what happens after sharp Up-token mid moves inside the window?

Event: |mid(t) - mid(t - move_win)| >= move_c cents, with t in
[open + guard, close - tail_guard]. Measures mid at t + {10,30,60}s and the
outcome, conditioned on move direction. Pure phenomenology, no execution.

Usage: python3 ev_sharp_moves.py 5m train --move_c 0.06 --move_win 5
"""
import sys, pathlib, argparse, warnings
import numpy as np
import pandas as pd
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import DayData, ROOT, DAILY
from splits import split_of

warnings.filterwarnings("ignore")
DUR = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}


def run_day(args):
    family, date, move_c, move_win, guard, tail_guard = args
    dur = DUR[family]
    wins = pd.read_parquet(ROOT / "master/windows_all.parquet")
    wins = wins[(wins.family == family) & (wins.date == date) & (wins.up_won >= 0)]
    if not len(wins):
        return []
    day = DayData(family, date)
    out = []
    for w in wins.itertuples():
        wts = int(w.wts)
        q, t, b = day.window(wts)
        if q is None or len(q) < 10:
            continue
        qq = q[np.isfinite(q.bid_price.values) & np.isfinite(q.ask_price.values)]
        if len(qq) < 10:
            continue
        ts = qq.timestamp_us.values
        mid = (qq.bid_price.values + qq.ask_price.values) / 2
        lo_us = (wts + guard) * 1_000_000
        hi_us = (wts + dur - tail_guard) * 1_000_000
        # scan on a 1s grid
        grid = np.arange(lo_us, hi_us, 1_000_000)
        idx_now = np.searchsorted(ts, grid, "right") - 1
        idx_prev = np.searchsorted(ts, grid - move_win * 1_000_000, "right") - 1
        ok = (idx_now >= 0) & (idx_prev >= 0)
        dmid = np.where(ok, mid[np.maximum(idx_now, 0)] - mid[np.maximum(idx_prev, 0)], 0)
        events = np.where(np.abs(dmid) >= move_c)[0]
        last_evt_us = -1e18
        for gi in events:
            t_us = grid[gi]
            if t_us - last_evt_us < 30_000_000:  # de-overlap
                continue
            last_evt_us = t_us
            m0 = mid[idx_now[gi]]
            rec = dict(date=date, wts=wts, t_rel=(t_us - wts * 1e6) / 1e6,
                       dmid=float(dmid[gi]), mid0=float(m0), up_won=int(w.up_won))
            good = True
            for h in (10, 30, 60):
                j = np.searchsorted(ts, t_us + h * 1_000_000, "right") - 1
                rec[f"mid_{h}"] = float(mid[j]) if j >= 0 else np.nan
            out.append(rec)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("family")
    ap.add_argument("split", choices=["train", "val", "test"])
    ap.add_argument("--move_c", type=float, default=0.06)
    ap.add_argument("--move_win", type=float, default=5)
    ap.add_argument("--guard", type=float, default=10)
    ap.add_argument("--tail_guard", type=float, default=60)
    a = ap.parse_args()
    dates = sorted(p.stem for p in (DAILY / a.family / "quotes").glob("*.parquet"))
    dates = [d for d in dates if split_of(a.family, d) == a.split]
    jobs = [(a.family, d, a.move_c, a.move_win, a.guard, a.tail_guard) for d in dates]
    rows = []
    with ProcessPoolExecutor(max_workers=3) as ex:
        for r in ex.map(run_day, jobs):
            rows.extend(r)
    df = pd.DataFrame(rows)
    outdir = pathlib.Path(__file__).resolve().parent.parent / "results"
    outdir.mkdir(exist_ok=True)
    df.to_parquet(outdir / f"events_sharp_{a.family}_{a.split}_{a.move_c}_{a.move_win}.parquet", index=False)
    if not len(df):
        print("no events")
        return
    sgn = np.sign(df.dmid)
    print(f"{a.family} {a.split} move>={a.move_c*100:.0f}c/{a.move_win}s: n={len(df)} "
          f"({len(df)/max(len(dates),1):.1f}/day)")
    for h in (10, 30, 60):
        cont = (df[f"mid_{h}"] - df.mid0) * sgn
        print(f"  t+{h}s: mean continuation {cont.mean()*100:+.2f}c "
              f"(median {cont.median()*100:+.2f}c, P(revert)={ (cont<0).mean():.3f}, n={cont.notna().sum()})")
    fin = (df.up_won - df.mid0) * sgn
    print(f"  settle: mean continuation {fin.mean()*100:+.2f}c (outcome vs mid0, signed)")


if __name__ == "__main__":
    main()
