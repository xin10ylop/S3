"""VERIFY: the post-close settlement trade.

An anomaly scan claimed that btc-updown-5m markets keep trading for ~60s AFTER
their nominal close (dt>=300), by which time the outcome is already determined,
and that the eventual winner still prints around 0.99 with a ~100% win rate.

If true, this is a settlement-lag arbitrage: buy the converged side after close,
redeem at $1.00. This script tests it adversarially on real data:

  1. Does trading really continue past close, and with what size?
  2. If we buy the side trading >=0.95 in [close+lo, close+hi], how often is it
     the actual winner? (This is the ONLY thing that matters - no oracle needed,
     we let the market's own convergence pick the side.)
  3. Net EV after the taker fee (fee = 0.07*p*(1-p) per share).
  4. Was the trade actually AVAILABLE to a buyer? Count taker-BUY prints only
     (side=="BUY" means a taker lifted an ask - exactly what we would do).
  5. Capacity: shares/dollars available per window.
  6. Failure tail: how often does the >=0.95 side LOSE (that costs ~99c).
"""
import sys, json, time, math
import requests
from concurrent.futures import ThreadPoolExecutor

GAMMA = "https://gamma-api.polymarket.com/markets"
DATA = "https://data-api.polymarket.com/trades"
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 10
ASSET = sys.argv[2] if len(sys.argv) > 2 else "btc"
DUR = 300
LO, HI = 5, 45          # seconds after nominal close
MINPX = 0.95            # only consider a side the market has converged on

S = requests.Session()
S.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=64, max_retries=2))


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    s = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - s) / d, (c + s) / d)


def one(wts):
    slug = f"{ASSET}-updown-5m-{wts}"
    try:
        r = S.get(GAMMA, params={"slug": slug, "closed": "true"}, timeout=12)
        ms = r.json() if r.ok else []
        if not ms:
            return None
        m = ms[0]
        prices = json.loads(m.get("outcomePrices", "[]"))
        outs = json.loads(m.get("outcomes", "[]"))
        if not prices or set(prices) != {"1", "0"}:
            return None
        winner = outs[prices.index("1")]
        cond = m["conditionId"]
        rows = []
        for off in range(0, 8 * 500, 500):
            rr = S.get(DATA, params={"market": cond, "limit": 500, "offset": off}, timeout=15)
            if not rr.ok:
                break
            pg = rr.json()
            rows.extend(pg)
            if len(pg) < 500:
                break
        close = wts + DUR
        # prints strictly inside the post-close arb window
        seg = [t for t in rows if close + LO <= t.get("timestamp", 0) <= close + HI]
        if not seg:
            return {"wts": wts, "traded_post": 0}
        # candidate side = the outcome whose OWN price is >= MINPX on a taker BUY
        cands = []
        for t in seg:
            px = float(t["price"])
            if t.get("side") != "BUY":
                continue          # we must be the taker lifting an ask
            if px >= MINPX:
                cands.append((t.get("outcome"), px, float(t["size"]), t.get("timestamp")))
        if not cands:
            return {"wts": wts, "traded_post": 1, "n_cands": 0}
        sides = set(c[0] for c in cands)
        vol = sum(c[2] for c in cands)
        vwap = sum(c[1] * c[2] for c in cands) / vol
        side = cands[0][0]
        won = int(side == winner)
        # if the market printed >=0.95 BUYs on BOTH outcomes it is ambiguous
        ambiguous = int(len(sides) > 1)
        return {"wts": wts, "traded_post": 1, "n_cands": len(cands), "side": side,
                "winner": winner, "won": won, "vwap": vwap, "vol": vol,
                "ambiguous": ambiguous, "first_dt": cands[0][3] - close}
    except Exception:
        return None


