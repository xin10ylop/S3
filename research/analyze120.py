"""Full statistical analysis of the 120-day btc-updown-5m EFC-M sweep."""
import json, math, warnings
import numpy as np, pandas as pd
from scipy import stats
import statsmodels.api as sm

warnings.filterwarnings("ignore")
SCRATCH = "/tmp/claude-0/-home-user-S3/8087631e-229b-5943-8560-8dda6ec827f4/scratchpad"
rows = json.load(open(f"{SCRATCH}/sweep120_MERGED.json"))
sig = pd.DataFrame([r for r in rows if "skip" not in r]).sort_values("wts").reset_index(drop=True)
sig["dt"] = pd.to_datetime(sig["wts"], unit="s", utc=True)
sig["date"] = sig["dt"].dt.date
sig["iso"] = sig["dt"].dt.strftime("%G-W%V")
sig["day"] = (sig["wts"] - sig["wts"].min()) / 86400.0
np.random.seed(7)


def wilson(k, n, z=1.96):
    if n == 0:
        return (float("nan"),) * 2
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    s = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - s) / d, (c + s) / d)


def blk(df):
    n = len(df); k = int(df.won.sum()); wr = k / n
    me = df.entry.mean(); lo, hi = wilson(k, n)
    return n, wr, lo, hi, me, (wr - me) * 100


print("=" * 118)
print("EFC-M BTC 5m — 120-DAY API SWEEP  (2026-03-29 .. 2026-07-27 UTC)")
print("=" * 118)
n, wr, lo, hi, me, ev = blk(sig)
print(f"windows swept: {len(rows)}   signals: {n}   ({n/120:.1f}/day)")
print(f"OVERALL  win {wr*100:.2f}%  Wilson95 [{lo*100:.2f}, {hi*100:.2f}]   "
      f"mean entry {me:.4f}   EV {ev:+.2f}c/share   EV_lo {(lo-me)*100:+.2f}c")
print(f"HISTORICAL LOCAL BASELINE (Feb12-Jul07, n=822): win 93.43%  entry 0.8787  EV +5.56c")

# ---------------------------------------------------------------- weekly table
print("\n" + "=" * 118)
print("WEEKLY TIME SERIES")
print("=" * 118)
print(f"{'ISO week':<10}{'start':<12}{'n':>5}{'win%':>8}{'Wilson95':>18}{'entry':>9}"
      f"{'EV/sh':>9}{'fill%':>8}{'medPrints':>11}{'winGivenFill%':>15}")
print("-" * 118)
wk = []
for w, g in sig.groupby("iso", sort=True):
    n_, wr_, lo_, hi_, me_, ev_ = blk(g)
    fr = g.fill_any.mean() * 100
    gf = g[g.fill_any == 1]
    wgf = gf.won.mean() * 100 if len(gf) else float("nan")
    print(f"{w:<10}{str(g.date.min()):<12}{n_:>5}{wr_*100:>8.1f}"
          f"{f'[{lo_*100:.1f},{hi_*100:.1f}]':>18}{me_:>9.4f}{ev_:>+9.2f}"
          f"{fr:>8.1f}{g.n_sig_prints.median():>11.0f}{wgf:>15.1f}")
    wk.append((w, n_, wr_, ev_))
print("-" * 118)

print("\nMONTHLY")
print(f"{'month':<10}{'n':>5}{'win%':>8}{'Wilson95':>18}{'entry':>9}{'EV/sh':>9}{'fill%':>8}")
for mo, g in sig.groupby(sig.dt.dt.strftime("%Y-%m")):
    n_, wr_, lo_, hi_, me_, ev_ = blk(g)
    print(f"{mo:<10}{n_:>5}{wr_*100:>8.1f}{f'[{lo_*100:.1f},{hi_*100:.1f}]':>18}"
          f"{me_:>9.4f}{ev_:>+9.2f}{g.fill_any.mean()*100:>8.1f}")

# ------------------------------------------------------------- (a) 14d vs rest
print("\n" + "=" * 118)
print("(a) DECAY TEST 1 — most recent 14 days vs everything before")
print("=" * 118)
cut = sig.wts.max() - 14 * 86400
rec, pri = sig[sig.wts >= cut], sig[sig.wts < cut]
for name, g in (("recent 14d", rec), ("prior 106d", pri)):
    n_, wr_, lo_, hi_, me_, ev_ = blk(g)
    print(f"{name:<12} n={n_:<5} win={wr_*100:5.2f}% [{lo_*100:.2f},{hi_*100:.2f}]  "
          f"entry={me_:.4f}  EV={ev_:+.2f}c")
