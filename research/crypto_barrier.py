"""ARE CRYPTO BARRIER LONGSHOTS OVERPRICED?

Polymarket runs a large "will BTC reach $X by DATE" family -- one-touch barrier
options, and the venue's biggest lottery-ticket factory ($31M of volume on
"bitcoin reach $1,000,000 by Dec 31 2025" alone).

Exp 109 located the maker edge at the price extremes: selling longshots earns
+30 c/$ of volume while buying favourites as a maker loses ~8 c/$. This tests
that on the one family where the tickets are large, liquid, and repeatedly
listed -- and where fair value is computable from Binance rather than forecast.

Prices come from the TAPE (real fills at real prices), not prices-history, which
is the MID and retains only ~9 days.

Two traps this is built to avoid, both of which already burned this project:
  * exp 101 -- thin markets whose "price" is a junk-book artefact. Handled by
    stratifying on volume and reporting the whole ladder, not one cut.
  * pseudo-replication -- BTC reach 130k / 150k / 200k for the same month are
    ONE bet wearing three hats. Clustered bootstrap on (asset, deadline month).

Usage: python3 crypto_barrier.py [horizon_days] [min_volume]
"""
import json, re, sys, time, math, random, statistics
from collections import defaultdict
import requests
from concurrent.futures import ThreadPoolExecutor

DATA = "https://data-api.polymarket.com/trades"
SCRATCH = "/tmp/claude-0/-home-user-S3/8087631e-229b-5943-8560-8dda6ec827f4/scratchpad"
HORIZ = float(sys.argv[1]) if len(sys.argv) > 1 else 7.0     # days before resolution
MINVOL = float(sys.argv[2]) if len(sys.argv) > 2 else 2000

S = requests.Session()
S.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=64, max_retries=3))

BANDS = [(0.00, 0.02), (0.02, 0.05), (0.05, 0.10), (0.10, 0.20), (0.20, 0.35),
         (0.35, 0.50), (0.50, 0.70), (0.70, 0.90), (0.90, 1.00)]


def parse_ts(v):
    if not v:
        return None
    v = str(v).replace("T", " ").replace("Z", "").split("+")[0].split(".")[0].strip()
    try:
        return time.mktime(time.strptime(v, "%Y-%m-%d %H:%M:%S")) - time.timezone
    except Exception:
        return None


def meta(m):
    """asset, direction, and the deadline cluster key."""
    s = m["slug"]
    asset = next((a for a in ("bitcoin", "ethereum", "solana", "xrp", "dogecoin")
                  if a in s), "other")
    down = bool(re.search(r"(dip-to|below)", s))
    # deadline month: 'by-december-31-2025', 'in-october', '-2026-07-'
    mo = re.search(r"(january|february|march|april|may|june|july|august|september|"
                   r"october|november|december)", s)
    yr = re.search(r"(20\d\d)", s)
    return asset, ("down" if down else "up"), f"{asset}|{mo.group(1) if mo else '?'}|{yr.group(1) if yr else '?'}"


def price_at(m):
    """Volume-weighted trade price in a window ending HORIZ days before
    resolution. Real fills only -- no mid, no book reconstruction."""
    res = parse_ts(m.get("closedTime")) or parse_ts(m.get("endDate"))
    if not res:
        return None
    hi = res - HORIZ * 86400
    lo = hi - 3 * 86400                       # a 3-day window ending at the horizon
    rows = []
    for off in range(0, 12 * 500, 500):
        try:
            r = S.get(DATA, params={"market": m["conditionId"], "limit": 500, "offset": off},
                      timeout=25)
            if not r.ok:
                break
            pg = r.json()
            if not isinstance(pg, list) or not pg:
                break
            rows.extend(pg)
            if len(pg) < 500 or min(t.get("timestamp", 0) for t in pg) < lo:
                break
        except Exception:
            break
    try:
        outs = json.loads(m["outcomes"]); pr = json.loads(m["outcomePrices"])
    except Exception:
        return None
    yes = outs[0] if outs[0] == "Yes" else outs[0]
    won = 1 if pr[outs.index("Yes")] == "1" else 0
    num = den = 0.0
    n = 0
    for t in rows:
        ts = t.get("timestamp", 0)
        if not (lo <= ts <= hi):
            continue
        try:
            p = float(t["price"]); sz = float(t["size"])
        except Exception:
            continue
        if not (0 < p < 1):
            continue
        # express every fill as a YES probability
        py = p if t.get("outcome") == "Yes" else 1 - p
        num += py * sz
        den += sz
        n += 1
    if den <= 0 or n < 5:
        return None
    a, d, clus = meta(m)
    return {"slug": m["slug"], "p": num / den, "won": won, "n": n, "sz": den,
            "asset": a, "dir": d, "clus": clus, "vol": float(m.get("volume") or 0)}


