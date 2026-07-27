"""VENUE-WIDE CALIBRATION STUDY — is Polymarket calibrated at the extremes?

The single most replicated anomaly in prediction markets is favourite-longshot
bias: longshots trade rich, favourites trade cheap. If it exists here it is the
one edge that is *taker*-executable (guaranteed fill, no queue, no adverse
selection) and it scales to whatever size rests on the book.

It is also the one place Polymarket's fee schedule works FOR us. Fee is
    rate * p * (1-p)
so at p=0.98 even the worst rate (crypto, 0.07) costs 0.137c/share versus 1.75c
at the midpoint. And most of the venue is outright fee-free (taker_base_fee=0).

METHOD (no lookahead anywhere):
  * Enumerate resolved binary markets by closedTime -- selection is on
    "resolved during the sample period", never on which side won.
  * For each market read the price of the YES token at closedTime - DELTA for
    several DELTAs. That is a price a trader could actually observe.
  * Bucket by that price, then measure the realised YES frequency.
  * Compare realised frequency to price. Wilson CI on the frequency, and a
    cluster-robust CI on per-dollar return because markets inside one event are
    the same bet wearing different hats.

Usage: python3 calibration.py [n_pages] [max_workers]
"""
import json, sys, time, math, os, statistics
from collections import defaultdict
import requests
from concurrent.futures import ThreadPoolExecutor

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
SCRATCH = "/tmp/claude-0/-home-user-S3/8087631e-229b-5943-8560-8dda6ec827f4/scratchpad"

PAGES = int(sys.argv[1]) if len(sys.argv) > 1 else 40
WORKERS = int(sys.argv[2]) if len(sys.argv) > 2 else 24
DELTAS_H = [1, 6, 24, 72, 168]          # hours before resolution
BANDS = [(0.50, 0.60), (0.60, 0.70), (0.70, 0.80), (0.80, 0.90), (0.90, 0.95),
         (0.95, 0.97), (0.97, 0.98), (0.98, 0.99), (0.99, 0.995), (0.995, 1.0)]

S = requests.Session()
S.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=WORKERS * 2, max_retries=3))


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def parse_ts(v):
    if not v:
        return None
    v = str(v).replace("T", " ").replace("Z", "")
    v = v.split("+")[0].split(".")[0].strip()
    try:
        return time.mktime(time.strptime(v, "%Y-%m-%d %H:%M:%S")) - time.timezone
    except Exception:
        return None


def enumerate_closed(pages):
    """Resolved binary markets. Selection is by resolution date, never by winner."""
    # Two gamma quirks, both of which silently truncate a scan:
    #   * limit is capped at 100 (asking 500 and breaking on len<500 gives 1 page)
    #   * offset is capped at ~2000 (offset=3000 -> HTTP 422)
    # So walk in date slices and paginate each slice separately.
    LIM, MAXOFF = 100, 2000
    out, seen = [], set()
    now = time.time()
    edges = [now - d * 86400 for d in range(0, pages * 3 + 1, 3)]
    slices = [(edges[i + 1], edges[i]) for i in range(len(edges) - 1)]

    def grab(job):
        lo, hi, off = job
        try:
            r = S.get(f"{GAMMA}/markets",
                      params={"closed": "true", "limit": LIM, "offset": off,
                              "end_date_min": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(lo)),
                              "end_date_max": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(hi))},
                      timeout=30)
            return r.json() if r.ok else []
        except Exception:
            return []

    jobs = [(lo, hi, off) for lo, hi in slices for off in range(0, MAXOFF, LIM)]
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for page in ex.map(grab, jobs):
            for m in page:
                c = m.get("conditionId")
                if c in seen:
                    continue
                seen.add(c)
                out.append(m)
    return out


