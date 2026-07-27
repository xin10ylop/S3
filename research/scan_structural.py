"""STRUCTURAL ARBITRAGE SCANNER — no prediction, guaranteed fills.

In a binary market the two tokens are complements: 1 Up + 1 Down always redeems
for exactly $1.00. They trade on SEPARATE order books. That creates two pure
arbitrages that need no forecast and no queue position, because we CROSS the
spread (taker) and are therefore always filled:

  A) BUY-BOTH  : up_ask + dn_ask < 1 - fees   -> buy both sides, redeem $1.00
  B) SELL-BOTH : up_bid + dn_bid > 1 + fees   -> mint a pair for $1, sell both

Taker fee per share = 0.07 * p * (1-p), which COLLAPSES at extreme prices
(0.07c at p=0.99 vs 1.75c at p=0.50). So dislocations near the extremes need
only a fraction of a cent to be profitable.

Also records, for the market-making idea, the live spread and depth per asset.

Usage: python3 scan_structural.py [minutes]
"""
import json, sys, time, statistics
import requests
from concurrent.futures import ThreadPoolExecutor

GAMMA = "https://gamma-api.polymarket.com/markets"
CLOB = "https://clob.polymarket.com"
ASSETS = ["btc", "eth", "sol", "xrp", "doge"]
MINUTES = int(sys.argv[1]) if len(sys.argv) > 1 else 20

S = requests.Session()
S.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=40, max_retries=2))


def fee(p):
    return 0.07 * p * (1 - p)


def book(tok):
    r = S.get(f"{CLOB}/book", params={"token_id": tok}, timeout=8)
    r.raise_for_status()
    b = r.json()
    bids = [(float(x["price"]), float(x["size"])) for x in b.get("bids", [])]
    asks = [(float(x["price"]), float(x["size"])) for x in b.get("asks", [])]
    return (max(bids) if bids else (None, 0)), (min(asks) if asks else (None, 0))


def snap(asset, wts, tag):
    try:
        ms = S.get(GAMMA, params={"slug": f"{asset}-updown-5m-{wts}"}, timeout=10).json()
        if not ms:
            return None
        toks = json.loads(ms[0]["clobTokenIds"])
        outs = json.loads(ms[0]["outcomes"])
        i_up = outs.index("Up")
        (ub, ubs), (ua, uas) = book(toks[i_up])
        (db, dbs), (da, das) = book(toks[1 - i_up])
    except Exception:
        return None
    r = {"asset": asset, "wts": wts, "tag": tag, "t": time.time(),
         "up_bid": ub, "up_ask": ua, "dn_bid": db, "dn_ask": da,
         "up_bid_sz": ubs, "up_ask_sz": uas, "dn_bid_sz": dbs, "dn_ask_sz": das}
    if ua is not None and da is not None:
        cost = ua + da
        f = fee(ua) + fee(da)
        r["buy_both_cost"] = cost
        r["buy_both_edge_c"] = (1.0 - cost - f) * 100          # cents per pair
        r["buy_both_sz"] = min(uas, das)
    if ub is not None and db is not None:
        proceeds = ub + db
        r["sell_both_proceeds"] = proceeds
        r["sell_both_edge_c"] = (proceeds - 1.0) * 100          # maker sells => no fee
        r["sell_both_sz"] = min(ubs, dbs)
    if ua is not None and ub is not None:
        r["up_spread"] = ua - ub
    return r