k1, n1 = int(rec.won.sum()), len(rec); k2, n2 = int(pri.won.sum()), len(pri)
p1, p2 = k1 / n1, k2 / n2
pp = (k1 + k2) / (n1 + n2)
z = (p1 - p2) / math.sqrt(pp * (1 - pp) * (1 / n1 + 1 / n2))
pz = 2 * (1 - stats.norm.cdf(abs(z)))
print(f"\ntwo-proportion z-test on win rate: diff = {(p1-p2)*100:+.2f}pp   z = {z:+.3f}   p = {pz:.4f}")
print(f"Fisher exact p = {stats.fisher_exact([[k1,n1-k1],[k2,n2-k2]])[1]:.4f}")

pnl_r = (rec.won - rec.entry).values * 100
pnl_p = (pri.won - pri.entry).values * 100
obs = pnl_r.mean() - pnl_p.mean()
B = 20000
bs = np.array([np.random.choice(pnl_r, n1, True).mean() - np.random.choice(pnl_p, n2, True).mean()
               for _ in range(B)])
clo, chi = np.percentile(bs, [2.5, 97.5])
pboot = 2 * min((bs <= 0).mean(), (bs >= 0).mean())
print(f"difference in EV/share: {obs:+.2f}c   bootstrap95 CI [{clo:+.2f}, {chi:+.2f}]c   "
      f"boot p = {pboot:.4f}  (B={B})")
t, pt = stats.ttest_ind(pnl_r, pnl_p, equal_var=False)
print(f"Welch t-test on per-trade PnL: t = {t:+.3f}  p = {pt:.4f}")

# --------------------------------------------------------- (b) logistic on time
print("\n" + "=" * 118)
print("(b) DECAY TEST 2 — logistic regression  won ~ days + fav_price")
print("=" * 118)
X = sm.add_constant(sig[["day", "fav_price"]].astype(float))
res = sm.Logit(sig.won.astype(float), X).fit(disp=0)
print(res.summary2().tables[1].to_string())
b, p = res.params["day"], res.pvalues["day"]
print(f"\ntime coefficient = {b:+.6f} log-odds/day (p = {p:.4f}), "
      f"CI [{res.conf_int().loc['day',0]:+.6f}, {res.conf_int().loc['day',1]:+.6f}]")
print(f"  -> over the full 120d span that is {b*120:+.3f} log-odds; "
      f"win rate {'falls' if b<0 else 'rises'} from "
      f"{100/(1+math.exp(-(res.params['const']+res.params['fav_price']*sig.fav_price.mean()))):.2f}% "
      f"to {100/(1+math.exp(-(res.params['const']+b*120+res.params['fav_price']*sig.fav_price.mean()))):.2f}% "
      f"at mean fav_price")
res0 = sm.Logit(sig.won.astype(float), sm.add_constant(sig[["day"]].astype(float))).fit(disp=0)
print(f"unconditional (no price control): day coef = {res0.params['day']:+.6f}  p = {res0.pvalues['day']:.4f}")
# EV regression (linear, on per-trade pnl)
ev_res = sm.OLS((sig.won - sig.entry).astype(float) * 100,
                sm.add_constant(sig[["day"]].astype(float))).fit()
print(f"OLS of EV(cents) on day: slope = {ev_res.params['day']:+.4f} c/day  "
      f"p = {ev_res.pvalues['day']:.4f}  (over 120d: {ev_res.params['day']*120:+.2f}c)")

# ------------------------------------------------------------ (c) changepoint
print("\n" + "=" * 118)
print("(c) DECAY TEST 3 — changepoint scan (max |z| split) + CUSUM")
print("=" * 118)
best = None
scan = []
N = len(sig)
for i in range(60, N - 60):
    a, bq = sig.iloc[:i], sig.iloc[i:]
    ka, na = int(a.won.sum()), len(a); kb, nb = int(bq.won.sum()), len(bq)
    pa, pb = ka / na, kb / nb
    pp2 = (ka + kb) / (na + nb)
    den = math.sqrt(pp2 * (1 - pp2) * (1 / na + 1 / nb))
    if den == 0:
        continue
    zz = (pb - pa) / den
    scan.append((abs(zz), i, zz, pa, pb, a.dt.iloc[-1]))