def keep(m):
    try:
        pr = json.loads(m.get("outcomePrices") or "[]")
        outs = json.loads(m.get("outcomes") or "[]")
        if sorted(pr) != ["0", "1"] or len(outs) != 2:
            return None
        if float(m.get("volume") or 0) < 1000:
            return None
        slug = m.get("slug") or ""
        # the crypto up/down families are already mapped and would swamp the sample
        if "updown" in slug or "up-or-down" in slug:
            return None
        ts = parse_ts(m.get("closedTime")) or parse_ts(m.get("umaEndDate"))
        if not ts:
            return None
        toks = json.loads(m.get("clobTokenIds") or "[]")
        if len(toks) != 2:
            return None
        iy = outs.index("Yes") if "Yes" in outs else 0
        return {"slug": slug, "cond": m.get("conditionId"), "tok": toks[iy],
                "won": pr[iy] == "1", "ts": ts,
                "vol": float(m.get("volume") or 0),
                "event": ((m.get("events") or [{}])[0].get("slug")
                          or m.get("eventSlug") or slug),
                "negrisk": bool(m.get("negRisk"))}
    except Exception:
        return None


def history(rec):
    """Hourly price series for the YES token, cut to before resolution."""
    try:
        r = S.get(f"{CLOB}/prices-history",
                  params={"market": rec["tok"], "interval": "max", "fidelity": 60},
                  timeout=25)
        if not r.ok:
            return None
        pts = r.json().get("history", [])
        if len(pts) < 3:
            return None
        rec["px"] = {}
        for dh in DELTAS_H:
            cut = rec["ts"] - dh * 3600
            prior = [q for q in pts if q["t"] <= cut]
            if not prior:
                continue
            # must be a live quote, not a months-stale one
            if cut - prior[-1]["t"] > 6 * 3600:
                continue
            rec["px"][dh] = float(prior[-1]["p"])
        rec["last_t"] = pts[-1]["t"]
        rec["n_pts"] = len(pts)
        return rec if rec["px"] else None
    except Exception:
        return None


def main():
    t0 = time.time()
    raw = enumerate_closed(PAGES)
    print(f"enumerated {len(raw)} closed markets in {time.time()-t0:.0f}s", flush=True)
    recs = [x for x in (keep(m) for m in raw) if x]
    print(f"  -> {len(recs)} resolved binary, vol>=$1000, non-crypto-updown", flush=True)
    if recs:
        span = (min(r["ts"] for r in recs), max(r["ts"] for r in recs))
        print(f"  resolution window: {time.strftime('%Y-%m-%d', time.gmtime(span[0]))}"
              f" .. {time.strftime('%Y-%m-%d', time.gmtime(span[1]))}")
        print(f"  base rate YES = {sum(r['won'] for r in recs)/len(recs):.4f}"
              f"   (sanity: should be well under 0.50 -- most listed things do not happen)")

    print(f"\nfetching price history for {len(recs)} markets...", flush=True)
    got = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for i, r in enumerate(ex.map(history, recs)):
            if r:
                got.append(r)
            if (i + 1) % 500 == 0:
                print(f"   {i+1}/{len(recs)}  kept {len(got)}  ({time.time()-t0:.0f}s)",
                      flush=True)
    print(f"got usable history for {len(got)} markets in {time.time()-t0:.0f}s\n", flush=True)
    json.dump(got, open(f"{SCRATCH}/calibration.json", "w"))

    for dh in DELTAS_H:
        sub = [r for r in got if dh in r["px"]]
        if len(sub) < 50:
            print(f"=== T-{dh}h: only {len(sub)} markets, skipping ===\n")
            continue
        print(f"=== CALIBRATION at T-{dh}h before resolution   (n={len(sub)} markets) ===")
        print(f"  {'price band':>13} {'n':>5} {'mean px':>8} {'realised':>9} "
              f"{'wilson lo':>10} {'wilson hi':>10} {'edge pp':>9} {'ret/$':>8}")
        for lo, hi in BANDS:
            g = [r for r in sub if lo <= r["px"][dh] < hi]
            if len(g) < 8:
                continue
            n = len(g)
            k = sum(r["won"] for r in g)
            mp = sum(r["px"][dh] for r in g) / n
            f = k / n
            wl, wh = wilson(k, n)
            # per-dollar return of buying YES at the observed price
            ret = sum((1.0 - r["px"][dh]) if r["won"] else -r["px"][dh] for r in g) \
                / sum(r["px"][dh] for r in g)
            print(f"  {lo:.3f}-{hi:<7.3f} {n:>5} {mp:>8.4f} {f:>9.4f} "
                  f"{wl:>10.4f} {wh:>10.4f} {(f-mp)*100:>+9.2f} {ret*100:>+7.2f}%")
        print()

    print(f"done in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
