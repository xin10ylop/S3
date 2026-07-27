"""Follow-ups: why 15m is degenerate, significance tests, multiple-comparison, thin-tape flags."""
import json, math, statistics as st
from collections import Counter

SP = "/tmp/claude-0/-home-user-S3/8087631e-229b-5943-8560-8dda6ec827f4/scratchpad"
ASSETS = ["eth", "sol", "xrp", "doge", "btc"]


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    s = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - s) / d, (c + s) / d)


def binom_sf(k, n, p):
    """P(X >= k) for Binomial(n,p)."""
    tot = 0.0
    for i in range(k, n + 1):
        tot += math.comb(n, i) * p ** i * (1 - p) ** (n - i)
    return tot


def load(a, d):
    with open(f"{SP}/sweep_{a}_{d}_14d.json") as f:
        raw = json.load(f)
    return raw, [x for x in raw if "skip" not in x]


print("=" * 118)
print("A. WHY 15m PRODUCES ALMOST NO SIGNALS -- distribution of the o60 median price")
print("   (at +60s a 15m window is only 6.7% elapsed, so the price is still near 0.50)")
print("=" * 118)
print(f"{'asset/dur':<12}{'windows w/ o60 print':>22}{'p05':>7}{'p25':>7}{'med':>7}{'p75':>7}{'p95':>7}"
      f"{'%|m-.5|>.35':>13}{'%favorite':>11}")
for a in ASSETS:
    for d, lab in [(300, "5m"), (900, "15m")]:
        raw, sigs = load(a, d)
        # recompute the o60 median distribution: need it from raw; only signals kept it.
        # Use skip labels: windows with a price but no favorite are 'no_favorite'.
        n_priced = sum(1 for x in raw if x.get("skip") != "no_prints_o60"
                       and x.get("skip") not in ("no_market", "no_tape", "unresolved"))
        n_fav = len(sigs)
        favs = sorted(x["fav_price"] for x in sigs)
        q = lambda f: favs[min(int(f * len(favs)), len(favs) - 1)] if favs else float("nan")
        print(f"{a+' '+lab:<12}{n_priced:>22}{'':>7}{'':>7}{'':>7}{'':>7}{'':>7}"
              f"{'':>13}{n_fav/max(n_priced,1)*100:>10.1f}%")

print("\n  -> the 'no_favorite' skip count IS the answer: see table below")
print(f"{'asset/dur':<12}{'total win':>10}{'no_prints':>11}{'priced':>9}{'no_favorite':>13}"
      f"{'signals':>9}{'signal rate of priced':>23}")
for a in ASSETS:
    for d, lab in [(300, "5m"), (900, "15m")]:
        raw, sigs = load(a, d)
        c = Counter(x.get("skip") for x in raw if "skip" in x)
        npr = c.get("no_prints_o60", 0)
        nf = c.get("no_favorite", 0)
        priced = nf + len(sigs)
        print(f"{a+' '+lab:<12}{len(raw):>10}{npr:>11}{priced:>9}{nf:>13}{len(sigs):>9}"
              f"{len(sigs)/max(priced,1)*100:>22.2f}%")

print("\n" + "=" * 118)
print("B. SIGNIFICANCE OF EACH 5m RESULT vs ITS OWN BREAKEVEN (exact binomial, one-sided)")
print("   H0: win rate == mean entry (zero EV).  8 combos tested -> Bonferroni alpha = 0.05/8 = 0.00625")
print("=" * 118)
print(f"{'asset/dur':<12}{'k':>5}{'n':>6}{'win%':>8}{'breakeven%':>12}{'p_onesided':>12}"
      f"{'sig@0.05':>10}{'sig@Bonf':>10}")
res = []
for a in ["eth", "sol", "xrp", "doge"]:
    for d, lab in [(300, "5m"), (900, "15m")]:
        raw, sigs = load(a, d)
        n = len(sigs); k = sum(x["won"] for x in sigs)
        me = sum(x["entry"] for x in sigs) / n
        p = binom_sf(k, n, me)
        res.append((a, lab, k, n, k / n, me, p))
        print(f"{a+' '+lab:<12}{k:>5}{n:>6}{k/n*100:>8.2f}{me*100:>12.2f}{p:>12.4f}"
              f"{'YES' if p<0.05 else 'no':>10}{'YES' if p<0.00625 else 'no':>10}")
raw, sigs = load("btc", 300)
n = len(sigs); k = sum(x["won"] for x in sigs); me = sum(x["entry"] for x in sigs) / n
print(f"{'btc 5m (ref)':<12}{k:>5}{n:>6}{k/n*100:>8.2f}{me*100:>12.2f}{binom_sf(k,n,me):>12.4f}")

print("\n" + "=" * 118)
print("C. ETH 5m DEEP DIVE (the only combo with a positive lower bound)")
print("=" * 118)
raw, sigs = load("eth", 300)
n = len(sigs); k = sum(x["won"] for x in sigs); me = sum(x["entry"] for x in sigs) / n
lo, hi = wilson(k, n)
print(f"unconditional : n={n} k={k} win={k/n*100:.2f}% [{lo*100:.2f},{hi*100:.2f}] "
      f"entry={me:.4f} EV={(k/n-me)*100:+.2f}c EV_lo={(lo-me)*100:+.2f}c")
