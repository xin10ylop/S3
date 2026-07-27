"""Live half-spread by price band -- the haircut a taker actually pays.

prices-history is the MID (verified exactly). Calibration edges measured in mid
terms are not tradeable until you subtract the cost of lifting the offer. This
measures that cost, and the resting size behind it, across the live venue.
"""
import json, requests, statistics, sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
S=requests.Session(); S.mount("https://",requests.adapters.HTTPAdapter(pool_maxsize=64,max_retries=3))
G="https://gamma-api.polymarket.com"; C="https://clob.polymarket.com"
BANDS=[(0.80,0.90),(0.90,0.95),(0.95,0.97),(0.97,0.98),(0.98,0.99),(0.99,0.995),(0.995,1.0)]

def pull():
    out,seen=[],set()
    def grab(off):
        try:
            r=S.get(f"{G}/markets",params={"closed":"false","active":"true","limit":100,
                "offset":off,"order":"volume24hr","ascending":"false"},timeout=25)
            return r.json() if r.ok else []
        except Exception: return []
    with ThreadPoolExecutor(max_workers=20) as ex:
        for pg in ex.map(grab,[i*100 for i in range(20)]):
            for m in pg:
                if m.get("conditionId") in seen: continue
                seen.add(m.get("conditionId")); out.append(m)
    return out

def probe(m):
    try:
        t=json.loads(m["clobTokenIds"]); o=json.loads(m["outcomes"])
        if len(t)!=2: return None
        res=[]
        for i in (0,1):   # BOTH sides: a 0.03 YES is a 0.97 NO, and NO is tradeable too
            bk=S.get(f"{C}/book",params={"token_id":t[i]},timeout=10).json()
            asks=[(float(x["price"]),float(x["size"])) for x in bk.get("asks",[])]
            bids=[(float(x["price"]),float(x["size"])) for x in bk.get("bids",[])]
            if not asks or not bids: continue
            a=min(asks); b=max(bids); mid=(a[0]+b[0])/2
            # dollars available lifting the offer up to 1c above best ask
            depth=sum(p*s for p,s in asks if p<=a[0]+0.01)
            res.append({"mid":mid,"ask":a[0],"bid":b[0],"half":a[0]-mid,
                        "asksz":a[1],"depth1c":depth,"slug":m.get("slug","?")})
        return res
    except Exception: return None

ms=pull(); print(f"{len(ms)} active markets pulled",flush=True)
rows=[]
with ThreadPoolExecutor(max_workers=30) as ex:
    for r in ex.map(probe,ms):
        if r: rows.extend(r)
print(f"{len(rows)} one-sided book observations\n")
print(f"{'band':>13} {'n':>5} {'med half-spr':>13} {'p75 half':>10} {'med ask sz':>11} {'med $ @<=1c':>12} {'tot $ avail':>13}")
for lo,hi in BANDS:
    g=[r for r in rows if lo<=r["mid"]<hi]
    if len(g)<5: continue
    print(f"  {lo:.3f}-{hi:<6.3f} {len(g):>5} {statistics.median(r['half'] for r in g):>13.5f} "
          f"{sorted(r['half'] for r in g)[int(len(g)*0.75)]:>10.5f} "
          f"{statistics.median(r['asksz'] for r in g):>11.0f} "
          f"{statistics.median(r['depth1c'] for r in g):>12,.0f} "
          f"{sum(r['depth1c'] for r in g):>13,.0f}")
json.dump(rows,open("/tmp/claude-0/-home-user-S3/8087631e-229b-5943-8560-8dda6ec827f4/scratchpad/spreads.json","w"))
