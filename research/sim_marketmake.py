"""TWO-SIDED MARKET MAKING simulation on thin alt-asset 5m markets.

Rationale: every strategy tested so far was a one-sided directional maker bet, and
each died on adverse selection — we only got filled when the price came to us,
which is when we were wrong.

Market making is structurally different: we quote BOTH sides. When both fill we
capture the spread and carry zero position. Crucially the fee asymmetry here is
enormous — makers pay ZERO while a taker crossing at p=0.50 pays 0.07*0.25 =
1.75c/share on top of the spread. In DOGE/XRP the quoted spread is ~4c. That is a
large gross edge IF the inventory left over when the price runs does not eat it.

Simulation, per window, per asset:
  - quote a bid at (mid - h) and an ask at (mid + h) starting at t_start
  - replay the tape: a taker SELL at <= our bid fills our bid; a taker BUY at
    >= our ask fills our ask (queue-join-back using displayed size is not
    available per-side historically, so we test both a zero-queue optimistic
    case and a pessimistic case requiring 2x our size to print through)
  - requote every requote_s seconds around the new mid
  - at close, any residual inventory settles at 1 or 0
  - P&L = spread captured + settlement value of residual inventory

Usage: python3 sim_marketmake.py [days] [asset]
"""
import sys, json, time, math, statistics
import requests
from concurrent.futures import ThreadPoolExecutor

GAMMA = "https://gamma-api.polymarket.com/markets"
DATA = "https://data-api.polymarket.com/trades"
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 5
ASSET = sys.argv[2] if len(sys.argv) > 2 else "doge"
DUR = 300
HALF_SPREADS = [0.01, 0.02, 0.03]     # how far off mid we quote
CLIP = 20.0                            # $ per side
T_START, T_END = 30, 270               # quote only mid-window (avoid open/close chaos)
REQUOTE = 30

S = requests.Session()
S.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=64, max_retries=2))


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
        up_won = 1 if outs[prices.index("1")] == "Up" else 0
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
    except Exception:
        return None
    # normalize every print to Up terms with its taker side
    tape = []
    for t in rows:
        ts = t.get("timestamp", 0) - wts
        if not (0 <= ts <= DUR):
            continue
        p = float(t["price"])
        if t.get("outcome") == "Up":
            up_px, side = p, t.get("side")
        else:
            up_px = round(1 - p, 4)
            side = "BUY" if t.get("side") == "SELL" else "SELL"   # mirror the aggressor
        tape.append((ts, up_px, float(t["size"]), side))
    if len(tape) < 10:
        return None
    tape.sort()
    out = {"wts": wts, "n_prints": len(tape), "up_won": up_won}
    for h in HALF_SPREADS:
        for pess in (False, True):
            inv = 0.0        # shares of Up held (negative = short via Down)
            cash = 0.0
            fills_b = fills_a = 0
            last_q = -999
            bid = ask = None
            for ts, px, sz, side in tape:
                if ts < T_START or ts > T_END:
                    continue
                if ts - last_q >= REQUOTE:      # requote around the current price
                    bid, ask = round(px - h, 4), round(px + h, 4)
                    last_q = ts
                need = 2.0 if pess else 1.0     # pessimistic: 2x our size must print
                qty = CLIP / max(px, 0.01)
                # each side is checked independently; a filled side stays down
                # until the next requote (one fill per quote cycle per side)
                if (side == "SELL" and bid is not None
                        and px <= bid + 1e-9 and sz >= need * qty):
                    inv += qty
                    cash -= qty * bid
                    fills_b += 1
                    bid = None
                elif (side == "BUY" and ask is not None
                        and px >= ask - 1e-9 and sz >= need * qty):
                    inv -= qty
                    cash += qty * ask
                    fills_a += 1
                    ask = None
            settle = inv * (1.0 if up_won else 0.0)
            pnl = cash + settle
            key = f"h{h}{'_pess' if pess else ''}"
            out[key] = {"pnl": pnl, "fills_b": fills_b, "fills_a": fills_a,
                        "resid": inv}
    return out


def main():
    now = int(time.time())
    end = now - 1200 - (now % DUR)
    start = end - DAYS * 86400
    wtss = list(range(start, end, DUR))
    print(f"market-making sim: {ASSET}, {len(wtss)} windows, {DAYS}d, "
          f"clip ${CLIP}/side, quote t={T_START}-{T_END}s", flush=True)
    res = []
    with ThreadPoolExecutor(max_workers=40) as ex:
        for i, r in enumerate(ex.map(one, wtss)):
            if r:
                res.append(r)
            if (i + 1) % 400 == 0:
                print(f"  {i+1}/{len(wtss)}, {len(res)} usable", flush=True)
    json.dump(res, open(f"/tmp/claude-0/-home-user-S3/8087631e-229b-5943-8560-8dda6ec827f4/scratchpad/mm_{ASSET}_{DAYS}d.json", "w"))
    print(f"\nusable windows: {len(res)}\n")
    print(f"{'variant':>12} {'wins/win':>9} {'bidfills':>9} {'askfills':>9} "
          f"{'both%':>7} {'PnL/win':>9} {'total$':>10} {'sd':>8} {'t-stat':>7}")
    for h in HALF_SPREADS:
        for pess in (False, True):
            key = f"h{h}{'_pess' if pess else ''}"
            g = [r[key] for r in res if key in r]
            if not g:
                continue
            pnls = [x["pnl"] for x in g]
            both = sum(1 for x in g if x["fills_b"] and x["fills_a"]) / len(g)
            mean = statistics.mean(pnls)
            sd = statistics.pstdev(pnls) or 1e-9
            t = mean / (sd / math.sqrt(len(pnls)))
            print(f"{key:>12} {len(g):>9} "
                  f"{statistics.mean(x['fills_b'] for x in g):>9.2f} "
                  f"{statistics.mean(x['fills_a'] for x in g):>9.2f} "
                  f"{both*100:>6.1f}% {mean:>9.4f} {sum(pnls):>10.2f} "
                  f"{sd:>8.3f} {t:>7.2f}")
    print("\n(PnL is $ per window on a $20/side clip; t-stat > 2 means the mean "
          "beats noise. Negative => adverse selection eats the spread.)")


if __name__ == "__main__":
    main()
