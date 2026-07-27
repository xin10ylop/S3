"""THE KILL TEST for the post-close maker trade.

Historical tape says: rest a bid at 0.99 on the post-close converged side and
sellers hit you for a median 413 shares/window, 100% win (Wilson lo 99.85%),
+1.00c/share with zero fee.

But tape flow does NOT prove we would get the fill. If competitors already rest
thousands of shares at 0.99, we join the back of the queue and receive nothing —
exactly the trap that made the previous strategy look good on paper.

This samples the LIVE book right after close on all five assets simultaneously
(they share window timestamps), recording the resting bid size at 0.99 on the
converged side. Our $100 clip is ~101 shares; we fill only if
   incoming_sell_flow > queue_ahead.
"""
import json, time, sys
import requests
from concurrent.futures import ThreadPoolExecutor

GAMMA = "https://gamma-api.polymarket.com/markets"
CLOB = "https://clob.polymarket.com"
ASSETS = ["btc", "eth", "sol", "xrp", "doge"]
MINUTES = int(sys.argv[1]) if len(sys.argv) > 1 else 25
OFFSETS = [5, 12, 20, 35, 50]      # seconds after close to sample

S = requests.Session()
S.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=32, max_retries=2))


def book(tok):
    r = S.get(f"{CLOB}/book", params={"token_id": tok}, timeout=8)
    r.raise_for_status()
    b = r.json()
    bids = [(float(x["price"]), float(x["size"])) for x in b.get("bids", [])]
    asks = [(float(x["price"]), float(x["size"])) for x in b.get("asks", [])]
    return bids, asks


def sample(asset, wts):
    """Sample one asset's window at several post-close offsets."""
    try:
        ms = S.get(GAMMA, params={"slug": f"{asset}-updown-5m-{wts}"}, timeout=10).json()
        if not ms:
            return []
        toks = json.loads(ms[0]["clobTokenIds"])
        outs = json.loads(ms[0]["outcomes"])
    except Exception:
        return []
    close = wts + 300
    rows = []
    for off in OFFSETS:
        while time.time() < close + off:
            time.sleep(0.2)
        for tok, out in zip(toks, outs):
            try:
                bids, asks = book(tok)
            except Exception:
                continue
            if not bids:
                continue
            bb = max(bids)
            if bb[0] < 0.90:          # not the converged side
                continue
            q99 = sum(sz for p, sz in bids if abs(p - 0.99) < 1e-9)
            q98 = sum(sz for p, sz in bids if abs(p - 0.98) < 1e-9)
            ba = min(asks) if asks else (None, 0)
            rows.append({"asset": asset, "wts": wts, "off": off, "outcome": out,
                         "best_bid": bb[0], "best_bid_sz": bb[1],
                         "queue_at_099": q99, "queue_at_098": q98,
                         "best_ask": ba[0], "best_ask_sz": ba[1]})
    return rows


def main():
    end_at = time.time() + MINUTES * 60
    out = []
    print(f"sampling live post-close books for {MINUTES} min across {ASSETS}", flush=True)
    while time.time() < end_at:
        now = int(time.time())
        wts = now - (now % 300)          # window currently closing
        close = wts + 300
        if close - now > 70 or close - now < 3:
            time.sleep(2)
            continue
        print(f"  window {wts} closes in {close-now}s", flush=True)
        with ThreadPoolExecutor(max_workers=5) as ex:
            for r in ex.map(lambda a: sample(a, wts), ASSETS):
                out.extend(r)
        print(f"    collected {len(out)} book snapshots so far", flush=True)
    p = "/tmp/claude-0/-home-user-S3/8087631e-229b-5943-8560-8dda6ec827f4/scratchpad/postclose_queue.json"
    json.dump(out, open(p, "w"))
    print(f"\nsaved {len(out)} snapshots -> {p}\n")
    if not out:
        print("no data")
        return
    print(f"{'off':>5} {'n':>4} {'med_bid':>8} {'med_q@0.99':>11} {'p75_q':>8} {'p90_q':>8} {'med_ask':>8}")
    for off in OFFSETS:
        g = [r for r in out if r["off"] == off]
        if not g:
            continue
        qs = sorted(r["queue_at_099"] for r in g)
        bb = sorted(r["best_bid"] for r in g)
        ask = sorted(r["best_ask"] for r in g if r["best_ask"])
        print(f"{off:>5} {len(g):>4} {bb[len(bb)//2]:>8.3f} {qs[len(qs)//2]:>11.0f} "
              f"{qs[3*len(qs)//4]:>8.0f} {qs[int(0.9*len(qs))]:>8.0f} "
              f"{(ask[len(ask)//2] if ask else float('nan')):>8.3f}")
    qs = sorted(r["queue_at_099"] for r in out)
    med = qs[len(qs) // 2]
    print(f"\noverall median queue resting at 0.99: {med:.0f} shares (${med*0.99:.0f})")
    print(f"  our $100 clip = 101 shares; median incoming post-close sell flow = 413 shares")
    print(f"  => we fill if 413 > queue_ahead. queue<312 in "
          f"{sum(1 for q in qs if q < 312)/len(qs)*100:.0f}% of snapshots")
    print(f"  fraction of snapshots with queue at 0.99 == 0: "
          f"{sum(1 for q in qs if q == 0)/len(qs)*100:.0f}%")


if __name__ == "__main__":
    main()
