"""VENUE-WIDE OPPORTUNITY SCAN — beyond crypto 5m.

The crypto 5m family was mapped exhaustively and every route is closed. But its
closures were structural and they point elsewhere:

  * Market making failed because 5m markets CONVERGE (no mean reversion).
    Multi-day markets (weather / sports / politics) do not.
  * Liquidity rewards are configured on ZERO crypto markets but ARE configured
    on other categories -> a subsidised MM game we never tested.
  * The post-close settlement premium was real, and died only because bots watch
    BTC. Illiquid markets have the same premium with nobody watching.
  * Multi-outcome events allow a genuine DUTCH BOOK: mutually exclusive and
    exhaustive outcomes must sum to $1.00. If the asks sum below that, buying
    every outcome is risk-free -- and we are the taker, so fills are certain.

This enumerates ALL active markets and scores four opportunities:
  A) DUTCH BOOK      sum(asks) over an event's outcomes < 1 - fees
  B) REVERSE DUTCH   sum(bids) > 1 + fees
  C) REWARDED THIN   rewards configured AND little resting size to share it with
  D) STALE / WIDE    wide spread + low competition (stale-quote and MM candidates)

Usage: python3 scan_venue.py [max_markets]
"""
import json, sys, time, math
from collections import defaultdict
import requests
from concurrent.futures import ThreadPoolExecutor

GAMMA = "https://gamma-api.polymarket.com/markets"
CLOB = "https://clob.polymarket.com"
MAXM = int(sys.argv[1]) if len(sys.argv) > 1 else 3000

S = requests.Session()
S.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=64, max_retries=2))


def fee(p):
    return 0.07 * p * (1 - p)


def enumerate_markets(cap):
    out, off = [], 0
    while len(out) < cap:
        try:
            r = S.get(GAMMA, params={"closed": "false", "active": "true",
                                     "limit": 500, "offset": off}, timeout=25)
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
    return out[:cap]


def book(tok):
    try:
        r = S.get(f"{CLOB}/book", params={"token_id": tok}, timeout=10)
        if not r.ok:
            return None
        b = r.json()
        bids = [(float(x["price"]), float(x["size"])) for x in b.get("bids", [])]
        asks = [(float(x["price"]), float(x["size"])) for x in b.get("asks", [])]
        return {"bid": max(bids) if bids else None, "ask": min(asks) if asks else None,
                "bids": bids, "asks": asks}
    except Exception:
        return None


def yes_token(m):
    try:
        toks = json.loads(m.get("clobTokenIds") or "[]")
        outs = json.loads(m.get("outcomes") or "[]")
        if len(toks) != 2:
            return None
        i = outs.index("Yes") if "Yes" in outs else 0
        return toks[i]
    except Exception:
        return None


