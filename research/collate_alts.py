"""Collate the 8 alt-asset sweeps (+ btc baseline) into the requested tables."""
import json, math, statistics as st
from collections import Counter

SP = "/tmp/claude-0/-home-user-S3/8087631e-229b-5943-8560-8dda6ec827f4/scratchpad"
DAYS = 14
ASSETS = ["eth", "sol", "xrp", "doge"]
DURS = [(300, "5m"), (900, "15m")]


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    s = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - s) / d, (c + s) / d)


def load(a, d):
    with open(f"{SP}/sweep_{a}_{d}_{DAYS}d.json") as f:
        raw = json.load(f)
    return raw, [x for x in raw if "skip" not in x]


def stats(rows):
    n = len(rows)
    if n == 0:
        return None
    k = sum(r["won"] for r in rows)
    wr = k / n
    me = sum(r["entry"] for r in rows) / n
    lo, hi = wilson(k, n)
    return dict(n=n, k=k, wr=wr, me=me, lo=lo, hi=hi,
                ev=(wr - me) * 100, ev_lo=(lo - me) * 100, ev_hi=(hi - me) * 100)


rowsout = []
print("=" * 132)
print("PER ASSET x DURATION -- 14 days ending 2026-07-27, fully-indexed tape")
print("=" * 132)
hdr = (f"{'asset/dur':<12}{'windows':>8}{'n_sig':>7}{'sig/day':>9}{'win%':>7}"
       f"{'Wilson95':>16}{'entry':>8}{'EV/sh':>8}{'EV_lo':>8}{'fill%':>7}"
       f"{'n_fill':>7}{'winGF%':>8}{'evGF':>8}{'medPrints':>10}{'p25Pr':>7}")
print(hdr)
print("-" * 132)

