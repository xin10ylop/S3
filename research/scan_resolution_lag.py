"""SETTLEMENT-LAG PREMIUM on SLOW markets — the uncontested version.

On btc-updown-5m the post-close premium was real (+1.0c/share at 100% win) but
unreachable: ~$370k already rests at the top tick because the capital is locked
for only ~30 seconds, which is perfect for HFT.

Invert that. On markets that resolve in HOURS or DAYS, the same "outcome is
effectively known but the market still trades below $1.00" premium should exist
AND be far less contested, because tying capital up for a day is unattractive to
a latency bot but perfectly fine for us.

Method: take recently RESOLVED markets, replay their tape, and ask —
  for trades at price >= P on the side that actually won, in the final H hours,
  what return would a buyer have earned, and how much size was available?

Reports annualised return, capital lockup, and available dollars, so the result
is directly comparable to the crypto trade.

Usage: python3 scan_resolution_lag.py [n_markets]
"""
import json, sys, time, math, statistics
from collections import defaultdict
import requests
from concurrent.futures import ThreadPoolExecutor

GAMMA = "https://gamma-api.polymarket.com/markets"
DATA = "https://data-api.polymarket.com/trades"
NM = int(sys.argv[1]) if len(sys.argv) > 1 else 400
BANDS = [0.90, 0.95, 0.97, 0.99]

S = requests.Session()
S.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=64, max_retries=2))


def fee(p):
    return 0.07 * p * (1 - p)


def recent_resolved(n):
    out, off = [], 0
    while len(out) < n * 4 and off < 6000:
        try:
            r = S.get(GAMMA, params={"closed": "true", "limit": 500, "offset": off,
                                     "order": "endDate", "ascending": "false"}, timeout=25)
            if not r.ok:
                break
            page = r.json()
        except Exception:
            break
        if not page:
            break
        out.extend(page)
        off += 500
        if len(page) < 500:
            break
    keep = []
    for m in out:
        try:
            pr = json.loads(m.get("outcomePrices") or "[]")
            if set(pr) != {"1", "0"}:
                continue
            if float(m.get("volume") or 0) < 500:
                continue
            # skip the crypto up/down families we already mapped
            if "updown" in (m.get("slug") or "") or "up-or-down" in (m.get("slug") or ""):
                continue
            keep.append(m)
        except Exception:
            continue
    return keep[:n]


def analyse(m):
    try:
        pr = json.loads(m["outcomePrices"])
        outs = json.loads(m["outcomes"])
        winner = outs[pr.index("1")]
        cond = m["conditionId"]
        end_ms = m.get("endDate")
        # resolution timestamp: prefer closedTime, else endDate
        ts_end = None
        for k in ("closedTime", "endDate"):
            v = m.get(k)
            if v:
                try:
                    ts_end = time.mktime(time.strptime(v[:19], "%Y-%m-%dT%H:%M:%S"))
                    break
                except Exception:
                    pass
        if not ts_end:
            return None
        rows = []
        for off in range(0, 6 * 500, 500):
            r = S.get(DATA, params={"market": cond, "limit": 500, "offset": off}, timeout=15)
            if not r.ok:
                break
            pg = r.json()
            rows.extend(pg)
            if len(pg) < 500:
                break
        if not rows:
            return None
        res = {"slug": m.get("slug", "?"), "winner": winner, "ts_end": ts_end,
               "vol": float(m.get("volume") or 0), "n_trades": len(rows)}
        for band in BANDS:
            # taker BUYs of the WINNING outcome at >= band, i.e. we could have lifted an offer
            qual = [t for t in rows
                    if t.get("outcome") == winner and t.get("side") == "BUY"
                    and band <= float(t["price"]) < 0.999
                    and t.get("timestamp", 0) <= ts_end]
            if not qual:
                continue
            sz = sum(float(t["size"]) for t in qual)
            vwap = sum(float(t["price"]) * float(t["size"]) for t in qual) / sz
            hold_h = statistics.median((ts_end - t["timestamp"]) / 3600 for t in qual)
            net = (1.0 - vwap - fee(vwap))
            res[f"b{band}"] = {"n": len(qual), "sz": sz, "vwap": vwap,
                               "hold_h": hold_h, "net_c": net * 100,
                               "usd": sz * vwap}
        return res
    except Exception:
        return None


def main():
    t0 = time.time()
    ms = recent_resolved(NM)
    print(f"{len(ms)} recently-resolved non-crypto binary markets with volume", flush=True)
    res = []
    with ThreadPoolExecutor(max_workers=30) as ex:
        for i, r in enumerate(ex.map(analyse, ms)):
            if r:
                res.append(r)
            if (i + 1) % 100 == 0:
                print(f"  {i+1}/{len(ms)}  ({time.time()-t0:.0f}s)", flush=True)
    json.dump(res, open("/tmp/claude-0/-home-user-S3/8087631e-229b-5943-8560-8dda6ec827f4/scratchpad/reslag.json", "w"))
    print(f"\nanalysed {len(res)} markets\n")

    print(f"{'band':>6} {'mkts':>5} {'trades':>7} {'vwap':>7} {'net c/sh':>9} "
          f"{'med hold':>9} {'ann.%':>9} {'$ avail':>12}")
    for band in BANDS:
        g = [r[f"b{band}"] for r in res if f"b{band}" in r]
        if not g:
            continue
        tot_sz = sum(x["sz"] for x in g)
        vwap = sum(x["vwap"] * x["sz"] for x in g) / tot_sz
        net = sum(x["net_c"] * x["sz"] for x in g) / tot_sz
        hold = statistics.median(x["hold_h"] for x in g)
        ann = ((1 + net / 100 / vwap) ** (8760 / max(hold, 0.01)) - 1) * 100 if hold > 0 else float("nan")
        print(f"{band:>6} {len(g):>5} {sum(x['n'] for x in g):>7} {vwap:>7.4f} "
              f"{net:>9.3f} {hold:>8.1f}h {ann:>8.0f}% {sum(x['usd'] for x in g):>12,.0f}")

    print("\n=== biggest single opportunities at the 0.95 band ===")
    top = sorted((r for r in res if "b0.95" in r),
                 key=lambda r: -r["b0.95"]["usd"])[:15]
    print(f"{'$avail':>10} {'vwap':>7} {'net c':>7} {'hold':>8}  slug")
    for r in top:
        b = r["b0.95"]
        print(f"{b['usd']:>10,.0f} {b['vwap']:>7.4f} {b['net_c']:>7.2f} "
              f"{b['hold_h']:>7.1f}h  {r['slug'][:58]}")
    print(f"\ndone in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
