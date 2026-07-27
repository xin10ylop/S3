"""30-day power extension + out-of-sample check of the eth 5m result."""
import json, math, statistics as st

SP = "/tmp/claude-0/-home-user-S3/8087631e-229b-5943-8560-8dda6ec827f4/scratchpad"


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    s = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - s) / d, (c + s) / d)


def binom_sf(k, n, p):
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k, n + 1))


def load30(a):
    with open(f"{SP}/sweep_{a}_300_30d.json") as f:
        raw = json.load(f)
    return [x for x in raw if "skip" not in x]


def row(sigs, lab, extra=""):
    n = len(sigs)
    if n == 0:
        print(f"{lab:<26} n=0")
        return None
    k = sum(x["won"] for x in sigs)
    me = sum(x["entry"] for x in sigs) / n
    lo, hi = wilson(k, n)
    p = binom_sf(k, n, me)
    print(f"{lab:<26} n={n:<5} win={k/n*100:6.2f}% [{lo*100:5.2f},{hi*100:6.2f}] entry={me:.4f} "
          f"EV={(k/n-me)*100:+6.2f}c EV_lo={(lo-me)*100:+6.2f}c p={p:.4f} {extra}")
    return dict(n=n, k=k, wr=k / n, me=me, lo=lo, ev=(k / n - me) * 100, ev_lo=(lo - me) * 100, p=p)


print("=" * 122)
print("F. 30-DAY POWER EXTENSION, 5m only (2026-06-27 .. 2026-07-27)")
print("=" * 122)
res30 = {}
for a in ["eth", "sol", "xrp", "doge", "btc"]:
    s = load30(a)
    res30[a] = row(s, f"{a} 5m 30d ALL")
print()
for a in ["eth", "sol", "xrp", "doge", "btc"]:
    s = load30(a)
    f = [x for x in s if x["fill_any"]]
    row(f, f"{a} 5m 30d GIVEN FILL", f"fill={len(f)/len(s)*100:.1f}%")

print("\n" + "=" * 122)
print("G. OUT-OF-SAMPLE SPLIT for the alts: days 15-30 ago (never seen in the 14d table) vs last 14d")
print("=" * 122)
import time
now = int(time.time())
cut = now - 14 * 86400
for a in ["eth", "sol", "xrp", "doge", "btc"]:
    s = load30(a)
    old = [x for x in s if x["wts"] < cut]
    new = [x for x in s if x["wts"] >= cut]
    print(f"--- {a} 5m")
    row(old, "   older half (d15-30)")
    row(new, "   recent half (d1-14)")

print("\n" + "=" * 122)
print("H. PRICE-BAND STRUCTURE over 30 days (is 'cheap favorites are better' a real structure?)")
print("=" * 122)
BANDS = [(0.85, 0.88), (0.88, 0.91), (0.91, 0.94), (0.94, 0.9701)]
print(f"{'asset':<8}" + "".join(f"{f'[{a},{b})':>30}" for a, b in BANDS))
for a in ["eth", "sol", "xrp", "doge", "btc"]:
    s = load30(a)
    cells = []
    for lo_b, hi_b in BANDS:
        sub = [x for x in s if lo_b <= x["fav_price"] < hi_b]
        if not sub:
            cells.append("n=0")
            continue
        n = len(sub); k = sum(x["won"] for x in sub)
        me = sum(x["entry"] for x in sub) / n
        cells.append(f"n={n} {k/n*100:.1f}% EV={(k/n-me)*100:+.1f}c")
    print(f"{a:<8}" + "".join(f"{c:>30}" for c in cells))

print("\n" + "=" * 122)
print("I. ETH 5m 30d -- the specific configuration that looked best at 14d (price<0.91)")
print("=" * 122)
s = load30("eth")
row(s, "eth all")
row([x for x in s if x["fav_price"] < 0.91], "eth price<0.91")
row([x for x in s if x["fav_price"] < 0.91 and x["fill_any"]], "eth price<0.91 & filled")
row([x for x in s if x["fill_any"]], "eth filled (all bands)")
row([x for x in s if not x["fill_any"]], "eth NOT filled")
print()
print("same cut on the other assets over 30d (is price<0.91 generically good?):")
for a in ["sol", "xrp", "doge", "btc"]:
    ss = load30(a)
    row([x for x in ss if x["fav_price"] < 0.91], f"  {a} price<0.91")
    row([x for x in ss if x["fav_price"] < 0.91 and x["fill_any"]], f"  {a} <0.91 & filled")

print("\n" + "=" * 122)
print("J. POOLED ALTS 30d and print quality")
print("=" * 122)
pool = [x for a in ["eth", "sol", "xrp", "doge"] for x in load30(a)]
row(pool, "ALL ALTS 5m 30d")
row([x for x in pool if x["fill_any"]], "ALL ALTS 5m 30d filled")
row([x for x in pool if x["n_sig_prints"] >= 5], "ALL ALTS 30d, >=5 prints")
print()
for a in ["eth", "sol", "xrp", "doge", "btc"]:
    s = load30(a)
    pr = sorted(x["n_sig_prints"] for x in s)
    print(f"  {a:<6} n={len(s):<5} med prints={st.median(pr):>5.1f}  "
          f"%1-print={sum(1 for x in pr if x==1)/len(pr)*100:>5.1f}%  "
          f"%>=5={sum(1 for x in pr if x>=5)/len(pr)*100:>5.1f}%")