def main():
    now = int(time.time())
    end = now - 1200 - (now % DUR)
    start = end - DAYS * 86400
    wtss = list(range(start, end, DUR))
    print(f"verifying post-close trade on {len(wtss)} {ASSET} windows ({DAYS}d), "
          f"window=[close+{LO}s, close+{HI}s], min price {MINPX}", flush=True)
    res = []
    with ThreadPoolExecutor(max_workers=40) as ex:
        for i, r in enumerate(ex.map(one, wtss)):
            if r:
                res.append(r)
            if (i + 1) % 500 == 0:
                print(f"  {i+1}/{len(wtss)}", flush=True)
    path = f"/tmp/claude-0/-home-user-S3/8087631e-229b-5943-8560-8dda6ec827f4/scratchpad/postclose_{ASSET}_{DAYS}d.json"
    json.dump(res, open(path, "w"))
    print(f"\nraw: {path}")

    resolved = [r for r in res if "traded_post" in r]
    post = [r for r in resolved if r.get("traded_post")]
    cand = [r for r in post if r.get("n_cands", 0) > 0]
    print(f"\nwindows resolved:            {len(resolved)}")
    print(f"  with ANY post-close print: {len(post)} ({len(post)/max(len(resolved),1)*100:.1f}%)")
    print(f"  with a taker-BUY >= {MINPX}:  {len(cand)} ({len(cand)/max(len(resolved),1)*100:.1f}%)")
    if not cand:
        print("NO TRADEABLE CANDIDATES — claim refuted.")
        return
    amb = sum(r["ambiguous"] for r in cand)
    print(f"  ambiguous (both sides >= {MINPX}): {amb} ({amb/len(cand)*100:.2f}%)")

    clean = [r for r in cand if not r["ambiguous"]]
    k = sum(r["won"] for r in clean)
    n = len(clean)
    wr = k / n
    lo, hi = wilson(k, n)
    vwap = sum(r["vwap"] * r["vol"] for r in clean) / sum(r["vol"] for r in clean)
    fee = 0.07 * vwap * (1 - vwap)
    gross = (wr - vwap) * 100
    net = gross - fee * 100
    print(f"\n=== THE TRADE (unambiguous windows, n={n}) ===")
    print(f"  win rate      : {wr*100:.3f}%  Wilson95 [{lo*100:.3f}, {hi*100:.3f}]")
    print(f"  losses        : {n-k} of {n}")
    print(f"  VWAP paid     : {vwap:.4f}")
    print(f"  taker fee     : {fee*100:.3f}c/share")
    print(f"  GROSS EV      : {gross:+.3f}c/share")
    print(f"  NET EV        : {net:+.3f}c/share")
    print(f"  worst-case EV using Wilson LOWER bound: {((lo - vwap)*100 - fee*100):+.3f}c/share")
    vols = sorted(r["vol"] for r in clean)
    print(f"\n  capacity/window (shares): p25={vols[len(vols)//4]:.0f} "
          f"med={vols[len(vols)//2]:.0f} p75={vols[3*len(vols)//4]:.0f}")
    print(f"  median $ available/window: ${vols[len(vols)//2]*vwap:.0f}")
    print(f"  trades/day available: {len(clean)/DAYS:.0f}")
    dts = sorted(r["first_dt"] for r in clean)
    print(f"  first qualifying print at close+ {dts[len(dts)//4]}s / {dts[len(dts)//2]}s / {dts[3*len(dts)//4]}s (p25/med/p75)")
    daily_shares = sum(r["vol"] for r in clean) / DAYS
    print(f"\n  if we captured 10% of available volume: "
          f"${daily_shares*0.10*net/100:.2f}/day gross of capital constraints")
    if n - k:
        print("\n  LOSING WINDOWS (the tail that matters):")
        for r in [x for x in clean if not x["won"]][:8]:
            print(f"    wts={r['wts']} bought {r['side']} @{r['vwap']:.4f} but {r['winner']} won "
                  f"(vol {r['vol']:.0f}, first at close+{r['first_dt']}s)")


if __name__ == "__main__":
    main()
