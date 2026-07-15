"""EV vs clip size for the EFC-M strategy, with partial fills.

For each signal window, replay a maker bid of `clip` USDC at the favorite's best bid
(join-back queue). Partial fills count pro-rata. Reports, per clip size:
  - fill rate (any / full)
  - EV cents/share on filled shares
  - expected profit $/window-signal and $/day
Run on train+test (excluding val) to maximize sample while keeping val untouched-ish
(val already consumed by gates; this is post-validation capacity analysis).
"""
import sys, pathlib
import numpy as np, pandas as pd, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import DayData, ROOT, DAILY
from splits import split_of
from concurrent.futures import ProcessPoolExecutor

CLIPS = [5, 10, 20, 50, 100, 200, 400, 800, 1500, 3000]


def run_day(date):
    wins = pd.read_parquet(ROOT / 'master/windows_all.parquet')
    wins = wins[(wins.family == '5m') & (wins.date == date) & (wins.up_won >= 0)]
    day = DayData('5m', date)
    out = []
    for w in wins.itertuples():
        wts = int(w.wts)
        sig_us = (wts + 60) * 1_000_000
        q, t, b = day.window(wts)
        if q is None or not len(q) or t is None or not len(t):
            continue
        qts = q.timestamp_us.values
        i = np.searchsorted(qts, sig_us, 'right') - 1
        if i < 0:
            continue
        row = q.iloc[i]
        if not (np.isfinite(row.bid_price) and np.isfinite(row.ask_price)):
            continue
        mid = (row.bid_price + row.ask_price) / 2
        if 0.85 <= mid <= 0.97:
            side, price, queue = 'up', row.bid_price, row.bid_size
        elif 0.85 <= 1 - mid <= 0.97:
            side, price, queue = 'dn', 1 - row.ask_price, row.ask_size
        else:
            continue
        up_lvl = price if side == 'up' else 1 - price
        tts = t.timestamp_us.values
        lo = np.searchsorted(tts, sig_us + 500_000, 'right')
        hi = np.searchsorted(tts, (wts + 90) * 1_000_000, 'right')
        px = t.price.values[lo:hi]
        sz = t['size'].values[lo:hi]
        if side == 'up':
            better = px < up_lvl - 1e-9
            at = np.abs(px - up_lvl) <= 1e-9
        else:
            better = px > up_lvl + 1e-9
            at = np.abs(px - up_lvl) <= 1e-9
        win = (w.up_won == 1) if side == 'up' else (w.up_won == 0)
        pnl_share = (1 - price) if win else -price
        # simulate each clip size independently
        rec = dict(date=date, wts=wts, price=price, win=bool(win))
        for clip in CLIPS:
            shares = clip / price
            qq = queue
            filled = 0.0
            for j in range(len(px)):
                if better[j]:
                    filled += sz[j]
                elif at[j]:
                    if qq > 0:
                        eat = min(qq, sz[j])
                        qq -= eat
                        filled += max(sz[j] - eat, 0)
                    else:
                        filled += sz[j]
                if filled >= shares:
                    filled = shares
                    break
            rec[f'fill_{clip}'] = filled
            rec[f'pnl_{clip}'] = filled * pnl_share
        out.append(rec)
    return out


def main():
    dates = sorted(p.stem for p in (DAILY / '5m' / 'quotes').glob('*.parquet'))
    dates = [d for d in dates if split_of('5m', d) in ('train', 'test')]
    rows = []
    with ProcessPoolExecutor(max_workers=3) as ex:
        for r in ex.map(run_day, dates):
            rows.extend(r)
    df = pd.DataFrame(rows)
    n_days = df.date.nunique()
    print(f"signals: {len(df)} over {n_days} days ({len(df)/n_days:.1f}/day)")
    print(f"{'clip':>6} {'fullfill%':>9} {'anyfill%':>8} {'EV c/sh':>8} {'$/signal':>9} {'$/day':>8}")
    for clip in CLIPS:
        f = df[f'fill_{clip}']
        p = df[f'pnl_{clip}']
        shares_req = clip / df.price
        full = (f >= shares_req - 1e-6).mean()
        anyf = (f > 0).mean()
        ev_sh = p.sum() / max(f.sum(), 1e-9) * 100
        per_sig = p.mean()
        print(f"{clip:>6} {full*100:>8.1f}% {anyf*100:>7.1f}% {ev_sh:>8.2f} {per_sig:>9.2f} {per_sig*len(df)/n_days:>8.2f}")
    df.to_parquet(pathlib.Path(__file__).resolve().parent.parent / 'results/ev_size_curve.parquet', index=False)


if __name__ == '__main__':
    main()
