"""POST-CLOSE settlement-lag trade, both execution directions.

Run 1 tested only the TAKER direction (lift an ask): qualified in just 2.7% of
windows for +1.36c net. That scarcity is itself informative — if most post-close
flow is taker-SELL, the impatient winners are HITTING BIDS, which means the right
side of this trade is to REST A BID (maker, zero fee) and be the buyer of last
resort for people who want their capital back before settlement.

This measures both:
  TAKER  : taker-BUY prints (we lift an ask)  -> pay fee
  MAKER  : taker-SELL prints (we are the bid) -> zero fee, but queue matters

For the maker side we sweep bid levels and report, per level, how many windows
offer qualifying flow, the size, the realised win rate of the side we would buy,
and EV. The side is chosen ONLY from the market's own post-close convergence
(no oracle needed) — the same rule a live bot could apply.
"""
import sys, json, time, math
import requests
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

GAMMA = "https://gamma-api.polymarket.com/markets"
DATA = "https://data-api.polymarket.com/trades"
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 10
ASSET = sys.argv[2] if len(sys.argv) > 2 else "btc"
DUR = 300
LO, HI = 4, 60
LEVELS = [0.95, 0.96, 0.97, 0.98, 0.99]

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
        seg = [t for t in rows if close + LO <= t.get("timestamp", 0) <= close + HI]
        if not seg:
            return {"wts": wts, "post": 0}
        # market's own convergence pick: the outcome with the highest post-close print
        best = max(seg, key=lambda t: float(t["price"]))
        pick = best["outcome"]
        rec = {"wts": wts, "post": 1, "pick": pick, "winner": winner,
               "won": int(pick == winner), "n_prints": len(seg),
               "sides": Counter(t.get("side") for t in seg)}
        # flow on the PICKED side only
        for lvl in LEVELS:
            # maker: we rest a bid at lvl; taker-SELLs at price <= lvl would hit us
            mk = [t for t in seg if t.get("outcome") == pick and t.get("side") == "SELL"
                  and float(t["price"]) <= lvl + 1e-9]
            # taker: we lift asks; taker-BUYs at price <= lvl are proof of offers there
            tk = [t for t in seg if t.get("outcome") == pick and t.get("side") == "BUY"
                  and float(t["price"]) <= lvl + 1e-9]
            rec[f"mk_{lvl}_n"] = len(mk)
            rec[f"mk_{lvl}_sz"] = sum(float(t["size"]) for t in mk)
            rec[f"tk_{lvl}_n"] = len(tk)
            rec[f"tk_{lvl}_sz"] = sum(float(t["size"]) for t in tk)
        return rec
    except Exception:
        return None


def main():
    now = int(time.time())
    end = now - 1200 - (now % DUR)
    start = end - DAYS * 86400
    wtss = list(range(start, end, DUR))
    print(f"post-close both-directions, {len(wtss)} {ASSET} windows, "
          f"[close+{LO}s, close+{HI}s]", flush=True)
    res = []
    with ThreadPoolExecutor(max_workers=40) as ex:
        for i, r in enumerate(ex.map(one, wtss)):
            if r:
                res.append(r)
            if (i + 1) % 700 == 0:
                print(f"  {i+1}/{len(wtss)}", flush=True)
    json.dump([{k: (dict(v) if isinstance(v, Counter) else v) for k, v in r.items()} for r in res],
              open(f"/tmp/claude-0/-home-user-S3/8087631e-229b-5943-8560-8dda6ec827f4/scratchpad/postclose2_{ASSET}_{DAYS}d.json", "w"))
    post = [r for r in res if r.get("post")]
    print(f"\nresolved windows: {len(res)}   with post-close prints: {len(post)} "
          f"({len(post)/max(len(res),1)*100:.1f}%)")
    allsides = Counter()
    for r in post:
        allsides.update(r["sides"])
    print(f"post-close taker sides: {dict(allsides)}")

    k = sum(r["won"] for r in post)
    lo, hi = wilson(k, len(post))
    print(f"\nMARKET-CONVERGENCE PICK correct: {k}/{len(post)} = {k/len(post)*100:.3f}% "
          f"Wilson95 [{lo*100:.3f}, {hi*100:.3f}]")

    for tag, label, fee_on in (("mk", "MAKER (rest bid, sellers hit us)", False),
                               ("tk", "TAKER (lift offers)", True)):
        print(f"\n=== {label} ===")
        print(f"{'level':>6} {'windows':>8} {'%win':>7} {'wilson_lo':>10} {'med_sz':>8} "
              f"{'$/win':>8} {'EV/sh':>8} {'EV_lo':>8} {'trades/d':>9}")
        for lvl in LEVELS:
            q = [r for r in post if r[f"{tag}_{lvl}_sz"] > 0]
            if len(q) < 5:
                print(f"{lvl:>6} {len(q):>8}  (too few)")
                continue
            kk = sum(r["won"] for r in q)
            wr = kk / len(q)
            wlo, _ = wilson(kk, len(q))
            szs = sorted(r[f"{tag}_{lvl}_sz"] for r in q)
            med = szs[len(szs) // 2]
            fee = 0.07 * lvl * (1 - lvl) if fee_on else 0.0
            ev = (wr - lvl - fee) * 100
            evlo = (wlo - lvl - fee) * 100
            print(f"{lvl:>6} {len(q):>8} {wr*100:>6.2f}% {wlo*100:>9.2f}% {med:>8.0f} "
                  f"{med*lvl:>8.0f} {ev:>+8.2f}c {evlo:>+8.2f}c {len(q)/DAYS:>9.1f}")


if __name__ == "__main__":
    main()
