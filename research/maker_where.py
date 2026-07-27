"""WHERE should a maker quote? Maker edge by price band and time-to-resolution.

Exp 107 established that outside live in-game sports, makers win 66.4% of
markets at +0.175 c/$ traded. Takers execute against the BEST price, so those
fills are BBO fills -- that number already IS the edge of quoting at the top of
book. What it does not say is which prices and which horizons carry it.

Same exact arithmetic as maker_pnl.py, but per-trade detail is retained:
    taker BUY  O at p -> maker P&L = p - 1{O}
    taker SELL O at p -> maker P&L = 1{O} - p
"""
import json, re, sys, time, random, statistics
from collections import defaultdict
import requests
from concurrent.futures import ThreadPoolExecutor
S=requests.Session(); S.mount("https://",requests.adapters.HTTPAdapter(pool_maxsize=64,max_retries=3))
G="https://gamma-api.polymarket.com"; D="https://data-api.polymarket.com/trades"
NM=int(sys.argv[1]) if len(sys.argv)>1 else 600
LEAGUE=re.compile(r"^(fifwc|fifa|mlb|nba|nfl|nhl|atp|wta|ucl|uel|epl|lal|seri|bund|lig1|mls|cs2|lol|val|dota|ufc|box|f1|nascar|wnba|ncaa|kbo|npb|cfb)\b")
DATED=re.compile(r"\d{4}-\d{2}-\d{2}"); PROP=re.compile(r"(spread|total|moneyline|game\d|-ml-|handicap|pt5)")
def is_sport(s): return bool(LEAGUE.match(s) or (DATED.search(s) and PROP.search(s)))
def parse_ts(v):
    if not v: return None
    v=str(v).replace("T"," ").replace("Z","").split("+")[0].split(".")[0].strip()
    try: return time.mktime(time.strptime(v,"%Y-%m-%d %H:%M:%S"))-time.timezone
    except Exception: return None
def enum(n):
    now=time.time(); edges=[now-d*86400 for d in range(0,46,3)]
    def grab(j):
        lo,hi,off=j
        try:
            r=S.get(f"{G}/markets",params={"closed":"true","limit":100,"offset":off,
              "end_date_min":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime(lo)),
              "end_date_max":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime(hi))},timeout=30)
            return r.json() if r.ok else []
        except Exception: return []
    jobs=[(edges[i+1],edges[i],o) for i in range(len(edges)-1) for o in range(0,1200,100)]
    out,seen=[],set()
    with ThreadPoolExecutor(max_workers=24) as ex:
        for pg in ex.map(grab,jobs):
            for m in pg:
                c=m.get("conditionId")
                if not c or c in seen: continue
                try:
                    pr=json.loads(m.get("outcomePrices") or "[]"); o=json.loads(m.get("outcomes") or "[]")
                    if sorted(pr)!=["0","1"] or len(o)!=2: continue
                    if float(m.get("volume") or 0)<5000: continue
                    if is_sport(m.get("slug") or ""): continue
                    ts=parse_ts(m.get("closedTime")) or parse_ts(m.get("umaEndDate"))
                    if not ts: continue
                except Exception: continue
                seen.add(c)
                out.append({"cond":c,"slug":m["slug"],"winner":o[pr.index("1")],
                            "vol":float(m.get("volume") or 0),"ts":ts})
    out.sort(key=lambda x:-x["vol"]); return out[:n]
def tape(m):
    rows=[]
    for off in range(0,8*500,500):
        try:
            r=S.get(D,params={"market":m["cond"],"limit":500,"offset":off},timeout=25)
            if not r.ok: break
            pg=r.json()
            if not isinstance(pg,list) or not pg: break
            rows.extend(pg)
            if len(pg)<500: break
        except Exception: break
    out=[]
    for t in rows:
        try:
            p=float(t["price"]); s=float(t["size"])
            if not (0<p<1): continue
            won=1.0 if t.get("outcome")==m["winner"] else 0.0
            pnl=s*(p-won) if t.get("side")=="BUY" else s*(won-p)
            out.append((p,s,pnl,(m["ts"]-t.get("timestamp",m["ts"]))/3600.0,m["slug"]))
        except Exception: continue
    return out
t0=time.time(); ms=enum(NM)
print(f"{len(ms)} resolved NON-SPORTS markets, vol>=$5k, 45d ({time.time()-t0:.0f}s)",flush=True)
tr=[]
with ThreadPoolExecutor(max_workers=24) as ex:
    for i,r in enumerate(ex.map(tape,ms)):
        tr.extend(r)
        if (i+1)%200==0: print(f"   {i+1}/{len(ms)}  {len(tr):,} trades ({time.time()-t0:.0f}s)",flush=True)
print(f"\n{len(tr):,} trades\n")
def boot(v,n=2000,seed=9):
    if len(v)<20: return (float('nan'),)*2
    rng=random.Random(seed); o=[]
    for _ in range(n):
        s=w=0.0
        for _ in range(min(len(v),4000)):
            pnl,vol=v[rng.randrange(len(v))]; s+=pnl; w+=vol
        o.append(s/w*100 if w else 0)
    o.sort(); return o[int(.025*n)],o[int(.975*n)]
BANDS=[(0.0,0.05),(0.05,0.15),(0.15,0.30),(0.30,0.50),(0.50,0.70),(0.70,0.85),(0.85,0.95),(0.95,1.0)]
print("MAKER EDGE BY PRICE OF THE FILL  (bootstrap clustered on trades)")
print(f"{'band':>13} {'trades':>9} {'volume $':>14} {'maker P&L':>12} {'c/$':>8} {'95% CI':>20}")
for lo,hi in BANDS:
    g=[(x[2],x[0]*x[1]) for x in tr if lo<=x[0]<hi]
    if len(g)<50: continue
    v=sum(b for _,b in g); p=sum(a for a,_ in g)
    l,h=boot(g)
    print(f"  {lo:.2f}-{hi:<7.2f} {len(g):>9,} {v:>14,.0f} {p:>12,.0f} {p/v*100:>+8.3f} [{l:>+7.2f},{h:>+7.2f}]")
print("\nMAKER EDGE BY HOURS BEFORE RESOLUTION")
HH=[(0,6),(6,24),(24,72),(72,168),(168,720),(720,1e9)]
print(f"{'hours out':>13} {'trades':>9} {'volume $':>14} {'maker P&L':>12} {'c/$':>8} {'95% CI':>20}")
for lo,hi in HH:
    g=[(x[2],x[0]*x[1]) for x in tr if lo<=x[3]<hi]
    if len(g)<50: continue
    v=sum(b for _,b in g); p=sum(a for a,_ in g)
    l,h=boot(g)
    lbl=f"{lo}-{int(hi) if hi<1e8 else 'inf'}h"
    print(f"  {lbl:>11} {len(g):>9,} {v:>14,.0f} {p:>12,.0f} {p/v*100:>+8.3f} [{l:>+7.2f},{h:>+7.2f}]")
