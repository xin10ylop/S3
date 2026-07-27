"""The decisive cut: does the fill proxy destroy the edge? filled vs unfilled, 30d, all assets."""
import json, math

SP = "/tmp/claude-0/-home-user-S3/8087631e-229b-5943-8560-8dda6ec827f4/scratchpad"


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    s = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - s) / d, (c + s) / d)


def load30(a):
    with open(f"{SP}/sweep_{a}_300_30d.json") as f:
        return [x for x in json.load(f) if "skip" not in x]


def agg(rows):
    n = len(rows)
    if n == 0:
        return None
    k = sum(x["won"] for x in rows)
    me = sum(x["entry"] for x in rows) / n
    lo, hi = wilson(k, n)
    return dict(n=n, k=k, wr=k / n, me=me, lo=lo, hi=hi, ev=(k / n - me) * 100, ev_lo=(lo - me) * 100)


print("=" * 120)
print("K. ADVERSE SELECTION OF THE MAKER FILL, 30 days, 5m")
print("   Our resting bid sits 1c BELOW the o60 favorite price, so a 'fill' requires the")
print("   favorite to tick DOWN within 30s. Question: does that select losers?")
print("=" * 120)
print(f"{'asset':<8}{'n':>6}{'fill%':>8}{'win_FILLED':>12}{'win_UNFILL':>12}{'gap_pp':>9}{'z':>8}"
      f"{'EV_filled':>11}{'EV_lo_fill':>12}")
tot_f = tot_u = 0
kf = ku = 0
for a in ["eth", "sol", "xrp", "doge", "btc"]:
    s = load30(a)
    f = [x for x in s if x["fill_any"]]
    u = [x for x in s if not x["fill_any"]]
    af, au = agg(f), agg(u)
    pp = (af["k"] + au["k"]) / (af["n"] + au["n"])
    se = math.sqrt(pp * (1 - pp) * (1 / af["n"] + 1 / au["n"]))
    z = (af["wr"] - au["wr"]) / se
    tot_f += af["n"]; tot_u += au["n"]; kf += af["k"]; ku += au["k"]
    print(f"{a:<8}{len(s):>6}{len(f)/len(s)*100:>8.1f}{af['wr']*100:>12.2f}{au['wr']*100:>12.2f}"
          f"{(af['wr']-au['wr'])*100:>+9.2f}{z:>+8.2f}{af['ev']:>+11.2f}{af['ev_lo']:>+12.2f}")
pp = (kf + ku) / (tot_f + tot_u)
se = math.sqrt(pp * (1 - pp) * (1 / tot_f + 1 / tot_u))
z = (kf / tot_f - ku / tot_u) / se
print(f"{'POOLED':<8}{tot_f+tot_u:>6}{tot_f/(tot_f+tot_u)*100:>8.1f}{kf/tot_f*100:>12.2f}"
      f"{ku/tot_u*100:>12.2f}{(kf/tot_f-ku/tot_u)*100:>+9.2f}{z:>+8.2f}")

print("\n" + "=" * 120)
print("L. THE HONEST TRADABLE NUMBER: EV given fill, 30d")
print("=" * 120)
print(f"{'asset':<10}{'n_fills':>9}{'fills/day':>11}{'win%':>8}{'Wilson95':>17}{'entry':>9}"
      f"{'EV/sh':>9}{'EV_lo':>9}{'EV_hi':>9}  verdict")
rank = []
for a in ["eth", "sol", "xrp", "doge", "btc"]:
    s = load30(a)
    f = [x for x in s if x["fill_any"]]
    af = agg(f)
    v = ("POSITIVE lower bound" if af["ev_lo"] > 0 else
         ("indistinguishable from 0" if (af["lo"] - af["me"]) * 100 < 0 < (af["hi"] - af["me"]) * 100
          else "NEGATIVE upper bound"))
    ev_hi = (af["hi"] - af["me"]) * 100
    rank.append((af["ev_lo"], a, af, ev_hi, len(f)))
    print(f"{a:<10}{af['n']:>9}{af['n']/30:>11.1f}{af['wr']*100:>8.2f}"
          f"{f'[{af[chr(108)+chr(111)]*100:.1f},{af[chr(104)+chr(105)]*100:.1f}]':>17}{af['me']:>9.4f}"
          f"{af['ev']:>+9.2f}{af['ev_lo']:>+9.2f}{ev_hi:>+9.2f}  {v}")

print("\n" + "=" * 120)
print("M. ETH 5m: the ONLY candidate. Does anything survive after conditioning on fill?")
print("=" * 120)
s = load30("eth")
cuts = [
    ("all filled", lambda x: x["fill_any"]),
    ("filled, price<0.88", lambda x: x["fill_any"] and x["fav_price"] < 0.88),
    ("filled, 0.88<=p<0.91", lambda x: x["fill_any"] and 0.88 <= x["fav_price"] < 0.91),
    ("filled, price<0.91", lambda x: x["fill_any"] and x["fav_price"] < 0.91),
    ("filled, price>=0.91", lambda x: x["fill_any"] and x["fav_price"] >= 0.91),
    ("filled, >=10 prints", lambda x: x["fill_any"] and x["n_sig_prints"] >= 10),
    ("filled shallow only", lambda x: x["fill_any"] and not x["fill_deep"]),
    ("filled deep/through", lambda x: x["fill_deep"]),
]
for lab, fn in cuts:
    sub = [x for x in s if fn(x)]
    a_ = agg(sub)
    if not a_:
        print(f"  {lab:<24} n=0")
        continue
    print(f"  {lab:<24} n={a_['n']:<5} win={a_['wr']*100:6.2f}% "
          f"[{a_['lo']*100:5.2f},{a_['hi']*100:6.2f}] entry={a_['me']:.4f} "
          f"EV={a_['ev']:+6.2f}c EV_lo={a_['ev_lo']:+6.2f}c")

print("\n" + "=" * 120)
print("N. SANITY: is the 'unfilled wins more' effect just price drift? mean entry filled vs unfilled")
print("=" * 120)
for a in ["eth", "sol", "xrp", "doge", "btc"]:
    s = load30(a)
    f = [x for x in s if x["fill_any"]]
    u = [x for x in s if not x["fill_any"]]
    print(f"  {a:<6} entry filled={sum(x['entry'] for x in f)/len(f):.4f}  "
          f"unfilled={sum(x['entry'] for x in u)/len(u):.4f}  "
          f"(near-identical entries -> the win-rate gap is NOT a price-level artifact)")