def main():
    end_at = time.time() + MINUTES * 60
    out = []
    print(f"structural scan, {MINUTES} min, assets={ASSETS}", flush=True)
    while time.time() < end_at:
        now = int(time.time())
        wts_live = now - (now % 300)          # window currently running
        rel = now - wts_live
        tag = ("open" if rel < 60 else "early" if rel < 150 else
               "late" if rel < 280 else "close")
        jobs = [(a, w, tg) for a in ASSETS
                for w, tg in ((wts_live, tag), (wts_live + 300, "pre-open"))]
        with ThreadPoolExecutor(max_workers=10) as ex:
            for r in ex.map(lambda j: snap(*j), jobs):
                if r:
                    out.append(r)
        n_arb = sum(1 for r in out if r.get("buy_both_edge_c", -9) > 0)
        print(f"  {len(out)} snapshots, {n_arb} buy-both arbs so far", flush=True)
        time.sleep(20)

    p = "/tmp/claude-0/-home-user-S3/8087631e-229b-5943-8560-8dda6ec827f4/scratchpad/structural.json"
    json.dump(out, open(p, "w"))
    print(f"\nsaved {len(out)} snapshots -> {p}\n")

    print("=== A) BUY-BOTH ARB (up_ask + dn_ask < 1 - fees) ===")
    ok = [r for r in out if "buy_both_edge_c" in r]
    pos = [r for r in ok if r["buy_both_edge_c"] > 0]
    print(f"  measurable snapshots: {len(ok)}   profitable: {len(pos)} "
          f"({len(pos)/max(len(ok),1)*100:.2f}%)")
    if ok:
        costs = sorted(r["buy_both_cost"] for r in ok)
        print(f"  up_ask+dn_ask: min={costs[0]:.4f} p05={costs[len(costs)//20]:.4f} "
              f"median={costs[len(costs)//2]:.4f}")
        edges = sorted((r["buy_both_edge_c"] for r in ok), reverse=True)
        print(f"  best edges (c/pair): {[round(e,3) for e in edges[:8]]}")
    if pos:
        tot = sum(min(r["buy_both_sz"], 1e9) * r["buy_both_edge_c"] / 100 for r in pos)
        print(f"  TOTAL $ available across profitable snapshots: ${tot:.2f}")
        for r in pos[:10]:
            print(f"    {r['asset']:>5} {r['tag']:<8} up_ask={r['up_ask']:.3f} "
                  f"dn_ask={r['dn_ask']:.3f} sum={r['buy_both_cost']:.4f} "
                  f"edge={r['buy_both_edge_c']:+.3f}c size={r['buy_both_sz']:.0f}")

    print("\n=== B) SELL-BOTH ARB (up_bid + dn_bid > 1) ===")
    ok2 = [r for r in out if "sell_both_edge_c" in r]
    pos2 = [r for r in ok2 if r["sell_both_edge_c"] > 0]
    print(f"  measurable: {len(ok2)}   profitable: {len(pos2)} "
          f"({len(pos2)/max(len(ok2),1)*100:.2f}%)")
    if ok2:
        pr = sorted((r["sell_both_proceeds"] for r in ok2), reverse=True)
        print(f"  up_bid+dn_bid: max={pr[0]:.4f} p95={pr[len(pr)//20]:.4f} "
              f"median={pr[len(pr)//2]:.4f}")
    for r in pos2[:10]:
        print(f"    {r['asset']:>5} {r['tag']:<8} up_bid={r['up_bid']:.3f} "
              f"dn_bid={r['dn_bid']:.3f} sum={r['sell_both_proceeds']:.4f} "
              f"edge={r['sell_both_edge_c']:+.3f}c size={r['sell_both_sz']:.0f}")

    print("\n=== C) SPREAD / DEPTH by asset and stage (market-making input) ===")
    print(f"{'asset':>6} {'stage':>9} {'n':>4} {'med_spread':>11} {'med_bidsz':>10} {'taker_fee@mid':>14}")
    for a in ASSETS:
        for tg in ["pre-open", "open", "early", "late", "close"]:
            g = [r for r in out if r["asset"] == a and r["tag"] == tg
                 and r.get("up_spread") is not None]
            if len(g) < 3:
                continue
            sp = statistics.median(r["up_spread"] for r in g)
            bs = statistics.median(r["up_bid_sz"] for r in g)
            mid = statistics.median((r["up_bid"] + r["up_ask"]) / 2 for r in g)
            print(f"{a:>6} {tg:>9} {len(g):>4} {sp:>11.4f} {bs:>10.0f} {fee(mid)*100:>13.2f}c")


if __name__ == "__main__":
    main()
