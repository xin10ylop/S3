"""Follow-ups: local coverage gap, decay on the TRADEABLE (filled) subset,
trailing-window current estimates, and a pooled local+API long trend."""
import json, math, warnings
import numpy as np, pandas as pd
from scipy import stats
import statsmodels.api as sm

warnings.filterwarnings("ignore")
SCRATCH = "/tmp/claude-0/-home-user-S3/8087631e-229b-5943-8560-8dda6ec827f4/scratchpad"
sig = pd.read_parquet(f"{SCRATCH}/sweep120_signals.parquet")
np.random.seed(11)


def wilson(k, n, z=1.96):
    if n == 0:
        return (float("nan"),) * 2
    p = k / n; d = 1 + z * z / n; c = p + z * z / (2 * n)
    s = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - s) / d, (c + s) / d)


def blk(df):
    n = len(df); k = int(df.won.sum()); wr = k / n
    me = df.entry.mean(); lo, hi = wilson(k, n)
    return n, wr, lo, hi, me, (wr - me) * 100


def evci(df, B=20000):
    """bootstrap CI on EV/share in cents."""
    x = ((df.won - df.entry) * 100).values
    bs = np.random.choice(x, (B, len(x)), True).mean(1)
    return np.percentile(bs, [2.5, 97.5])


print("=" * 110)
print("A. LOCAL FILE COVERAGE — does 5m_master really cover Feb12..Jul07 continuously?")
print("=" * 110)
loc = pd.read_parquet("/home/user/S3/data/master/5m_master.parquet",
                      columns=["wts", "date", "up_won", "bid_o60", "ask_o60"])
cnt = loc.groupby(pd.to_datetime(loc.date).dt.strftime("%Y-%m")).size()
book = loc.dropna(subset=["bid_o60", "ask_o60"]).groupby(
    pd.to_datetime(loc.dropna(subset=["bid_o60", "ask_o60"]).date).dt.strftime("%Y-%m")).size()
print(f"{'month':<9}{'windows':>9}{'with o60 book':>16}{'expected(~8928)':>18}")
for m in cnt.index:
    print(f"{m:<9}{cnt[m]:>9}{book.get(m,0):>16}{'':>18}")
d = pd.to_datetime(loc.date).dt.date
allday = pd.date_range(d.min(), d.max(), freq="D").date
missing = sorted(set(allday) - set(d.unique()))
print(f"\ncalendar days in span: {len(allday)}  present: {d.nunique()}  MISSING: {len(missing)}")
if missing:
    print(f"  missing range: {missing[0]} .. {missing[-1]}")

print("\n" + "=" * 110)
print("B. TRADEABLE SUBSET — decay tests restricted to signals that would have FILLED")
print("=" * 110)
f = sig[sig.fill_any == 1].copy()
n, wr, lo, hi, me, ev = blk(f)
c = evci(f)
print(f"FILLED, full 120d: n={n}  win={wr*100:.2f}% [{lo*100:.2f},{hi*100:.2f}]  "
      f"entry={me:.4f}  EV={ev:+.2f}c  boot95 [{c[0]:+.2f},{c[1]:+.2f}]c")
fd = sig[sig.fill_deep == 1]
n2, wr2, lo2, hi2, me2, ev2 = blk(fd); c2 = evci(fd)
print(f"DEEP-FILLED (through) : n={n2}  win={wr2*100:.2f}% [{lo2*100:.2f},{hi2*100:.2f}]  "
      f"entry={me2:.4f}  EV={ev2:+.2f}c  boot95 [{c2[0]:+.2f},{c2[1]:+.2f}]c")

print("\nFILLED by month:")
for mo, g in f.groupby(f.dt.dt.strftime("%Y-%m")):
    n_, wr_, l_, h_, me_, ev_ = blk(g)
    print(f"  {mo}  n={n_:<5} win={wr_*100:5.2f}% [{l_*100:5.1f},{h_*100:5.1f}]  EV={ev_:+6.2f}c")

cut = f.wts.max() - 14 * 86400
r, p_ = f[f.wts >= cut], f[f.wts < cut]
k1, n1 = int(r.won.sum()), len(r); k2, n2b = int(p_.won.sum()), len(p_)
pp = (k1 + k2) / (n1 + n2b)
z = (k1 / n1 - k2 / n2b) / math.sqrt(pp * (1 - pp) * (1 / n1 + 1 / n2b))
print(f"\nFILLED recent14d n={n1} win={k1/n1*100:.2f}% EV={blk(r)[5]:+.2f}c   vs "
      f"prior n={n2b} win={k2/n2b*100:.2f}% EV={blk(p_)[5]:+.2f}c")
print(f"  z={z:+.3f}  p={2*(1-stats.norm.cdf(abs(z))):.4f}")
X = sm.add_constant(f[["day", "fav_price"]].astype(float))
rs = sm.Logit(f.won.astype(float), X).fit(disp=0)
print(f"  logistic day coef (filled only) = {rs.params['day']:+.6f}  p={rs.pvalues['day']:.4f}")

