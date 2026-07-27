"""Day-matched validation, and what the local 54-day gap did to the baseline."""
import math, warnings
import numpy as np, pandas as pd
from scipy import stats
import statsmodels.api as sm

warnings.filterwarnings("ignore")
SCRATCH = "/tmp/claude-0/-home-user-S3/8087631e-229b-5943-8560-8dda6ec827f4/scratchpad"
sig = pd.read_parquet(f"{SCRATCH}/sweep120_signals.parquet")
np.random.seed(3)


def wilson(k, n, z=1.96):
    p = k / n; d = 1 + z * z / n; c = p + z * z / (2 * n)
    s = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - s) / d, (c + s) / d)


def blk(df):
    n = len(df); k = int(df.won.sum()); wr = k / n; me = df.entry.mean()
    lo, hi = wilson(k, n); return n, wr, lo, hi, me, (wr - me) * 100


def line(nm, df):
    n, wr, lo, hi, me, ev = blk(df)
    print(f"  {nm:<44} n={n:<5} win={wr*100:5.2f}% [{lo*100:5.2f},{hi*100:5.2f}]  "
          f"entry={me:.4f}  EV={ev:+6.2f}c")


def ztest(a, b):
    k1, n1 = int(a.won.sum()), len(a); k2, n2 = int(b.won.sum()), len(b)
    pp = (k1 + k2) / (n1 + n2)
    z = (k1 / n1 - k2 / n2) / math.sqrt(pp * (1 - pp) * (1 / n1 + 1 / n2))
    return z, 2 * (1 - stats.norm.cdf(abs(z)))


loc = pd.read_parquet("/home/user/S3/data/master/5m_master.parquet",
                      columns=["wts", "date", "up_won", "bid_o60", "ask_o60"]).dropna(
                          subset=["bid_o60", "ask_o60"])
loc["mid"] = (loc.bid_o60 + loc.ask_o60) / 2
up = (loc["mid"] >= 0.85) & (loc["mid"] <= 0.97)
dn = ((1 - loc["mid"]) >= 0.85) & ((1 - loc["mid"]) <= 0.97)
ls = loc[up | dn].copy()
ls["fav_up"] = up[up | dn]
ls["fav_price"] = np.where(ls.fav_up, ls["mid"], (1 - ls["mid"]).round(4))
ls["entry"] = (ls.fav_price - 0.01).round(4)
ls["won"] = np.where(ls.fav_up, ls.up_won, 1 - ls.up_won)
ls["d"] = pd.to_datetime(ls.date).dt.date
sig["d"] = sig.dt.dt.date
covered = set(ls.d.unique())

print("=" * 108)
print("F. DAY-MATCHED VALIDATION — restrict API to the exact calendar days the local file covers")
print("=" * 108)
both_days = sorted(covered & set(sig.d.unique()))
print(f"days covered by BOTH: {len(both_days)}  ({both_days[0]} .. {both_days[-1]})")
a = sig[sig.d.isin(both_days)]
b = ls[ls.d.isin(both_days)]
line("API   (trade-median @o60)", a)
line("LOCAL (order-book mid @o60)", b)
z, p = ztest(a, b)
print(f"  gap = {(a.won.mean()-b.won.mean())*100:+.2f}pp   z={z:+.3f}  p={p:.4f}  "
      f"-> {'AGREE (validated)' if abs(z)<1.96 else 'DISAGREE'}")
print(f"  EV gap = {blk(a)[5]-blk(b)[5]:+.2f}c")

print("\n" + "=" * 108)
print("G. WHAT THE 54-DAY LOCAL GAP HID (2026-05-13 .. 2026-07-05, API-only data)")
print("=" * 108)
gap_days = sorted(set(sig.d.unique()) - covered)
gap_days = [x for x in gap_days if pd.Timestamp(x) <= pd.Timestamp("2026-07-07")]
print(f"local-missing days inside the local nominal span: {len(gap_days)} "
      f"({gap_days[0]} .. {gap_days[-1]})")
line("API on local-COVERED days", a)
line("API on local-MISSING days (the hole)", sig[sig.d.isin(gap_days)])
z, p = ztest(a, sig[sig.d.isin(gap_days)])
print(f"  covered-vs-hole: z={z:+.3f}  p={p:.4f}")
print("\n  local baseline composition (n=822 by month): "
      + str(ls.groupby(pd.to_datetime(ls.date).dt.strftime('%Y-%m')).size().to_dict()))
