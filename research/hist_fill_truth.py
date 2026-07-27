"""Was the historical edge ever real AS EXECUTED?

Runs the EXACT strategy fill simulation (rest at the favorite's best bid from
o60.5 to o90, queue-join-back against the microsecond tape) over every historical
signal, and compares:
    unconditional  vs  win-given-fill  vs  win-given-NO-fill
under four escalating queue assumptions.

If win-given-fill collapses relative to unconditional, the +5.56c historical edge
was a paper edge on quotes that never traded — i.e. the original research was wrong,
not the market.
"""
import sys, pathlib, math, json, warnings
import numpy as np
import pandas as pd
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import DayData, ROOT, DAILY
warnings.filterwarnings("ignore")

# queue multiplier variants: how much size must clear before we fill at our level
VARIANTS = {"base(q x1)": 1.0, "q x2": 2.0, "q x5": 5.0, "below-only": None}


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    s = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - s) / d, (c + s) / d)


def run_day(date):
    wins = pd.read_parquet(ROOT / "master/windows_all.parquet")
    wins = wins[(wins.family == "5m") & (wins.date == date) & (wins.up_won >= 0)]
    if not len(wins):
        return []
    try:
        day = DayData("5m", date)
    except Exception:
        return []
    out = []
    for w in wins.itertuples():
        wts = int(w.wts)
        q, t, b = day.window(wts)
        if q is None or not len(q) or t is None or not len(t):
            continue
        qts = q.timestamp_us.values
        i = np.searchsorted(qts, (wts + 60) * 1_000_000, "right") - 1
        if i < 0:
            continue
        row = q.iloc[i]
        bid, ask = row.bid_price, row.ask_price
        if not (np.isfinite(bid) and np.isfinite(ask)):
            continue
        mid = (bid + ask) / 2
        if 0.85 <= mid <= 0.97:
            fav, entry, queue = "up", round(float(bid), 4), float(row.bid_size)
        elif 0.85 <= 1 - mid <= 0.97:
            fav, entry, queue = "dn", round(float(1 - ask), 4), float(row.ask_size)
        else:
            continue
        if entry <= 0 or entry >= 1:
            continue
        won = int((w.up_won == 1) if fav == "up" else (w.up_won == 0))
        # tape inside the ACTUAL order lifetime (o60.5 -> o90)
        tts = t.timestamp_us.values
        lo = np.searchsorted(tts, (wts * 1_000_000) + 60_500_000, "right")
        hi = np.searchsorted(tts, (wts + 90) * 1_000_000, "right")
        px = t.price.values[lo:hi]
        sz = t["size"].values[lo:hi]
        up_lvl = entry if fav == "up" else round(1 - entry, 4)
        if fav == "up":
            better = px < up_lvl - 5e-4
            at = np.abs(px - up_lvl) <= 5e-4
        else:
            better = px > up_lvl + 5e-4
            at = np.abs(px - up_lvl) <= 5e-4
        below_sz = float(sz[better].sum())
        at_sz = float(sz[at].sum())
        rec = dict(date=date, wts=wts, fav=fav, entry=entry, won=won,
                   queue=queue, below=below_sz, at=at_sz)
        shares = 20.0 / entry
        for name, mult in VARIANTS.items():
            if mult is None:                      # below-only: at-level prints never fill us
                filled = below_sz >= min(shares, 1.0)
            else:
                avail = below_sz + max(at_sz - queue * mult, 0.0)
                filled = avail >= min(shares, 1.0)
            rec[f"fill_{name}"] = int(filled)
        out.append(rec)
    return out


def block(rows, name):
    if not rows:
        print(f"  {name:<26} n=0")
        return
    n = len(rows)
    k = sum(r["won"] for r in rows)
    wr = k / n
    me = sum(r["entry"] for r in rows) / n
    lo, hi = wilson(k, n)
    print(f"  {name:<26} n={n:<5} win={wr*100:5.2f}% [{lo*100:.1f},{hi*100:.1f}]  "
          f"entry={me:.4f}  EV={(wr-me)*100:+6.2f}c  EV_lo={(lo-me)*100:+7.2f}c")


def main():
    dates = sorted(p.stem for p in (DAILY / "5m" / "quotes").glob("*.parquet"))
    print(f"replaying {len(dates)} historical days with the exact strategy fill logic", flush=True)
    rows = []
    with ProcessPoolExecutor(max_workers=3) as ex:
        for i, r in enumerate(ex.map(run_day, dates)):
            rows.extend(r)
            if (i + 1) % 20 == 0:
                print(f"  {i+1}/{len(dates)} days, {len(rows)} signals", flush=True)
    df = pd.DataFrame(rows)
    p = "/tmp/claude-0/-home-user-S3/8087631e-229b-5943-8560-8dda6ec827f4/scratchpad/hist_fill_truth.parquet"
    df.to_parquet(p, index=False)
    print(f"\nsignals: {len(df)}  saved: {p}\n")

    print("=== HISTORICAL, exact strategy fill window (o60.5 -> o90) ===")
    block(rows, "UNCONDITIONAL")
    for name in VARIANTS:
        col = f"fill_{name}"
        f = [r for r in rows if r[col]]
        nf = [r for r in rows if not r[col]]
        fr = len(f) / max(len(rows), 1)
        print(f"\n  -- variant {name}  (fill rate {fr*100:.1f}%)")
        block(f, "GIVEN FILL")
        block(nf, "GIVEN NO FILL")

    print("\n=== by month (base variant) ===")
    df["month"] = df.date.str[:7]
    for m, g in df.groupby("month"):
        gr = g.to_dict("records")
        block(gr, f"{m} uncond")
        block([r for r in gr if r["fill_base(q x1)"]], f"{m} given fill")

    # significance of the fill/no-fill gap, base variant
    f = [r for r in rows if r["fill_base(q x1)"]]
    nf = [r for r in rows if not r["fill_base(q x1)"]]
    if f and nf:
        p1 = sum(r["won"] for r in f) / len(f)
        p2 = sum(r["won"] for r in nf) / len(nf)
        se = math.sqrt(p1 * (1 - p1) / len(f) + p2 * (1 - p2) / len(nf))
        z = (p2 - p1) / se if se else 0
        pv = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))
        print(f"\nADVERSE SELECTION TEST (historical): filled {p1*100:.2f}% vs unfilled {p2*100:.2f}% "
              f"-> gap {(p2-p1)*100:+.2f}pp, z={z:.2f}, p={pv:.5f}")


if __name__ == "__main__":
    main()
