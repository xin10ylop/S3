"""Crypto barrier calibration, anchored at market OPEN.

The T-7d-before-resolution anchor was contaminated: 27.4% of winners resolve
early when the barrier is touched, 0.0% of losers do, so the sampling instant
was a function of the answer.

Anchoring on the FIRST 3 days of each market's tape removes it. Nothing has
resolved yet, so winners and losers are sampled identically, and "buy or sell at
open, hold to resolution" is a strategy you could actually run.

data-api serves newest-first with no ordering param, so the oldest fills are
reached by binary-searching the offset.
"""
import json, sys, time, random, statistics
from collections import defaultdict
import requests
from concurrent.futures import ThreadPoolExecutor
S=requests.Session(); S.mount("https://",requests.adapters.HTTPAdapter(pool_maxsize=80,max_retries=3))
D="https://data-api.polymarket.com/trades"
SCRATCH="/tmp/claude-0/-home-user-S3/8087631e-229b-5943-8560-8dda6ec827f4/scratchpad"
WIN=float(sys.argv[1]) if len(sys.argv)>1 else 3.0
BANDS=[(0.00,0.02),(0.02,0.05),(0.05,0.10),(0.10,0.20),(0.20,0.35),(0.35,0.50),(0.50,0.70),(0.70,1.0)]
import re
def meta(s):
    a=next((x for x in ("bitcoin","ethereum","solana","xrp","dogecoin") if x in s),"other")
    d="down" if re.search(r"(dip-to|below)",s) else "up"
    mo=re.search(r"(january|february|march|april|may|june|july|august|september|october|november|december)",s)
    yr=re.search(r"(20\d\d)",s)
    return a,d,f"{a}|{mo.group(1) if mo else '?'}|{yr.group(1) if yr else '?'}"
def page(cond,off,lim=500):
    try:
        r=S.get(D,params={"market":cond,"limit":lim,"offset":off},timeout=25)
        j=r.json() if r.ok else []
        return j if isinstance(j,list) else []
    except Exception: return []
def tail_offset(cond):
    lo,hi=0,500
    while page(cond,hi,1):
        lo=hi; hi*=2
        if hi>60000: break
    while lo+250<hi:
        mid=(lo+hi)//2
        if page(cond,mid,1): lo=mid
        else: hi=mid
    return lo
def anchor(m):
    try:
        outs=json.loads(m["outcomes"]); pr=json.loads(m["outcomePrices"])
        won=1 if pr[outs.index("Yes")]=="1" else 0
    except Exception: return None
    cond=m["conditionId"]
    off=tail_offset(cond)
    rows=[]
    for o in (max(0,off-500),max(0,off-1000)):
        rows+=page(cond,o,500)
    if not rows: return None
    t0=min(t.get("timestamp",0) for t in rows)
    num=den=0.0; n=0
    for t in rows:
        ts=t.get("timestamp",0)
        if ts>t0+WIN*86400: continue
        try: p=float(t["price"]); sz=float(t["size"])
        except Exception: continue
        if not (0<p<1): continue
        py=p if t.get("outcome")=="Yes" else 1-p
        num+=py*sz; den+=sz; n+=1
    if den<=0 or n<5: return None
    a,d,c=meta(m["slug"])
    return {"slug":m["slug"],"p":num/den,"won":won,"n":n,"asset":a,"dir":d,"clus":c,
            "vol":float(m.get("volume") or 0),"t0":t0}
def boot(items,n=4000,seed=23):
    by=defaultdict(list)
    for x in items: by[x["clus"]].append(x)
    ks=list(by)
    if len(ks)<4: return (float("nan"),)*2
    rng=random.Random(seed); tot=len(items); out=[]
    for _ in range(n):
        w=k=0.0; c=0
        while c<tot:
            g=by[ks[rng.randrange(len(ks))]]
            for x in g: w+=x["p"]; k+=x["won"]
            c+=len(g)
        out.append((k-w)/max(c,1)*100)
    out.sort(); return out[int(.025*n)],out[int(.975*n)]
t0=time.time()
ms=[m for m in json.load(open(f"{SCRATCH}/cryptothresh.json")) if float(m.get("volume") or 0)>=2000]
print(f"{len(ms)} resolved crypto barrier markets; anchoring on first {WIN:.0f} days of each tape",flush=True)
got=[]
with ThreadPoolExecutor(max_workers=32) as ex:
    for i,r in enumerate(ex.map(anchor,ms)):
        if r: got.append(r)
        if (i+1)%300==0: print(f"   {i+1}/{len(ms)} anchored {len(got)} ({time.time()-t0:.0f}s)",flush=True)
json.dump(got,open(f"{SCRATCH}/barrier_open.json","w"))
print(f"\nanchored {len(got)} markets ({len(got)/len(ms):.1%} coverage) in {time.time()-t0:.0f}s")
print(f"clusters: {len({x['clus'] for x in got})}   base rate {sum(x['won'] for x in got)/len(got):.4f} "
      f"vs mean open price {sum(x['p'] for x in got)/len(got):.4f}\n")
print("CALIBRATION AT OPEN  (negative edge = market OVERprices => selling is +EV)")
print(f"{'band':>13} {'mkts':>6} {'clus':>5} {'mean px':>8} {'realised':>9} {'edge pp':>9} {'95% CI (cluster)':>22} {'sell ROI':>9}")
for lo,hi in BANDS:
    g=[x for x in got if lo<=x["p"]<hi]
    if len(g)<8: continue
    mp=sum(x["p"] for x in g)/len(g); fr=sum(x["won"] for x in g)/len(g)
    l,h=boot(g); roi=((1-fr)-(1-mp))/(1-mp)*100 if mp<1 else float("nan")
    print(f"  {lo:.2f}-{hi:<7.2f} {len(g):>6} {len({x['clus'] for x in g}):>5} {mp:>8.4f} {fr:>9.4f} "
          f"{(fr-mp)*100:>+9.2f} [{l:>+8.2f},{h:>+8.2f}] {roi:>+8.2f}%")
for key,lbl in (("dir","DIRECTION"),("asset","ASSET")):
    print(f"\nBY {lbl}")
    for v in sorted({x[key] for x in got}):
        g=[x for x in got if x[key]==v]
        if len(g)<10: continue
        mp=sum(x["p"] for x in g)/len(g); fr=sum(x["won"] for x in g)/len(g); l,h=boot(g)
        print(f"  {v:>11} n={len(g):>4} clus={len({x['clus'] for x in g}):>3} px {mp:>7.4f} "
              f"real {fr:>7.4f} edge {(fr-mp)*100:>+7.2f}pp [{l:>+7.2f},{h:>+7.2f}]")
print("\nVOLUME STRATIFICATION")
for mv in (2e3,1e4,5e4,2e5,1e6):
    g=[x for x in got if x["vol"]>=mv]
    if len(g)<10: continue
    mp=sum(x["p"] for x in g)/len(g); fr=sum(x["won"] for x in g)/len(g)
    print(f"  vol>=${mv:>10,.0f} n={len(g):>4} px {mp:>7.4f} real {fr:>7.4f} edge {(fr-mp)*100:>+7.2f}pp")
print(f"\ndone in {time.time()-t0:.0f}s")
