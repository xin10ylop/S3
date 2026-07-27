"""STAGE 2 -- is buying the favourite +EV after real costs?

Framing. Every hourly quote is a decision point: buy the FAVOURITE side at its
ask and hold to resolution. Buying NO at 0.97 and buying YES at 0.97 are the
same trade, so the two are pooled -- for a quote p we take q = max(p, 1-p) and
ask whether the side priced q actually won.

Costs, all measured rather than assumed:
  * half-spread  -- prices-history is exactly the MID (verified to 1e-9 against
    live books), so lifting the offer costs the measured median half-spread of
    that price band.
  * fee = rate * p * (1-p). Reported at rate 0 (most of the venue is
    taker_base_fee=0) and at 0.05 as the pessimistic case.

Inference. Three levels of correlation, all of which inflate naive CIs:
  1. hourly quotes inside one market share one outcome  -> 1 obs/market/day
  2. markets inside one event are the same bet          -> cluster on event
  3. events resolving together share news               -> reported separately
The headline CI is a cluster bootstrap resampling EVENTS with replacement.

Usage: python3 calib_analyze.py [rate] [min_hold_h]
"""
import json, sys, time, math, random
from collections import defaultdict

SER = "/home/user/S3/data/calib/series.jsonl"
RATE = float(sys.argv[1]) if len(sys.argv) > 1 else 0.0
MINHOLD = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0

# median half-spread by band, measured live over 3,388 book observations
HALF = [(0.50, 0.80, 0.0050), (0.80, 0.90, 0.0050), (0.90, 0.95, 0.0050),
        (0.95, 0.97, 0.0030), (0.97, 0.98, 0.0035), (0.98, 0.99, 0.0020),
        (0.99, 0.995, 0.0010), (0.995, 1.01, 0.0005)]
BANDS = [(0.50, 0.60), (0.60, 0.70), (0.70, 0.80), (0.80, 0.90), (0.90, 0.95),
         (0.95, 0.97), (0.97, 0.98), (0.98, 0.99), (0.99, 0.995), (0.995, 1.00)]
HOLDS = [(0, 2), (2, 12), (12, 48), (48, 168), (168, 1e9)]


def half_spread(q):
    for lo, hi, h in HALF:
        if lo <= q < hi:
            return h
    return 0.005


def band_of(q):
    for lo, hi in BANDS:
        if lo <= q < hi:
            return (lo, hi)
    return None


def load():
    obs = []
    n_mkt = 0
    with open(SER) as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            n_mkt += 1
            ts, ps, won, res = r["t"], r["p"], r["won"], r["ts_res"]
            seen_day = set()
            for t, p in zip(ts, ps):
                if t >= res:
                    continue
                d = int(t // 86400)
                if d in seen_day:            # one observation per market per day
                    continue
                seen_day.add(d)
                if p <= 0.0 or p >= 1.0:
                    continue
                q = p if p >= 0.5 else 1.0 - p
                w = won if p >= 0.5 else 1 - won
                hold = (res - t) / 3600.0
                if hold < MINHOLD:
                    continue
                obs.append((q, w, hold, r["event"], r["slug"]))
    return obs, n_mkt


def econ(q, w, rate):
    """Net return per dollar of buying the favourite as a taker."""
    ask = min(q + half_spread(q), 0.999)
    fee = rate * ask * (1 - ask)
    cost = ask + fee
    return ((1.0 if w else 0.0) - cost) / cost, ask, cost


def boot_ci(by_ev, n=2000, seed=7):
    """Cluster bootstrap over events: resample events, recompute mean return."""
    rng = random.Random(seed)
    evs = list(by_ev.keys())
    if len(evs) < 5:
        return (float("nan"), float("nan"))
    tot_n = sum(len(v) for v in by_ev.values())
    means = []
    for _ in range(n):
        s, c = 0.0, 0
        while c < tot_n:
            v = by_ev[evs[rng.randrange(len(evs))]]
            s += sum(v)
            c += len(v)
        means.append(s / c)
    means.sort()
    return (means[int(0.025 * n)], means[int(0.975 * n)])


def main():
    t0 = time.time()
    obs, n_mkt = load()
    print(f"{n_mkt:,} markets -> {len(obs):,} decision points "
          f"(1/market/day, hold>={MINHOLD}h)   [{time.time()-t0:.0f}s]")
    print(f"fee rate applied: {RATE}   half-spread: measured per band\n")

    print("=== BUY THE FAVOURITE, HOLD TO RESOLUTION ===")
    print(f"{'band':>13} {'n obs':>9} {'events':>7} {'mean q':>7} {'mean ask':>8} "
          f"{'realised':>9} {'edge pp':>8} {'net ret/$':>10} {'95% CI (cluster)':>24} {'med hold':>9}")
    rows = []
    for lo, hi in BANDS:
        g = [o for o in obs if lo <= o[0] < hi]
        if len(g) < 200:
            continue
        by_ev = defaultdict(list)
        rets = []
        for q, w, hold, ev, slug in g:
            r, ask, cost = econ(q, w, RATE)
            by_ev[ev].append(r)
            rets.append((r, q, ask, w, hold))
        n = len(rets)
        mq = sum(x[1] for x in rets) / n
        ma = sum(x[2] for x in rets) / n
        fr = sum(x[3] for x in rets) / n
        mr = sum(x[0] for x in rets) / n
        hold = sorted(x[4] for x in rets)[n // 2]
        clo, chi = boot_ci(by_ev)
        star = "  <<<" if clo > 0 else ""
        print(f"  {lo:.3f}-{hi:<6.3f} {n:>9,} {len(by_ev):>7,} {mq:>7.4f} {ma:>8.4f} "
              f"{fr:>9.4f} {(fr-mq)*100:>+8.2f} {mr*100:>+9.3f}% "
              f"[{clo*100:>+8.3f}%,{chi*100:>+8.3f}%] {hold:>8.0f}h{star}")
        rows.append((lo, hi, n, len(by_ev), mq, ma, fr, mr, clo, chi, hold))

    print("\n=== WHERE DOES THE EDGE LIVE? net return/$ by holding period ===")
    print(f"{'band':>13} " + " ".join(f"{a}-{int(b) if b<1e8 else 'inf'}h".rjust(12)
                                      for a, b in HOLDS))
    for lo, hi in BANDS:
        g = [o for o in obs if lo <= o[0] < hi]
        if len(g) < 200:
            continue
        cells = []
        for a, b in HOLDS:
            gg = [o for o in g if a <= o[2] < b]
            if len(gg) < 50:
                cells.append("        -   ")
                continue
            m = sum(econ(q, w, RATE)[0] for q, w, _, _, _ in gg) / len(gg)
            cells.append(f"{m*100:>+9.3f}% n={len(gg)//1000}k" if len(gg) >= 1000
                         else f"{m*100:>+9.3f}%   ")
        print(f"  {lo:.3f}-{hi:<6.3f} " + " ".join(c.rjust(12) for c in cells))

    json.dump([{"lo": r[0], "hi": r[1], "n": r[2], "ev": r[3], "q": r[4], "ask": r[5],
                "realised": r[6], "ret": r[7], "ci_lo": r[8], "ci_hi": r[9],
                "hold_h": r[10]} for r in rows],
              open("/tmp/claude-0/-home-user-S3/8087631e-229b-5943-8560-8dda6ec827f4/scratchpad/calib_rows.json", "w"))
    print(f"\ndone in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
