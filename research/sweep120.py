"""Decisive 120-day sweep of btc-updown-5m for the EFC-M favorite signal.

Speed tricks vs sweep_current.py (validated by measurement, same methodology):
  * gamma /markets accepts repeated ?slug= params -> 100 markets per request
  * data-api /trades accepts limit=5000 -> whole tape of a 5m market in ONE call
    (median 2621 trades/market; only paginate when a page comes back full AND
     still hasn't reached wts+50, since trades are returned NEWEST-FIRST)

Usage: python3 sweep120.py <day_from> <day_to>
  sweeps windows in [ANCHOR_END - day_to*86400, ANCHOR_END - day_from*86400)
  writes /…/scratchpad/sweep120_chunk_<day_from>_<day_to>.json
"""
import sys, json, time, os
import requests
from concurrent.futures import ThreadPoolExecutor

GAMMA = "https://gamma-api.polymarket.com/markets"
DATA = "https://data-api.polymarket.com/trades"
SCRATCH = "/tmp/claude-0/-home-user-S3/8087631e-229b-5943-8560-8dda6ec827f4/scratchpad"
ANCHOR_END = 1785148500          # fixed so every chunk uses the same grid
ASSET, DUR = "btc", 300

S = requests.Session()
S.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=100, max_retries=3))


def meta_batch(wtss):
    """Resolve up to 100 slugs in one gamma call -> {wts: (conditionId, winner)}."""
    slugs = [f"{ASSET}-updown-5m-{w}" for w in wtss]
    out = {}
    for attempt in range(3):
        try:
            r = S.get(GAMMA, params=[("slug", s) for s in slugs]
                      + [("closed", "true"), ("limit", str(len(slugs)))], timeout=45)
            if not r.ok:
                time.sleep(1 + attempt)
                continue
            for m in r.json():
                try:
                    w = int(m["slug"].rsplit("-", 1)[1])
                    prices = json.loads(m.get("outcomePrices", "[]"))
                    outs = json.loads(m.get("outcomes", "[]"))
                    if not prices or set(prices) != {"1", "0"}:
                        out[w] = (None, "unresolved")
                        continue
                    out[w] = (m["conditionId"], outs[prices.index("1")])
                except Exception:
                    pass
            return out
        except Exception:
            time.sleep(1 + attempt)
    return out


def upx(t):
    p = float(t["price"])
    return p if t.get("outcome") == "Up" else round(1 - p, 4)


def one(arg):
    wts, cond, winner = arg
    if cond is None:
        return {"wts": wts, "skip": winner}
    try:
        rows = []
        for off in range(0, 4 * 5000, 5000):
            rr = S.get(DATA, params={"market": cond, "limit": 5000, "offset": off}, timeout=60)
            if not rr.ok:
                break
            page = rr.json()
            rows.extend(page)
            # newest-first: stop once we've walked back past the o60 window
            if len(page) < 5000 or (page and page[-1].get("timestamp", 0) < wts + 50):
                break
        if not rows:
            return {"wts": wts, "skip": "no_tape"}

        sig = sorted(upx(t) for t in rows if wts + 50 <= t.get("timestamp", 0) <= wts + 62)
        if not sig:
            return {"wts": wts, "skip": "no_prints_o60"}
        n_sig = len(sig)
        m = sig[n_sig // 2]
        if 0.85 <= m <= 0.97:
            fav, favp = "Up", m
        elif 0.85 <= 1 - m <= 0.97:
            fav, favp = "Down", round(1 - m, 4)
        else:
            return {"wts": wts, "skip": "no_favorite", "mid": m}
        entry = round(favp - 0.01, 4)
        below = at = 0.0
        for t in rows:
            ts = t.get("timestamp", 0)
            if not (wts + 60 < ts <= wts + 90):
                continue
            up = upx(t)
            fp = up if fav == "Up" else round(1 - up, 4)
            sz = float(t["size"])
            if fp < entry - 5e-4:
                below += sz
            elif abs(fp - entry) <= 5e-4:
                at += sz
        return {"wts": wts, "fav": fav, "fav_price": favp, "entry": entry,
                "won": int(winner == fav), "n_sig_prints": n_sig,
                "below": below, "at": at, "ntape": len(rows),
                "fill_any": int(below + at > 0), "fill_deep": int(below > 0)}
    except Exception as e:
        return {"wts": wts, "skip": f"err:{type(e).__name__}"}


def main():
    d_from, d_to = int(sys.argv[1]), int(sys.argv[2])
    lo, hi = ANCHOR_END - d_to * 86400, ANCHOR_END - d_from * 86400
    wtss = list(range(lo, hi, DUR))
    print(f"[chunk {d_from}-{d_to}d] {len(wtss)} windows  {lo}..{hi}", flush=True)

    t0 = time.time()
    batches = [wtss[i:i + 100] for i in range(0, len(wtss), 100)]
    meta = {}
    with ThreadPoolExecutor(max_workers=20) as ex:
        for i, d in enumerate(ex.map(meta_batch, batches)):
            meta.update(d)
            if (i + 1) % 20 == 0:
                print(f"  meta {len(meta)}/{len(wtss)}  {time.time()-t0:.0f}s", flush=True)
    print(f"  meta done: {len(meta)} markets in {time.time()-t0:.0f}s", flush=True)

    args = [(w, *meta.get(w, (None, "no_market"))) for w in wtss]
    out, done = [], 0
    with ThreadPoolExecutor(max_workers=40) as ex:
        for r in ex.map(one, args):
            out.append(r)
            done += 1
            if done % 1000 == 0:
                s = sum(1 for x in out if "skip" not in x)
                print(f"  {done}/{len(wtss)}  {s} signals  {time.time()-t0:.0f}s", flush=True)

    path = f"{SCRATCH}/sweep120_chunk_{d_from}_{d_to}.json"
    with open(path, "w") as f:
        json.dump(out, f)
    sigs = sum(1 for x in out if "skip" not in x)
    wins = sum(x["won"] for x in out if "skip" not in x)
    print(f"[chunk {d_from}-{d_to}d] DONE {len(out)} windows, {sigs} signals, "
          f"{wins} wins ({100*wins/max(sigs,1):.2f}%), {time.time()-t0:.0f}s -> {path}", flush=True)


if __name__ == "__main__":
    main()