def boot(items, n=4000, seed=17):
    """Cluster bootstrap on (asset, deadline month): BTC reach 130k/150k/200k in
    one month is one bet, not three."""
    by = defaultdict(list)
    for x in items:
        by[x["clus"]].append(x)
    keys = list(by)
    if len(keys) < 4:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    tot = len(items)
    out = []
    for _ in range(n):
        w = k = 0.0
        c = 0
        while c < tot:
            g = by[keys[rng.randrange(len(keys))]]
            for x in g:
                w += x["p"]; k += x["won"]
            c += len(g)
        out.append((k - w) / max(c, 1) * 100)      # realised minus priced, in pp
    out.sort()
    return out[int(0.025 * n)], out[int(0.975 * n)]


def main():
    t0 = time.time()
    ms = json.load(open(f"{SCRATCH}/cryptothresh.json"))
    ms = [m for m in ms if float(m.get("volume") or 0) >= MINVOL]
    print(f"{len(ms)} resolved crypto threshold markets, vol>=${MINVOL:,.0f}", flush=True)
    print(f"pricing from the tape in a 3-day window ending T-{HORIZ:.0f}d\n", flush=True)

    got = []
    with ThreadPoolExecutor(max_workers=24) as ex:
        for i, r in enumerate(ex.map(price_at, ms)):
            if r:
                got.append(r)
            if (i + 1) % 300 == 0:
                print(f"   {i+1}/{len(ms)}  priced {len(got)}  ({time.time()-t0:.0f}s)", flush=True)
    json.dump(got, open(f"{SCRATCH}/barrier.json", "w"))
    print(f"\npriced {len(got)} markets in {time.time()-t0:.0f}s")
    if not got:
        return
    print(f"clusters (asset x deadline month): {len({x['clus'] for x in got})}")
    print(f"base rate: {sum(x['won'] for x in got)/len(got):.4f} "
          f"vs mean price {sum(x['p'] for x in got)/len(got):.4f}\n")

    print("CALIBRATION OF CRYPTO BARRIER MARKETS  (positive edge = market UNDERprices)")
    print(f"{'price band':>13} {'mkts':>6} {'clus':>5} {'mean px':>8} {'realised':>9} "
          f"{'edge pp':>9} {'95% CI (cluster)':>22} {'sell ROI':>9}")
    for lo, hi in BANDS:
        g = [x for x in got if lo <= x["p"] < hi]
        if len(g) < 8:
            continue
        mp = sum(x["p"] for x in g) / len(g)
        fr = sum(x["won"] for x in g) / len(g)
        l, h = boot(g)
        # selling = buying NO at (1-p); cost 1-p, pays 1 if it never touches
        roi = ((1 - fr) - (1 - mp)) / (1 - mp) * 100 if mp < 1 else float("nan")
        print(f"  {lo:.2f}-{hi:<7.2f} {len(g):>6} {len({x['clus'] for x in g}):>5} "
              f"{mp:>8.4f} {fr:>9.4f} {(fr-mp)*100:>+9.2f} [{l:>+8.2f},{h:>+8.2f}] "
              f"{roi:>+8.2f}%")

    for key, lbl in (("dir", "DIRECTION"), ("asset", "ASSET")):
        print(f"\nBY {lbl}")
        print(f"{'':>13} {'mkts':>6} {'clus':>5} {'mean px':>8} {'realised':>9} "
              f"{'edge pp':>9} {'95% CI (cluster)':>22}")
        for v in sorted({x[key] for x in got}):
            g = [x for x in got if x[key] == v]
            if len(g) < 10:
                continue
            mp = sum(x["p"] for x in g) / len(g); fr = sum(x["won"] for x in g) / len(g)
            l, h = boot(g)
            print(f"  {v:>11} {len(g):>6} {len({x['clus'] for x in g}):>5} {mp:>8.4f} "
                  f"{fr:>9.4f} {(fr-mp)*100:>+9.2f} [{l:>+8.2f},{h:>+8.2f}]")

    print("\nVOLUME STRATIFICATION (exp 101's trap: thin markets fake edges)")
    print(f"{'min vol':>13} {'mkts':>6} {'clus':>5} {'mean px':>8} {'realised':>9} {'edge pp':>9}")
    for mv in (2e3, 1e4, 5e4, 2e5, 1e6):
        g = [x for x in got if x["vol"] >= mv]
        if len(g) < 10:
            continue
        mp = sum(x["p"] for x in g) / len(g); fr = sum(x["won"] for x in g) / len(g)
        print(f"  {mv:>11,.0f} {len(g):>6} {len({x['clus'] for x in g}):>5} {mp:>8.4f} "
              f"{fr:>9.4f} {(fr-mp)*100:>+9.2f}")
    print(f"\ndone in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