for name, sub in [("given fill (any)", [x for x in sigs if x["fill_any"]]),
                  ("given fill (deep)", [x for x in sigs if x["fill_deep"]]),
                  ("NOT filled", [x for x in sigs if not x["fill_any"]])]:
    if not sub:
        continue
    nn = len(sub); kk = sum(x["won"] for x in sub)
    mm = sum(x["entry"] for x in sub) / nn
    l2, h2 = wilson(kk, nn)
    print(f"{name:<14}: n={nn} k={kk} win={kk/nn*100:.2f}% [{l2*100:.2f},{h2*100:.2f}] "
          f"entry={mm:.4f} EV={(kk/nn-mm)*100:+.2f}c EV_lo={(l2-mm)*100:+.2f}c "
          f"p_binom={binom_sf(kk,nn,mm):.4f}")
sub = [x for x in sigs if x["fav_price"] < 0.91]
nn = len(sub); kk = sum(x["won"] for x in sub); mm = sum(x["entry"] for x in sub) / nn
l2, h2 = wilson(kk, nn)
print(f"{'price<0.91':<14}: n={nn} k={kk} win={kk/nn*100:.2f}% [{l2*100:.2f},{h2*100:.2f}] "
      f"entry={mm:.4f} EV={(kk/nn-mm)*100:+.2f}c EV_lo={(l2-mm)*100:+.2f}c p={binom_sf(kk,nn,mm):.4f}")
sub2 = [x for x in sub if x["fill_any"]]
nn = len(sub2); kk = sum(x["won"] for x in sub2); mm = sum(x["entry"] for x in sub2) / nn
l2, h2 = wilson(kk, nn)
print(f"{'<0.91 + fill':<14}: n={nn} k={kk} win={kk/nn*100:.2f}% [{l2*100:.2f},{h2*100:.2f}] "
      f"entry={mm:.4f} EV={(kk/nn-mm)*100:+.2f}c EV_lo={(l2-mm)*100:+.2f}c p={binom_sf(kk,nn,mm):.4f}")
# daily P&L scale: filled signals/day * EV
fills = [x for x in sigs if x["fill_any"]]
print(f"\nfilled signals/day = {len(fills)/14:.1f};  at 100 shares/fill, expected "
      f"${len(fills)/14*100*(sum(x['won'] for x in fills)/len(fills)-sum(x['entry'] for x in fills)/len(fills)):+.2f}/day")

print("\n" + "=" * 118)
print("D. ETH vs BTC and ETH vs OTHER ALTS, same 14 days (two-proportion z)")
print("=" * 118)


def ztest(k1, n1, k2, n2, lab):
    p1, p2 = k1 / n1, k2 / n2
    pp = (k1 + k2) / (n1 + n2)
    se = math.sqrt(pp * (1 - pp) * (1 / n1 + 1 / n2))
    z = (p1 - p2) / se if se else 0
    print(f"  {lab:<38} {p1*100:6.2f}% (n={n1:>4}) vs {p2*100:6.2f}% (n={n2:>4})  z={z:+.2f}  "
          f"{'sig' if abs(z)>1.96 else 'NOT sig'}")


def kn(a, d):
    _, s = load(a, d)
    return sum(x["won"] for x in s), len(s)


ke, ne = kn("eth", 300)
for other in ["btc", "sol", "xrp", "doge"]:
    ko, no = kn(other, 300)
    ztest(ke, ne, ko, no, f"eth 5m vs {other} 5m")
oth = [(kn(a, 300)) for a in ["sol", "xrp", "doge"]]
ztest(ke, ne, sum(x[0] for x in oth), sum(x[1] for x in oth), "eth 5m vs sol+xrp+doge pooled")
ztest(ke, ne, 768, 822, "eth 5m vs BTC historical (93.43%,n=822)")

print("\n" + "=" * 118)
print("E. TAPE-THINNESS FLAGS (missing-data assessment)")
print("=" * 118)
print(f"{'asset/dur':<12}{'%windows w/ NO o60 print':>26}{'med prints/signal':>19}"
      f"{'%signals from 1 print':>23}   verdict")
for a in ["eth", "sol", "xrp", "doge", "btc"]:
    for d, lab in [(300, "5m"), (900, "15m")]:
        raw, sigs = load(a, d)
        c = Counter(x.get("skip") for x in raw if "skip" in x)
        miss = c.get("no_prints_o60", 0) / len(raw) * 100
        pr = [x["n_sig_prints"] for x in sigs]
        med = st.median(pr) if pr else 0
        one = sum(1 for x in pr if x == 1) / len(pr) * 100 if pr else 0
        if len(sigs) < 20:
            v = "NO DATA (structural: o60 too early for 15m)" if d == 900 else "NO DATA"
        elif med >= 10 and miss < 10:
            v = "OK - price well determined"
        elif med >= 4 and miss < 25:
            v = "MARGINAL - price noisy"
        else:
            v = "TOO THIN - price is ~1 random print; MISSING DATA, not a finding"
        print(f"{a+' '+lab:<12}{miss:>25.1f}%{med:>19.1f}{one:>22.1f}%   {v}")