for a in ASSETS + ["btc"]:
    for d, lab in DURS:
        raw, sigs = load(a, d)
        s = stats(sigs)
        fills = [r for r in sigs if r["fill_any"]]
        sf = stats(fills)
        prints = sorted(r["n_sig_prints"] for r in sigs)
        medp = st.median(prints) if prints else 0
        p25 = prints[len(prints) // 4] if prints else 0
        fr = len(fills) / max(len(sigs), 1)
        rec = dict(asset=a, dur=lab, durs=d, windows=len(raw), s=s, sf=sf,
                   fr=fr, medp=medp, p25=p25, sigs=sigs, raw=raw, prints=prints)
        rowsout.append(rec)
        print(f"{a+' '+lab:<12}{len(raw):>8}{s['n']:>7}{s['n']/DAYS:>9.1f}"
              f"{s['wr']*100:>7.1f}{f'[{s[chr(108)+chr(111)]*100:.1f},{s[chr(104)+chr(105)]*100:.1f}]':>16}"
              f"{s['me']:>8.4f}{s['ev']:>+8.2f}{s['ev_lo']:>+8.2f}{fr*100:>7.1f}"
              f"{sf['n'] if sf else 0:>7}{(sf['wr']*100 if sf else 0):>8.1f}"
              f"{(sf['ev'] if sf else 0):>+8.2f}{medp:>10.1f}{p25:>7}")

print("\n" + "=" * 132)
print("RANKING BY HONEST LOWER BOUND (Wilson95 lower - mean entry), alts only")
print("=" * 132)
alts = [r for r in rowsout if r["asset"] != "btc"]
alts.sort(key=lambda r: -r["s"]["ev_lo"])
print(f"{'rank':<6}{'asset/dur':<12}{'n':>6}{'win%':>8}{'entry':>9}{'EV/sh':>9}{'EV_lo':>9}{'EV_hi':>9}  verdict")
for i, r in enumerate(alts, 1):
    s = r["s"]
    v = "POSITIVE lower bound" if s["ev_lo"] > 0 else "indistinguishable from 0"
    print(f"{i:<6}{r['asset']+' '+r['dur']:<12}{s['n']:>6}{s['wr']*100:>8.1f}{s['me']:>9.4f}"
          f"{s['ev']:>+9.2f}{s['ev_lo']:>+9.2f}{s['ev_hi']:>+9.2f}  {v}")
print("\n(btc reference, same 14d window:)")
for r in [x for x in rowsout if x["asset"] == "btc"]:
    s = r["s"]
    print(f"      {r['asset']+' '+r['dur']:<12}{s['n']:>6}{s['wr']*100:>8.1f}{s['me']:>9.4f}"
          f"{s['ev']:>+9.2f}{s['ev_lo']:>+9.2f}{s['ev_hi']:>+9.2f}")

print("\n" + "=" * 132)
print("PRINT-COUNT DIAGNOSTICS -- how many trade prints back the o60 median price")
print("=" * 132)
print(f"{'asset/dur':<12}{'n_sig':>7}{'min':>6}{'p25':>6}{'med':>6}{'p75':>6}{'max':>6}"
      f"{'%with1':>9}{'%>=3':>8}{'%>=5':>8}   {'skip_no_prints_o60':>20}{'skip_no_mkt':>12}")
for r in rowsout:
    p = r["prints"]
    n = len(p)
    q = lambda f: p[min(int(f * n), n - 1)] if n else 0
    c = Counter(x.get("skip") for x in r["raw"] if "skip" in x)
    print(f"{r['asset']+' '+r['dur']:<12}{n:>7}{p[0]:>6}{q(.25):>6}{q(.5):>6}{q(.75):>6}{p[-1]:>6}"
          f"{sum(1 for x in p if x==1)/n*100:>9.1f}{sum(1 for x in p if x>=3)/n*100:>8.1f}"
          f"{sum(1 for x in p if x>=5)/n*100:>8.1f}   {c.get('no_prints_o60',0):>20}{c.get('no_market',0):>12}")

print("\nAll skip reasons per sweep:")
for r in rowsout:
    c = Counter(x.get("skip") for x in r["raw"] if "skip" in x)
    print(f"  {r['asset']+' '+r['dur']:<12} {dict(c.most_common())}")

print("\n" + "=" * 132)
print("ROBUSTNESS: restrict to signals with >=3 prints backing the o60 median")
print("=" * 132)
print(f"{'asset/dur':<12}{'n_all':>7}{'n>=3':>7}{'win%':>8}{'entry':>9}{'EV':>8}{'EV_lo':>8}")
for r in rowsout:
    sub = [x for x in r["sigs"] if x["n_sig_prints"] >= 3]
    s = stats(sub)
    if s:
        print(f"{r['asset']+' '+r['dur']:<12}{r['s']['n']:>7}{s['n']:>7}{s['wr']*100:>8.1f}"
              f"{s['me']:>9.4f}{s['ev']:>+8.2f}{s['ev_lo']:>+8.2f}")
    else:
        print(f"{r['asset']+' '+r['dur']:<12}{r['s']['n']:>7}{0:>7}  --")

print("\n" + "=" * 132)
print("PER PRICE BAND (win% / n / EV cents)")
print("=" * 132)
BANDS = [(0.85, 0.88), (0.88, 0.91), (0.91, 0.94), (0.94, 0.9701)]
print(f"{'asset/dur':<12}" + "".join(f"{f'[{a},{b})':>26}" for a, b in BANDS))
for r in rowsout:
    cells = []
    for lo_b, hi_b in BANDS:
        sub = [x for x in r["sigs"] if lo_b <= x["fav_price"] < hi_b]
        s = stats(sub)
        cells.append(f"n={s['n']} {s['wr']*100:.0f}% {s['ev']:+.1f}c" if s else "n=0")
    print(f"{r['asset']+' '+r['dur']:<12}" + "".join(f"{c:>26}" for c in cells))

print("\n" + "=" * 132)
print("PER WEEK (2 weeks)")
print("=" * 132)
for r in rowsout:
    if not r["sigs"]:
        continue
    t0 = min(x["wts"] for x in r["sigs"])
    parts = []
    for w in range(2):
        sub = [x for x in r["sigs"] if t0 + w * 7 * 86400 <= x["wts"] < t0 + (w + 1) * 7 * 86400]
        s = stats(sub)
        parts.append(f"wk{w+1}: n={s['n']:<4} win={s['wr']*100:5.1f}% EV={s['ev']:+6.2f}c" if s else f"wk{w+1}: n=0")
    print(f"{r['asset']+' '+r['dur']:<12} " + "   ".join(parts))

print("\n" + "=" * 132)
print("POOLED ALTS (all 4 assets, per duration) and GRAND POOL")
print("=" * 132)
for d, lab in DURS:
    pool = [x for r in rowsout if r["asset"] != "btc" and r["dur"] == lab for x in r["sigs"]]
    s = stats(pool)
    f = [x for x in pool if x["fill_any"]]
    sf = stats(f)
    print(f"alts {lab:<4} n={s['n']:<5} win={s['wr']*100:5.2f}% [{s['lo']*100:.1f},{s['hi']*100:.1f}] "
          f"entry={s['me']:.4f} EV={s['ev']:+.2f}c EV_lo={s['ev_lo']:+.2f}c  "
          f"fill={len(f)/s['n']*100:.1f}% winGF={sf['wr']*100:.2f}% evGF={sf['ev']:+.2f}c")
allpool = [x for r in rowsout if r["asset"] != "btc" for x in r["sigs"]]
s = stats(allpool)
f = [x for x in allpool if x["fill_any"]]
sf = stats(f)
print(f"ALL ALTS  n={s['n']:<5} win={s['wr']*100:5.2f}% [{s['lo']*100:.1f},{s['hi']*100:.1f}] "
      f"entry={s['me']:.4f} EV={s['ev']:+.2f}c EV_lo={s['ev_lo']:+.2f}c  "
      f"fill={len(f)/s['n']*100:.1f}% winGF={sf['wr']*100:.2f}% evGF={sf['ev']:+.2f}c")

# two-proportion z test: alts pooled vs historical btc 93.43% at n=822
import math as _m
p1, n1 = s["wr"], s["n"]
p2, n2 = 0.9343, 822
pp = (p1 * n1 + p2 * n2) / (n1 + n2)
se = _m.sqrt(pp * (1 - pp) * (1 / n1 + 1 / n2))
print(f"\nalts pooled ({p1*100:.2f}%, n={n1}) vs historical BTC 5m (93.43%, n=822): z={(p1-p2)/se:+.2f}")

# per-asset pooled across durations
print("\nper-asset pooled across both durations:")
for a in ASSETS:
    pool = [x for r in rowsout if r["asset"] == a for x in r["sigs"]]
    ss = stats(pool)
    ff = [x for x in pool if x["fill_any"]]
    sff = stats(ff)
    print(f"  {a:<5} n={ss['n']:<5} win={ss['wr']*100:5.2f}% [{ss['lo']*100:.1f},{ss['hi']*100:.1f}] "
          f"entry={ss['me']:.4f} EV={ss['ev']:+.2f}c EV_lo={ss['ev_lo']:+.2f}c fill={len(ff)/ss['n']*100:.1f}% "
          f"winGF={sff['wr']*100:.2f}%")

# save a compact csv
with open(f"{SP}/alt_asset_summary.csv", "w") as fo:
    fo.write("asset,dur,windows,n_sig,sig_per_day,win_rate,wilson_lo,wilson_hi,mean_entry,"
             "ev_cents,ev_lo_cents,fill_rate,n_fills,win_given_fill,ev_given_fill,med_prints,pct_prints_ge3\n")
    for r in rowsout:
        st_ = r["s"]; sf_ = r["sf"]; p = r["prints"]
        fo.write(f"{r['asset']},{r['dur']},{r['windows']},{st_['n']},{st_['n']/DAYS:.2f},"
                 f"{st_['wr']:.4f},{st_['lo']:.4f},{st_['hi']:.4f},{st_['me']:.4f},"
                 f"{st_['ev']:.2f},{st_['ev_lo']:.2f},{r['fr']:.4f},{sf_['n']},"
                 f"{sf_['wr']:.4f},{sf_['ev']:.2f},{r['medp']},"
                 f"{sum(1 for x in p if x>=3)/len(p):.4f}\n")
print(f"\ncsv: {SP}/alt_asset_summary.csv")
