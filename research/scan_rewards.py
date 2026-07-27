"""LIQUIDITY REWARDS -- the venue's only fill-independent income stream.

Polymarket pays makers per minute for resting inside max_spread of the mid, at
or above min_size, split pro-rata by a score. You are paid for QUOTING, not for
being filled -- which is precisely the property every strategy so far lacked.

Exp 73 found 0 of 8,831 crypto markets carry rewards, but other categories do.
This measures the CURRENT marginal yield: what a fresh $100 earns per day given
the size already resting, plus the taker fee that would apply on an exit.

Marginal daily yield for adding X to a pool with S already resting:
    rate * X/(S+X) / X    ->    rate/(S+X)   per dollar
"""
import json, requests, statistics, time
from concurrent.futures import ThreadPoolExecutor
S = requests.Session(); S.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=64, max_retries=3))
C = "https://clob.polymarket.com"

def sampling():
    out, cur = [], ""
    for _ in range(200):
        r = S.get(f"{C}/sampling-markets", params={"next_cursor": cur} if cur else {}, timeout=25)
        if not r.ok: break
        j = r.json(); out += j.get("data", [])
        cur = j.get("next_cursor")
        if not cur or cur == "LTE=": break
    return out

def book_qual(job):
    tok, maxspr, minsz = job
    try:
        b = S.get(f"{C}/book", params={"token_id": tok}, timeout=10).json()
        bids = [(float(x["price"]), float(x["size"])) for x in b.get("bids", [])]
        asks = [(float(x["price"]), float(x["size"])) for x in b.get("asks", [])]
        if not bids or not asks: return None
        bb, ba = max(bids)[0], min(asks)[0]
        mid = (bb + ba) / 2
        lim = maxspr / 100.0
        qual = sum(p*s for p, s in bids if mid - p <= lim and s >= minsz) + \
               sum(p*s for p, s in asks if p - mid <= lim and s >= minsz)
        return {"mid": mid, "spread": ba - bb, "qual_usd": qual}
    except Exception:
        return None

t0 = time.time()
ms = sampling()
print(f"{len(ms)} reward-enabled markets from /sampling-markets  ({time.time()-t0:.0f}s)")
live = []
for m in ms:
    if m.get("closed") or not m.get("active"): continue
    rw = (m.get("rewards") or {})
    rates = rw.get("rates") or []
    daily = sum(float(x.get("rewards_daily_rate") or 0) for x in rates)
    if daily <= 0: continue
    toks = m.get("tokens") or []
    if not toks: continue
    live.append({"slug": m.get("market_slug", "?"), "daily": daily,
                 "maxspr": float(rw.get("max_spread") or 0),
                 "minsz": float(rw.get("min_size") or 0),
                 "tok": toks[0].get("token_id"),
                 "fee": m.get("taker_base_fee"),
                 "end": (m.get("end_date_iso") or "")[:10]})
print(f"{len(live)} ACTIVE with a positive daily rate; "
      f"total ${sum(x['daily'] for x in live):,.0f}/day paid out by the venue\n")

jobs = [(x["tok"], x["maxspr"], x["minsz"]) for x in live]
with ThreadPoolExecutor(max_workers=32) as ex:
    for x, r in zip(live, ex.map(book_qual, jobs)):
        x.update(r or {})

ok = [x for x in live if x.get("qual_usd") is not None]
for x in ok:
    x["marg100"] = x["daily"] * 100.0 / (x["qual_usd"] + 100.0)      # $/day on a fresh $100
print(f"{'$/day':>8} {'qualUSD':>10} {'$100 earns':>11} {'%/day':>7} {'spr':>6} {'maxspr':>7} "
      f"{'minsz':>7} {'fee':>5} {'ends':>11}  slug")
for x in sorted(ok, key=lambda z: -z["marg100"])[:25]:
    print(f"{x['daily']:>8.0f} {x['qual_usd']:>10,.0f} {x['marg100']:>11.2f} "
          f"{x['marg100']:>6.2f}% {x.get('spread',0):>6.3f} {x['maxspr']:>7.1f} "
          f"{x['minsz']:>7.0f} {str(x['fee']):>5} {x['end']:>11}  {x['slug'][:40]}")
if ok:
    mg = sorted(x["marg100"] for x in ok)
    print(f"\nmarginal $/day on a fresh $100 -- median ${statistics.median(mg):.2f} "
          f"p75 ${mg[int(len(mg)*0.75)]:.2f} p90 ${mg[int(len(mg)*0.90)]:.2f} max ${mg[-1]:.2f}")
    ff = [x for x in ok if x.get("fee") == 0]
    print(f"fee-free among them: {len(ff)}/{len(ok)}  "
          f"(exit as taker costs 0, so adverse selection is bounded by the spread)")
    top = sorted(ok, key=lambda z: -z["marg100"])[:20]
    print(f"deployable now on top-20 markets with $100 each: "
          f"${sum(x['marg100'] for x in top):.2f}/day on $2,000 = "
          f"{sum(x['marg100'] for x in top)/2000*100:.2f}%/day")
json.dump(ok, open("/tmp/claude-0/-home-user-S3/8087631e-229b-5943-8560-8dda6ec827f4/scratchpad/rewards.json","w"))
