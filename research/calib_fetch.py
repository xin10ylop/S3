"""STAGE 1 -- cache raw hourly price series for every resolved binary market.

Separated from the analysis so that the expensive network pull happens once and
every subsequent statistical question is answered offline for free.

Selection is on "resolved during the sample window", never on which side won.

Usage: python3 calib_fetch.py [days_back] [workers]
"""
import json, sys, time, os
import requests
from concurrent.futures import ThreadPoolExecutor

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
OUT = "/home/user/S3/data/calib"
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 400
WORKERS = int(sys.argv[2]) if len(sys.argv) > 2 else 32

S = requests.Session()
S.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=WORKERS * 2, max_retries=3))
os.makedirs(OUT, exist_ok=True)


def parse_ts(v):
    if not v:
        return None
    v = str(v).replace("T", " ").replace("Z", "").split("+")[0].split(".")[0].strip()
    try:
        return time.mktime(time.strptime(v, "%Y-%m-%d %H:%M:%S")) - time.timezone
    except Exception:
        return None


def enumerate_closed(days):
    """gamma caps limit at 100 and offset at ~2000, so walk in 3-day slices."""
    LIM, MAXOFF = 100, 2000
    now = time.time()
    edges = [now - d * 86400 for d in range(0, days + 1, 3)]
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
    out, seen = [], set()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for page in ex.map(grab, jobs):
            for m in page:
                c = m.get("conditionId")
                if c and c not in seen:
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
        if "updown" in slug or "up-or-down" in slug:
            return None      # crypto 5m already mapped; would swamp the sample
        ts = parse_ts(m.get("closedTime")) or parse_ts(m.get("umaEndDate"))
        if not ts:
            return None
        toks = json.loads(m.get("clobTokenIds") or "[]")
        if len(toks) != 2:
            return None
        iy = outs.index("Yes") if "Yes" in outs else 0
        ev = (m.get("events") or [{}])
        return {"slug": slug, "tok": toks[iy], "won": int(pr[iy] == "1"), "ts_res": ts,
                "vol": float(m.get("volume") or 0),
                "event": (ev[0].get("slug") if ev else None) or slug,
                "negrisk": int(bool(m.get("negRisk"))),
                "end": parse_ts(m.get("endDate"))}
    except Exception:
        return None


def fetch(rec):
    try:
        r = S.get(f"{CLOB}/prices-history",
                  params={"market": rec["tok"], "interval": "max", "fidelity": 60}, timeout=25)
        if not r.ok:
            return None
        pts = r.json().get("history", [])
        if len(pts) < 4:
            return None
        rec["t"] = [int(q["t"]) for q in pts]
        rec["p"] = [round(float(q["p"]), 5) for q in pts]
        rec.pop("tok", None)
        return rec
    except Exception:
        return None


def main():
    t0 = time.time()
    raw = enumerate_closed(DAYS)
    print(f"enumerated {len(raw)} closed markets in {time.time()-t0:.0f}s", flush=True)
    recs = [x for x in (keep(m) for m in raw) if x]
    print(f"  -> {len(recs)} resolved binary, vol>=$1000, non-crypto-updown", flush=True)
    lo = min(r["ts_res"] for r in recs); hi = max(r["ts_res"] for r in recs)
    print(f"  resolution window {time.strftime('%Y-%m-%d', time.gmtime(lo))}"
          f" .. {time.strftime('%Y-%m-%d', time.gmtime(hi))}"
          f"   base YES rate {sum(r['won'] for r in recs)/len(recs):.4f}", flush=True)

    got, f = [], open(f"{OUT}/series.jsonl", "w")
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for i, r in enumerate(ex.map(fetch, recs)):
            if r:
                f.write(json.dumps(r) + "\n")
                got.append(1)
            if (i + 1) % 2000 == 0:
                print(f"   {i+1}/{len(recs)}  kept {len(got)}  ({time.time()-t0:.0f}s)", flush=True)
    f.close()
    sz = os.path.getsize(f"{OUT}/series.jsonl") / 1e6
    print(f"\nwrote {len(got)} series -> {OUT}/series.jsonl ({sz:.0f} MB) "
          f"in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