print("\n" + "=" * 110)
print("C. TRAILING-WINDOW CURRENT ESTIMATES (unconditional / filled)")
print("=" * 110)
print(f"{'window':<12}{'n':>5}{'win%':>8}{'Wilson95':>18}{'EV/sh':>9}{'EVboot95':>20}")
mx = sig.wts.max()
for dd in (14, 21, 30, 45, 60, 90, 120):
    for lab, src in (("all", sig), ("fill", sig[sig.fill_any == 1])):
        g = src[src.wts >= mx - dd * 86400]
        n_, wr_, l_, h_, me_, ev_ = blk(g); cc = evci(g)
        print(f"{f'last {dd}d {lab}':<12}{n_:>5}{wr_*100:>8.2f}"
              f"{f'[{l_*100:.1f},{h_*100:.1f}]':>18}{ev_:>+9.2f}"
              f"{f'[{cc[0]:+.2f},{cc[1]:+.2f}]':>20}")

print("\n" + "=" * 110)
print("D. POOLED LONG TREND — local Feb-Mar (book method) + API Mar29-Jul27 (tape method)")
print("=" * 110)
locb = loc.dropna(subset=["bid_o60", "ask_o60"]).copy()
locb["mid"] = (locb.bid_o60 + locb.ask_o60) / 2
up = (locb["mid"] >= 0.85) & (locb["mid"] <= 0.97)
dn = ((1 - locb["mid"]) >= 0.85) & ((1 - locb["mid"]) <= 0.97)
ls = locb[up | dn].copy()
ls["fav_up"] = up[up | dn]
ls["fav_price"] = np.where(ls.fav_up, ls["mid"], (1 - ls["mid"]).round(4))
ls["entry"] = (ls.fav_price - 0.01).round(4)
ls["won"] = np.where(ls.fav_up, ls.up_won, 1 - ls.up_won)
early = ls[ls.wts < sig.wts.min()][["wts", "fav_price", "entry", "won"]]
early["src"] = "local"
late = sig[["wts", "fav_price", "entry", "won"]].copy(); late["src"] = "api"
pool = pd.concat([early, late]).sort_values("wts").reset_index(drop=True)
pool["day"] = (pool.wts - pool.wts.min()) / 86400.0
print(f"pooled n={len(pool)}  ({len(early)} local pre-Mar29 + {len(late)} API)  "
      f"span {pd.to_datetime(pool.wts.min(),unit='s'):%Y-%m-%d} .. "
      f"{pd.to_datetime(pool.wts.max(),unit='s'):%Y-%m-%d} ({pool.day.max():.0f} days)")
n_, wr_, l_, h_, me_, ev_ = blk(pool)
print(f"pooled overall: win={wr_*100:.2f}% [{l_*100:.2f},{h_*100:.2f}] EV={ev_:+.2f}c")
Xp = sm.add_constant(pool[["day", "fav_price"]].astype(float))
rp = sm.Logit(pool.won.astype(float), Xp).fit(disp=0)
print(f"pooled logistic day coef = {rp.params['day']:+.6f}  p = {rp.pvalues['day']:.4f}  "
      f"CI [{rp.conf_int().loc['day',0]:+.6f},{rp.conf_int().loc['day',1]:+.6f}]")
print(f"  -> {rp.params['day']*165:+.3f} log-odds over the 165-day pooled span "
      f"(NOTE: method changes at Mar29, so this is suggestive only)")
print("\npooled by month:")
for mo, g in pool.groupby(pd.to_datetime(pool.wts, unit="s").dt.strftime("%Y-%m")):
    n_, wr_, l_, h_, me_, ev_ = blk(g)
    print(f"  {mo}  n={n_:<5} win={wr_*100:5.2f}% [{l_*100:5.1f},{h_*100:5.1f}]  "
          f"EV={ev_:+6.2f}c   src={g.src.value_counts().to_dict()}")

print("\n" + "=" * 110)
print("E. METHOD-GAP DIAGNOSTIC on same windows (book-mid vs trade-median)")
print("=" * 110)
mg = sig.merge(ls[["wts", "fav_price", "won"]], on="wts", suffixes=("_api", "_loc"))
gap = (mg.fav_price_api - mg.fav_price_loc)
print(f"n matched = {len(mg)}   signed fav_price gap (api - local): mean {gap.mean():+.4f}  "
      f"median {gap.median():+.4f}  |median| {gap.abs().median():.4f}")
print(f"outcome (won) agreement: {(mg.won_api==mg.won_loc).mean()*100:.2f}%  "
      f"-> settlement/winner logic identical")
print(f"api-only signals: {len(sig)-len(mg)}   local-only signals in span: "
      f"{len(ls[(ls.wts>=sig.wts.min())&(ls.wts<=sig.wts.max())])-len(mg)}")