def main():
    t0 = time.time()
    ms = enumerate_markets(MAXM)
    print(f"enumerated {len(ms)} active markets in {time.time()-t0:.0f}s", flush=True)

    # ---------------- group into events ----------------
    ev = defaultdict(list)
    for m in ms:
        k = m.get("eventSlug") or (m.get("events") or [{}])[0].get("slug") or m.get("slug")
        ev[k].append(m)
    multi = {k: v for k, v in ev.items() if len(v) >= 3}
    print(f"events: {len(ev)}   multi-outcome (>=3 legs): {len(multi)}", flush=True)

    # ---------------- fetch books ----------------
    targets = []
    for k, legs in multi.items():
        for m in legs:
            t = yes_token(m)
            if t:
                targets.append((k, m, t))
    # plus rewarded / wide binary markets
    for m in ms:
        t = yes_token(m)
        if t and any(True for _ in [1]) and m.get("slug"):
            targets.append((None, m, t))
    seen, uniq = set(), []
    for k, m, t in targets:
        if (k, t) in seen:
            continue
        seen.add((k, t))
        uniq.append((k, m, t))
    print(f"fetching {len(uniq)} books...", flush=True)
    books = {}
    with ThreadPoolExecutor(max_workers=40) as ex:
        for (k, m, t), b in zip(uniq, ex.map(lambda x: book(x[2]), uniq)):
            if b:
                books[(k, t)] = (m, b)
    print(f"got {len(books)} books in {time.time()-t0:.0f}s\n", flush=True)

    # ---------------- A/B: dutch book ----------------
    print("=== A/B) DUTCH BOOK across multi-outcome events ===")
    rows = []
    for k, legs in multi.items():
        got = [(m, books[(k, yes_token(m))][1]) for m in legs
               if yes_token(m) and (k, yes_token(m)) in books]
        if len(got) < len(legs) or len(got) < 3:
            continue
        if any(b["ask"] is None for _, b in got):
            pass
        asks = [b["ask"] for _, b in got if b["ask"]]
        bids = [b["bid"] for _, b in got if b["bid"]]
        if len(asks) == len(legs):
            cost = sum(p for p, _ in asks)
            f = sum(fee(p) for p, _ in asks)
            edge = (1.0 - cost - f) * 100
            size = min(s for _, s in asks)
            rows.append(("BUY-ALL", k, len(legs), cost, edge, size))
        if len(bids) == len(legs):
            proceeds = sum(p for p, _ in bids)
            f = sum(fee(p) for p, _ in bids)
            edge = (proceeds - 1.0 - f) * 100
            size = min(s for _, s in bids)
            rows.append(("SELL-ALL", k, len(legs), proceeds, edge, size))
    rows.sort(key=lambda r: -r[4])
    pos = [r for r in rows if r[4] > 0]
    print(f"  events scored: {len(rows)}   PROFITABLE: {len(pos)}")
    for kind, k, n, tot, edge, size in rows[:12]:
        flag = "  <== ARB" if edge > 0 else ""
        print(f"  {kind:>8} {n:>2} legs  sum={tot:.4f}  edge={edge:+7.3f}c  "
              f"minsize={size:>7.0f}  {k[:52]}{flag}")
    if pos:
        tot_usd = sum(r[5] * r[4] / 100 for r in pos)
        print(f"\n  TOTAL RISK-FREE $ AVAILABLE NOW: ${tot_usd:,.2f}")

    # ---------------- C/D: rewarded + wide ----------------
    print("\n=== C/D) REWARDED and/or WIDE markets (MM + stale-quote candidates) ===")
    cands = []
    for (k, t), (m, b) in books.items():
        if b["bid"] is None or b["ask"] is None:
            continue
        bp, bs = b["bid"]
        ap, asz = b["ask"]
        spread = ap - bp
        mid = (ap + bp) / 2
        if not (0.05 < mid < 0.95):
            continue
        depth = sum(s for p, s in b["bids"] if p >= bp - 0.05) + \
                sum(s for p, s in b["asks"] if p <= ap + 0.05)
        rw = m.get("rewardsMinSize") or m.get("clobRewards") or 0
        cands.append({"slug": m.get("slug", "?")[:58], "spread": spread, "mid": mid,
                      "depth5c": depth, "bidsz": bs, "asksz": asz,
                      "vol24": float(m.get("volume24hr") or 0),
                      "liq": float(m.get("liquidity") or 0), "rw": rw,
                      "end": (m.get("endDate") or "")[:10]})
    wide = [c for c in cands if c["spread"] >= 0.03 and c["vol24"] > 200]
    wide.sort(key=lambda c: -(c["spread"] * math.log1p(c["vol24"])))
    print(f"  markets with 2-sided books: {len(cands)}   "
          f"spread>=3c AND 24h vol>$200: {len(wide)}")
    print(f"  {'spread':>7} {'mid':>6} {'depth5c':>9} {'vol24h':>10} {'ends':>11}  slug")
    for c in wide[:20]:
        print(f"  {c['spread']:>7.3f} {c['mid']:>6.3f} {c['depth5c']:>9.0f} "
              f"{c['vol24']:>10.0f} {c['end']:>11}  {c['slug']}")
    json.dump({"dutch": rows[:200], "wide": wide[:300]},
              open("/tmp/claude-0/-home-user-S3/8087631e-229b-5943-8560-8dda6ec827f4/scratchpad/venue_scan.json", "w"))
    print(f"\ndone in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