scan.sort(reverse=True)
az, i, zz, pa, pb, when = scan[0]
print(f"most likely break at signal #{i}/{N}  ~{when:%Y-%m-%d}  "
      f"before {pa*100:.2f}% (n={i}) -> after {pb*100:.2f}% (n={N-i})   z = {zz:+.3f}")
praw = 2 * (1 - stats.norm.cdf(az))
print(f"raw p at that split = {praw:.4f}  (NOT valid — maximised over {len(scan)} splits)")
# permutation test for the max-|z| statistic
perm = []
y = sig.won.values.copy()
for _ in range(2000):
    yp = np.random.permutation(y)
    cs = np.cumsum(yp)
    tot, nn = cs[-1], len(yp)
    idx = np.arange(60, nn - 60)
    ka_ = cs[idx - 1]; na_ = idx
    kb_ = tot - ka_; nb_ = nn - idx
    pa_ = ka_ / na_; pb_ = kb_ / nb_
    pp_ = tot / nn
    den_ = np.sqrt(pp_ * (1 - pp_) * (1 / na_ + 1 / nb_))
    perm.append(np.max(np.abs((pb_ - pa_) / den_)))
perm = np.array(perm)
print(f"PERMUTATION-CORRECTED p for a break anywhere = {(perm >= az).mean():.4f}  "
      f"(null max|z| median {np.median(perm):.2f}, 95th pct {np.percentile(perm,95):.2f})")
# CUSUM
mu = sig.won.mean()
cus = np.cumsum(sig.won.values - mu)
j = int(np.argmax(np.abs(cus)))
print(f"CUSUM extremum at signal #{j} ({sig.dt.iloc[j]:%Y-%m-%d}), value {cus[j]:+.1f} "
      f"({'excess wins before' if cus[j]>0 else 'excess losses before'})")

# ------------------------------------------------- (4) local overlap validation
print("\n" + "=" * 118)
print("(4) VALIDATION — API sweep vs LOCAL order-book ground truth on overlapping dates")
print("=" * 118)
loc = pd.read_parquet("/home/user/S3/data/master/5m_master.parquet",
                      columns=["wts", "date", "up_won", "bid_o60", "ask_o60"])
loc = loc.dropna(subset=["bid_o60", "ask_o60"])
loc["mid"] = (loc.bid_o60 + loc.ask_o60) / 2
print(f"local 5m_master: {len(loc)} windows with o60 book, {loc.date.min()} .. {loc.date.max()}")


def local_sig(df):
    up = (df["mid"] >= 0.85) & (df["mid"] <= 0.97)
    dn = ((1 - df["mid"]) >= 0.85) & ((1 - df["mid"]) <= 0.97)
    s = df[up | dn].copy()
    s["fav_up"] = up[up | dn]
    s["fav_price"] = np.where(s.fav_up, s["mid"], (1 - s["mid"]).round(4))
    s["entry"] = (s.fav_price - 0.01).round(4)
    s["won"] = np.where(s.fav_up, s.up_won, 1 - s.up_won)
    return s


ls_all = local_sig(loc)
n_, wr_, lo_, hi_, me_, ev_ = blk(ls_all)
print(f"LOCAL full period ({loc.date.min()}..{loc.date.max()}):  n={n_}  win={wr_*100:.2f}% "
      f"[{lo_*100:.2f},{hi_*100:.2f}]  entry={me_:.4f}  EV={ev_:+.2f}c   "
      f"<- reported ground truth: n=822, 93.43%, 0.8787, +5.56c")

ov_lo, ov_hi = max(loc.wts.min(), sig.wts.min()), min(loc.wts.max(), sig.wts.max())
lo_ov = ls_all[(ls_all.wts >= ov_lo) & (ls_all.wts <= ov_hi)]
api_ov = sig[(sig.wts >= ov_lo) & (sig.wts <= ov_hi)]
print(f"\nOVERLAP {pd.to_datetime(ov_lo,unit='s'):%Y-%m-%d} .. {pd.to_datetime(ov_hi,unit='s'):%Y-%m-%d}")
for nm, g in (("LOCAL  (order-book mid @o60)", lo_ov), ("API    (trade-median @o60)", api_ov)):
    n_, wr_, lo2, hi2, me_, ev_ = blk(g)
    print(f"  {nm:<32} n={n_:<5} win={wr_*100:5.2f}% [{lo2*100:.2f},{hi2*100:.2f}]  "
          f"entry={me_:.4f}  EV={ev_:+.2f}c")
