"""NEG-RISK DUTCH BOOK SCAN — mutually exclusive events, guaranteed fills.

In a negRisk event exactly one outcome resolves YES. Therefore:
  BUY-ALL-YES : cost sum(yes_ask); payout exactly $1  -> arb if sum(ask) < 1 - fees
  BUY-ALL-NO  : cost sum(no_ask) = n - sum(yes_bid); payout exactly $(n-1)
                -> arb if sum(yes_bid) > 1 + fees

Both are TAKER trades on every leg, so fills are certain — no queue, no adverse
selection, no forecast. The only enemies are the fee and the spread.

Fee rate by category (docs.polymarket.com/trading/fees):
  crypto 0.07 | sports, economics, culture, weather 0.05 | finance, politics,
  mentions, tech 0.04 | GEOPOLITICAL & WORLD EVENTS: FEE-FREE
Fee per share = rate * p * (1-p), so it is worst near 0.50 and collapses at the
extremes — which favours events with one dominant favourite and many longshots.

Usage: python3 scan_negrisk.py [max_legs] [min_liquidity]
"""
import json, sys, time
import requests
from concurrent.futures import ThreadPoolExecutor

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
MAXLEGS = int(sys.argv[1]) if len(sys.argv) > 1 else 24
MINLIQ = float(sys.argv[2]) if len(sys.argv) > 2 else 20000

RATES = {"crypto": 0.07, "sports": 0.05, "economics": 0.05, "culture": 0.05,
         "weather": 0.05, "finance": 0.04, "politics": 0.04, "mentions": 0.04,
         "tech": 0.04, "geopolitics": 0.0, "world": 0.0}

S = requests.Session()
S.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=64, max_retries=2))


def rate_for(ev):
    blob = " ".join([ev.get("slug", "")] +
                    [str(t.get("label", t) if isinstance(t, dict) else t)
                     for t in (ev.get("tags") or [])]).lower()
    for k in ("geopolitic", "world", "war", "ukraine", "israel", "iran", "ceasefire"):
        if k in blob:
            return 0.0, "geo/world (fee-free)"
    for k, v in RATES.items():
        if k in blob:
            return v, k
    return 0.05, "default 0.05"


def book(tok):
    try:
        r = S.get(f"{CLOB}/book", params={"token_id": tok}, timeout=10)
        if not r.ok:
            return None
        b = r.json()
        bids = [(float(x["price"]), float(x["size"])) for x in b.get("bids", [])]
        asks = [(float(x["price"]), float(x["size"])) for x in b.get("asks", [])]
        return {"bid": max(bids) if bids else None, "ask": min(asks) if asks else None}
    except Exception:
        return None


def main():
    t0 = time.time()
    evs, off = [], 0
    while off < 2000:
        r = S.get(f"{GAMMA}/events", params={"closed": "false", "active": "true",
                                             "limit": 100, "offset": off}, timeout=25)
        if not r.ok:
            break
        pg = r.json()
        if not pg:
            break
        evs += pg
        off += 100
        if len(pg) < 100:
            break
    neg = [e for e in evs
           if e.get("negRisk") and 3 <= len(e.get("markets", [])) <= MAXLEGS
           and float(e.get("liquidity") or 0) >= MINLIQ]
    print(f"{len(evs)} active events -> {len(neg)} negRisk with 3-{MAXLEGS} legs "
          f"and liq>=${MINLIQ:,.0f}", flush=True)

    jobs = []
    for e in neg:
        for m in e.get("markets", []):
            try:
                toks = json.loads(m.get("clobTokenIds") or "[]")
                outs = json.loads(m.get("outcomes") or "[]")
                if len(toks) != 2:
                    continue
                i = outs.index("Yes") if "Yes" in outs else 0
                jobs.append((e["slug"], m.get("slug"), toks[i]))
            except Exception:
                continue
    print(f"fetching {len(jobs)} books...", flush=True)
    res = {}
    with ThreadPoolExecutor(max_workers=40) as ex:
        for (ev, mk, tk), b in zip(jobs, ex.map(lambda j: book(j[2]), jobs)):
            if b:
                res.setdefault(ev, []).append((mk, b))
    print(f"got books for {len(res)} events in {time.time()-t0:.0f}s\n", flush=True)

    out = []
    for e in neg:
        legs = res.get(e["slug"], [])
        n_expected = len(e.get("markets", []))
        if len(legs) < n_expected:
            continue
        rate, cat = rate_for(e)
        asks = [b["ask"] for _, b in legs]
        bids = [b["bid"] for _, b in legs]
        row = {"slug": e["slug"], "n": n_expected, "cat": cat, "rate": rate,
               "liq": float(e.get("liquidity") or 0)}
        if all(a for a in asks):
            cost = sum(p for p, _ in asks)
            f = sum(rate * p * (1 - p) for p, _ in asks)
            row["buy_all"] = {"cost": cost, "fee": f,
                              "edge_c": (1 - cost - f) * 100,
                              "size": min(s for _, s in asks)}
        if all(b for b in bids):
            proceeds = sum(p for p, _ in bids)
            f = sum(rate * p * (1 - p) for p, _ in bids)
            row["buy_all_no"] = {"proceeds": proceeds, "fee": f,
                                 "edge_c": (proceeds - 1 - f) * 100,
                                 "size": min(s for _, s in bids)}
        out.append(row)

    json.dump(out, open("/tmp/claude-0/-home-user-S3/8087631e-229b-5943-8560-8dda6ec827f4/scratchpad/negrisk_scan.json", "w"))
    print(f"scored {len(out)} complete events\n")

    for key, label in (("buy_all", "BUY-ALL-YES  (sum asks < 1)"),
                       ("buy_all_no", "BUY-ALL-NO   (sum bids > 1)")):
        rows = [(r, r[key]) for r in out if key in r]
        rows.sort(key=lambda x: -x[1]["edge_c"])
        pos = [x for x in rows if x[1]["edge_c"] > 0]
        print(f"=== {label} ===")
        print(f"  events scored: {len(rows)}   PROFITABLE: {len(pos)}")
        print(f"  {'edge':>9} {'sum':>7} {'fee':>7} {'legs':>5} {'minsz':>8} {'rate':>6}  slug")
        for r, d in rows[:10]:
            s = d.get("cost", d.get("proceeds"))
            mark = "  <== ARB" if d["edge_c"] > 0 else ""
            print(f"  {d['edge_c']:>+8.3f}c {s:>7.4f} {d['fee']*100:>6.2f}c "
                  f"{r['n']:>5} {d['size']:>8.0f} {r['rate']:>6.2f}  {r['slug'][:44]}{mark}")
        if pos:
            tot = sum(x[1]["size"] * x[1]["edge_c"] / 100 for x in pos)
            print(f"  >>> TOTAL RISK-FREE $ AVAILABLE: ${tot:,.2f}")
        print()
    print(f"done in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
