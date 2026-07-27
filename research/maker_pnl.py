"""HOW BIG IS THE MAKER POT, AND WHO IS PAYING IT?

data-api/trades reports every fill from the TAKER's side. Every trade has a
maker on the other side, so the maker's position is the exact mirror:

    taker BUY  outcome O at p  ->  maker SOLD   O at p  ->  maker P&L = p - 1{O}
    taker SELL outcome O at p  ->  maker BOUGHT O at p  ->  maker P&L = 1{O} - p

Because we know how each market resolved, aggregate maker gross P&L is not
estimated, it is computed. Makers pay no fee, so this is their gross edge before
liquidity rewards -- the entire pot every market maker on the venue is splitting.

This is the question underneath every market-making idea we have tested: not
"can I quote well?" but "is there anything to win?". If the pot is negative in a
category, no amount of quoting skill fixes it. If positive, the reward subsidy
sits on top of it.

Reported per category and per reward-status, normalised by volume so the number
is an edge in cents per dollar traded, not a headline.

Usage: python3 maker_pnl.py [n_markets] [days_back] [workers]
"""
import json, sys, time, statistics
from collections import defaultdict
import requests
from concurrent.futures import ThreadPoolExecutor

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA = "https://data-api.polymarket.com/trades"
SCRATCH = "/tmp/claude-0/-home-user-S3/8087631e-229b-5943-8560-8dda6ec827f4/scratchpad"

NM = int(sys.argv[1]) if len(sys.argv) > 1 else 1500
DAYS = int(sys.argv[2]) if len(sys.argv) > 2 else 45
WORKERS = int(sys.argv[3]) if len(sys.argv) > 3 else 24
MAXPAGES = 8

S = requests.Session()
S.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=WORKERS * 2, max_retries=3))

CATS = [("sports", ("nba", "mlb", "nfl", "nhl", "atp", "wta", "cs2", "lol", "ufc",
                    "epl", "laliga", "seriea", "mls", "f1", "game1", "game2")),
        ("weather", ("temperature", "rain", "snow", "hurricane")),
        ("crypto", ("bitcoin", "ethereum", "solana", "xrp", "doge", "btc", "eth")),
        ("econ", ("fed", "inflation", "cpi", "gdp", "rate-cut", "rate-hike", "jobs")),
        ("geo", ("iran", "israel", "ukraine", "russia", "gaza", "ceasefire", "nato",
                 "taiwan", "venezuela", "maduro")),
        ("politics", ("election", "president", "senate", "house", "governor", "nominee")),
        ("stocks", ("nvda", "tsla", "aapl", "spx", "sp500", "nasdaq", "hood")),
        ("culture", ("mrbeast", "movie", "oscar", "grammy", "album", "tweet"))]


def category(slug):
    for name, keys in CATS:
        if any(k in slug for k in keys):
            return name
    return "other"


def enumerate_resolved(n, days):
    now = time.time()
    edges = [now - d * 86400 for d in range(0, days + 1, 3)]
    slices = [(edges[i + 1], edges[i]) for i in range(len(edges) - 1)]

    def grab(job):
        lo, hi, off = job
        try:
            r = S.get(f"{GAMMA}/markets",
                      params={"closed": "true", "limit": 100, "offset": off,
                              "end_date_min": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(lo)),
                              "end_date_max": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(hi))},
                      timeout=30)
            return r.json() if r.ok else []
        except Exception:
            return []

    jobs = [(lo, hi, off) for lo, hi in slices for off in range(0, 1200, 100)]
    out, seen = [], set()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for pg in ex.map(grab, jobs):
            for m in pg:
                c = m.get("conditionId")
                if not c or c in seen:
                    continue
                try:
                    pr = json.loads(m.get("outcomePrices") or "[]")
                    outs = json.loads(m.get("outcomes") or "[]")
                    if sorted(pr) != ["0", "1"] or len(outs) != 2:
                        continue
                    if float(m.get("volume") or 0) < 5000:
                        continue
                except Exception:
                    continue
                seen.add(c)
                out.append({"cond": c, "slug": m.get("slug") or "",
                            "winner": outs[pr.index("1")],
                            "vol": float(m.get("volume") or 0),
                            "rewards": bool(m.get("clobRewards"))})
    out.sort(key=lambda x: -x["vol"])
    return out[:n]


