"""Decisive current-regime measurement: sweep N days of btc-updown-5m from the
PUBLIC API with a fully-indexed tape, and measure the favorite's win rate,
fill rate, and win-given-fill with proper statistics.

This exists because the live paper bot's numbers (60% fill rate, 83% win, n=29)
are too small AND were computed from a possibly-incomplete real-time tape.
Days-old data is fully indexed, so this is the clean measurement.

Usage: python3 sweep_current.py [days] [asset] [dur_s]
"""
import sys, json, time, math
import requests
from concurrent.futures import ThreadPoolExecutor

GAMMA = "https://gamma-api.polymarket.com/markets"
DATA = "https://data-api.polymarket.com/trades"
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 30
ASSET = sys.argv[2] if len(sys.argv) > 2 else "btc"
DUR = int(sys.argv[3]) if len(sys.argv) > 3 else 300

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
    slug = f"{ASSET}-updown-{'5m' if DUR == 300 else '15m'}-{wts}"
    try:
        r = S.get(GAMMA, params={"slug": slug, "closed": "true"}, timeout=12)
        ms = r.json() if r.ok else []
        if not ms:
            r = S.get(GAMMA, params={"slug": slug}, timeout=12)
            ms = r.json() if r.ok else []
        if not ms:
            return {"wts": wts, "skip": "no_market"}
        m = ms[0]
        prices = json.loads(m.get("outcomePrices", "[]"))
        outs = json.loads(m.get("outcomes", "[]"))
        if not prices or set(prices) != {"1", "0"}:
            return {"wts": wts, "skip": "unresolved"}
        winner = outs[prices.index("1")]
        cond = m["conditionId"]
        rows = []
        for off in range(0, 8 * 500, 500):
            rr = S.get(DATA, params={"market": cond, "limit": 500, "offset": off}, timeout=15)
            if not rr.ok:
                break
            page = rr.json()
            rows.extend(page)
            if len(page) < 500:
                break
        if not rows:
            return {"wts": wts, "skip": "no_tape"}

        def upx(t):
            p = float(t["price"])
            return p if t.get("outcome") == "Up" else round(1 - p, 4)

        sig = sorted((upx(t) for t in rows if wts + 50 <= t.get("timestamp", 0) <= wts + 62))
        if not sig:
            return {"wts": wts, "skip": "no_prints_o60"}
        n_sig_prints = len(sig)
        mmid = sig[len(sig) // 2]
        if 0.85 <= mmid <= 0.97:
            fav, favp = "Up", mmid
        elif 0.85 <= 1 - mmid <= 0.97:
            fav, favp = "Down", round(1 - mmid, 4)
        else:
            return {"wts": wts, "skip": "no_favorite"}
        entry = round(favp - 0.01, 4)
        # fill window (wts+60, wts+90]: flow at or through our resting level
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
                "won": int(winner == fav), "n_sig_prints": n_sig_prints,
                "below": below, "at": at,
                "fill_any": int(below + at > 0), "fill_deep": int(below > 0)}
    except Exception as e:
        return {"wts": wts, "skip": f"err:{type(e).__name__}"}


def main():
    now = int(time.time())
    end = now - 900 - (now % DUR)
    start = end - DAYS * 86400
    wtss = list(range(start, end, DUR))
    print(f"sweeping {len(wtss)} windows of {ASSET}-{DUR}s over {DAYS} days", flush=True)
    out = []
    done = 0
    with ThreadPoolExecutor(max_workers=40) as ex:
        for r in ex.map(one, wtss):
            out.append(r)
            done += 1
            if done % 500 == 0:
                sigs = sum(1 for x in out if "skip" not in x)
                print(f"  {done}/{len(wtss)} windows, {sigs} signals", flush=True)
    sigs = [x for x in out if "skip" not in x]
    path = f"/tmp/claude-0/-home-user-S3/8087631e-229b-5943-8560-8dda6ec827f4/scratchpad/sweep_{ASSET}_{DUR}_{DAYS}d.json"
    with open(path, "w") as f:
        json.dump(out, f)
    print(f"\nraw saved: {path}")

    def block(rows, name):
        if not rows:
            print(f"{name:<28} n=0")
            return
        n = len(rows)
        k = sum(r["won"] for r in rows)
        wr = k / n
        me = sum(r["entry"] for r in rows) / n
        lo, hi = wilson(k, n)
        ev = (wr - me) * 100
        ev_lo = (lo - me) * 100
        print(f"{name:<28} n={n:<5} win={wr*100:5.1f}% [{lo*100:.1f},{hi*100:.1f}]  "
              f"entry={me:.4f}  EV={ev:+.2f}c  EV_lo={ev_lo:+.2f}c")

    print("\n=== CURRENT REGIME, fully-indexed tape ===")
    block(sigs, "ALL signals")
    block([r for r in sigs if r["fill_any"]], "GIVEN FILL (any flow)")
    block([r for r in sigs if r["fill_deep"]], "GIVEN FILL (deep/through)")
    block([r for r in sigs if not r["fill_any"]], "NOT FILLED")
    print(f"\nfill rate (any) = {sum(r['fill_any'] for r in sigs)/max(len(sigs),1)*100:.1f}%   "
          f"(deep) = {sum(r['fill_deep'] for r in sigs)/max(len(sigs),1)*100:.1f}%")
    print("\nby price band:")
    for lo_b, hi_b in [(0.85, 0.88), (0.88, 0.91), (0.91, 0.94), (0.94, 0.9701)]:
        block([r for r in sigs if lo_b <= r["fav_price"] < hi_b], f"  [{lo_b},{hi_b})")
    print("\nby week (most recent last):")
    if sigs:
        t0 = min(r["wts"] for r in sigs)
        for w in range((DAYS + 6) // 7):
            block([r for r in sigs if t0 + w * 7 * 86400 <= r["wts"] < t0 + (w + 1) * 7 * 86400],
                  f"  week {w+1}")
    print("\nprice-quality control (>=3 prints backing the o60 median):")
    block([r for r in sigs if r["n_sig_prints"] >= 3], "  >=3 prints")
    from collections import Counter
    print("\nskips:", dict(Counter(x.get("skip") for x in out if "skip" in x).most_common(8)))


if __name__ == "__main__":
    main()