print("  -> 82% of the 'historical' sample is Feb-Apr; May is truncated at the 12th; "
      "June is absent entirely.")

print("\n" + "=" * 108)
print("H. CLEAN SAME-METHOD DECAY TEST (API only, pre- vs post- the local data gap)")
print("=" * 108)
cut = pd.Timestamp("2026-05-13").date()
pre, post = sig[sig.d < cut], sig[sig.d >= cut]
line("API 2026-03-29 .. 2026-05-12", pre)
line("API 2026-05-13 .. 2026-07-27", post)
z, p = ztest(post, pre)
print(f"  diff = {(post.won.mean()-pre.won.mean())*100:+.2f}pp  z={z:+.3f}  p={p:.4f}")
x1 = ((post.won - post.entry) * 100).values; x0 = ((pre.won - pre.entry) * 100).values
B = 20000
bs = np.random.choice(x1, (B, len(x1)), True).mean(1) - np.random.choice(x0, (B, len(x0)), True).mean(1)
print(f"  EV diff = {x1.mean()-x0.mean():+.2f}c  boot95 [{np.percentile(bs,2.5):+.2f},"
      f"{np.percentile(bs,97.5):+.2f}]c  p={2*min((bs<=0).mean(),(bs>=0).mean()):.4f}")
print("  FILLED-only version:")
line("    filled, pre", pre[pre.fill_any == 1]); line("    filled, post", post[post.fill_any == 1])
z, p = ztest(post[post.fill_any == 1], pre[pre.fill_any == 1])
print(f"    z={z:+.3f}  p={p:.4f}")

print("\n" + "=" * 108)
print("I. ADVERSE SELECTION robustness (full n=1246)")
print("=" * 108)
for a2, b2 in [(0.85, 0.88), (0.88, 0.91), (0.91, 0.94), (0.94, 0.9701)]:
    g = sig[(sig.fav_price >= a2) & (sig.fav_price < b2)]
    gf, gn = g[g.fill_any == 1], g[g.fill_any == 0]
    if len(gn) > 5:
        z, p = ztest(gf, gn)
        print(f"  band [{a2},{b2}) : filled n={len(gf):<4} {gf.won.mean()*100:5.2f}%   "
              f"unfilled n={len(gn):<4} {gn.won.mean()*100:5.2f}%   diff="
              f"{(gf.won.mean()-gn.won.mean())*100:+6.2f}pp  p={p:.4f}")
X = sm.add_constant(sig[["fill_any", "fav_price"]].astype(float))
r = sm.Logit(sig.won.astype(float), X).fit(disp=0)
print(f"\n  logistic won ~ fill_any + fav_price: fill coef = {r.params['fill_any']:+.4f}  "
      f"p = {r.pvalues['fill_any']:.4f}  (price-controlled -> adverse selection is NOT a price artifact)")
print(f"  half-window check, fill_any in first 60d vs last 60d: "
      f"{sig[sig.wts<sig.wts.median()].fill_any.mean()*100:.1f}% vs "
      f"{sig[sig.wts>=sig.wts.median()].fill_any.mean()*100:.1f}%")

print("\n" + "=" * 108)
print("J. HEADLINE NUMBERS")
print("=" * 108)
for nm, g in (("120d unconditional", sig), ("120d given-fill", sig[sig.fill_any == 1]),
              ("last 60d unconditional", sig[sig.wts >= sig.wts.max() - 60 * 86400]),
              ("last 60d given-fill", sig[(sig.wts >= sig.wts.max() - 60 * 86400) & (sig.fill_any == 1)])):
    n, wr, lo, hi, me, ev = blk(g)
    x = ((g.won - g.entry) * 100).values
    bs = np.random.choice(x, (20000, len(x)), True).mean(1)
    c = np.percentile(bs, [2.5, 97.5])
    print(f"  {nm:<26} n={n:<5} win={wr*100:5.2f}% [{lo*100:.2f},{hi*100:.2f}]  "
          f"EV={ev:+.2f}c [{c[0]:+.2f},{c[1]:+.2f}]  "
          f"{'EV>0 significant' if c[0]>0 else 'EV indistinguishable from 0'}")