def tape(m):
    rows = []
    for off in range(0, MAXPAGES * 500, 500):
        try:
            r = S.get(DATA, params={"market": m["cond"], "limit": 500, "offset": off}, timeout=25)
            if not r.ok:
                break
            pg = r.json()
            if not isinstance(pg, list) or not pg:
                break
            rows.extend(pg)
            if len(pg) < 500:
                break
        except Exception:
            break
    if not rows:
        return None
    mk_pnl = 0.0     # maker gross P&L, in dollars
    vol = 0.0
    n_buy = n_sell = 0
    for t in rows:
        try:
            p = float(t["price"]); s = float(t["size"])
            won = 1.0 if t.get("outcome") == m["winner"] else 0.0
            if t.get("side") == "BUY":       # taker bought -> maker sold
                mk_pnl += s * (p - won)
                n_buy += 1
            else:                            # taker sold  -> maker bought
                mk_pnl += s * (won - p)
                n_sell += 1
            vol += s * p
        except Exception:
            continue
    if vol <= 0:
        return None
    return {"slug": m["slug"], "cat": category(m["slug"]), "rewards": m["rewards"],
            "vol": vol, "mk_pnl": mk_pnl, "n": len(rows),
            "capped": len(rows) >= MAXPAGES * 500,
            "edge_c": mk_pnl / vol * 100, "n_buy": n_buy, "n_sell": n_sell}


def boot(vals, n=2000, seed=11):
    import random
    if len(vals) < 5:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        s = w = 0.0
        for _ in range(len(vals)):
            pnl, vol = vals[rng.randrange(len(vals))]
            s += pnl; w += vol
        out.append(s / w * 100 if w else 0.0)
    out.sort()
    return (out[int(0.025 * n)], out[int(0.975 * n)])


def main():
    t0 = time.time()
    ms = enumerate_resolved(NM, DAYS)
    print(f"{len(ms)} resolved binary markets, vol>=$5k, last {DAYS}d "
          f"({time.time()-t0:.0f}s)", flush=True)
    res = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for i, r in enumerate(ex.map(tape, ms)):
            if r:
                res.append(r)
            if (i + 1) % 250 == 0:
                print(f"   {i+1}/{len(ms)}  kept {len(res)}  ({time.time()-t0:.0f}s)", flush=True)
    json.dump(res, open(f"{SCRATCH}/maker_pnl.json", "w"))
    capped = sum(r["capped"] for r in res)
    print(f"\n{len(res)} markets with a tape; {capped} hit the {MAXPAGES*500}-trade cap "
          f"(their tails are missing -- treat those as a lower bound on volume)\n")

    tot_v = sum(r["vol"] for r in res)
    tot_p = sum(r["mk_pnl"] for r in res)
    lo, hi = boot([(r["mk_pnl"], r["vol"]) for r in res])
    print(f"AGGREGATE MAKER GROSS P&L: ${tot_p:,.0f} on ${tot_v:,.0f} of taker volume")
    print(f"  = {tot_p/tot_v*100:+.3f} cents per dollar traded   "
          f"95% CI [{lo:+.3f}, {hi:+.3f}]  (bootstrap over markets)\n")

    print(f"{'category':>10} {'mkts':>6} {'volume $':>14} {'maker P&L $':>13} "
          f"{'c/$ traded':>11} {'95% CI':>22}")
    for cat in sorted({r["cat"] for r in res}):
        g = [r for r in res if r["cat"] == cat]
        v = sum(r["vol"] for r in g); p = sum(r["mk_pnl"] for r in g)
        l, h = boot([(r["mk_pnl"], r["vol"]) for r in g])
        print(f"{cat:>10} {len(g):>6} {v:>14,.0f} {p:>13,.0f} {p/v*100:>+11.3f} "
              f"[{l:>+8.3f},{h:>+8.3f}]")

    print(f"\n{'rewards':>10} {'mkts':>6} {'volume $':>14} {'maker P&L $':>13} {'c/$ traded':>11}")
    for rw in (True, False):
        g = [r for r in res if r["rewards"] == rw]
        if not g:
            continue
        v = sum(r["vol"] for r in g); p = sum(r["mk_pnl"] for r in g)
        print(f"{str(rw):>10} {len(g):>6} {v:>14,.0f} {p:>13,.0f} {p/v*100:>+11.3f}")

    print(f"\ndone in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