k1o, n1o = int(api_ov.won.sum()), len(api_ov); k2o, n2o = int(lo_ov.won.sum()), len(lo_ov)
p1o, p2o = k1o / n1o, k2o / n2o
ppo = (k1o + k2o) / (n1o + n2o)
zo = (p1o - p2o) / math.sqrt(ppo * (1 - ppo) * (1 / n1o + 1 / n2o))
print(f"  gap = {(p1o-p2o)*100:+.2f}pp   z = {zo:+.3f}   p = {2*(1-stats.norm.cdf(abs(zo))):.4f} "
      f"-> methods {'AGREE' if abs(zo)<1.96 else 'DISAGREE'}")
# window-level agreement on the shared wts
mg = api_ov.merge(lo_ov[["wts", "fav_price", "won"]], on="wts", suffixes=("_api", "_loc"))
print(f"  same-window matches: {len(mg)} windows both flag a signal; "
      f"outcome agreement {100*(mg.won_api==mg.won_loc).mean():.1f}%, "
      f"median |price gap| {np.median(np.abs(mg.fav_price_api-mg.fav_price_loc)):.4f}")

print("\n  LOCAL by month (independent check of the same decay):")
for mo, g in ls_all.groupby(pd.to_datetime(ls_all.date).dt.strftime("%Y-%m")):
    n_, wr_, l2, h2, me_, ev_ = blk(g)
    print(f"    {mo}  n={n_:<5} win={wr_*100:5.2f}% [{l2*100:.1f},{h2*100:.1f}]  EV={ev_:+.2f}c")

# ------------------------------------------------------ (5) fills, full sample
print("\n" + "=" * 118)
print("(5) FILLS — win-given-fill vs unconditional, full sample")
print("=" * 118)
for nm, g in (("UNCONDITIONAL (all signals)", sig),
              ("GIVEN FILL (any flow <= entry)", sig[sig.fill_any == 1]),
              ("GIVEN DEEP FILL (through)", sig[sig.fill_deep == 1]),
              ("NOT FILLED", sig[sig.fill_any == 0])):
    n_, wr_, l2, h2, me_, ev_ = blk(g)
    print(f"{nm:<32} n={n_:<5} win={wr_*100:5.2f}% [{l2*100:.2f},{h2*100:.2f}]  "
          f"entry={me_:.4f}  EV={ev_:+.2f}c")
kf, nf = int(sig[sig.fill_any == 1].won.sum()), int((sig.fill_any == 1).sum())
kn, nn2 = int(sig[sig.fill_any == 0].won.sum()), int((sig.fill_any == 0).sum())
pf, pn = kf / nf, kn / nn2
ppf = (kf + kn) / (nf + nn2)
zf = (pf - pn) / math.sqrt(ppf * (1 - ppf) * (1 / nf + 1 / nn2))
print(f"\nfill rate overall = {sig.fill_any.mean()*100:.1f}%  (deep {sig.fill_deep.mean()*100:.1f}%)")
print(f"adverse selection test  filled vs unfilled: {(pf-pn)*100:+.2f}pp  z={zf:+.3f}  "
      f"p={2*(1-stats.norm.cdf(abs(zf))):.4f}  -> "
      f"{'NO adverse selection' if abs(zf)<1.96 else 'ADVERSE SELECTION'}")
print("\nfill rate over time (monthly):")
for mo, g in sig.groupby(sig.dt.dt.strftime("%Y-%m")):
    print(f"  {mo}  n={len(g):<5} fill_any={g.fill_any.mean()*100:5.1f}%  "
          f"deep={g.fill_deep.mean()*100:5.1f}%  medPrints={g.n_sig_prints.median():.0f}")

print("\nby fav_price band (full 120d):")
for a2, b2 in [(0.85, 0.88), (0.88, 0.91), (0.91, 0.94), (0.94, 0.9701)]:
    g = sig[(sig.fav_price >= a2) & (sig.fav_price < b2)]
    if len(g):
        n_, wr_, l2, h2, me_, ev_ = blk(g)
        print(f"  [{a2},{b2})  n={n_:<5} win={wr_*100:5.2f}% [{l2*100:.1f},{h2*100:.1f}]  "
              f"entry={me_:.4f}  EV={ev_:+.2f}c")

sig.to_parquet(f"{SCRATCH}/sweep120_signals.parquet")
print(f"\nsignals parquet -> {SCRATCH}/sweep120_signals.parquet")
