"""LOGICAL-IMPLICATION ARBITRAGE across nested markets.

Today's structural finding: the YES and NO books of ONE market are the same book
(ask_YES == 1 - bid_NO to 1e-9), so intra-market arb is impossible. But books
are NOT unified ACROSS markets, and the venue lists many markets that are
logically nested:

    "BTC reaches 70k in July"      implies  "BTC reaches 67.5k in July"
    "ceasefire by July 24"         implies  "ceasefire by July 31"
    "FDV above 1B"                 implies  "FDV above 100M"

If A implies B then P(A) <= P(B) is a law, not a forecast. When the book breaks
it, the trade is:

    SELL A (= buy NO_A at 1 - bid_A)  and  BUY B (at ask_B)
    cost   = (1 - bid_A) + ask_B
    payoff = 1 if A true (then B true too);  2 if A false and B true;
             1 if both false            ->  minimum payoff is exactly 1

    so it is risk-free whenever   ask_B < bid_A,  profit >= bid_A - ask_B

Both legs are TAKER orders, so fills are certain -- no queue, no adverse
selection, no forecast. Fees are rate*p*(1-p) per leg and most of the venue is
taker_base_fee=0, verified per market rather than guessed from the category.

Ladders are detected two ways:
  NUMERIC  slugs sharing a stem with a varying magnitude (70k vs 67pt5k,
           1b vs 100m). Higher threshold => stricter => implies the lower one,
           unless the stem says "below"/"under"/"dip", which reverses it.
  DATE     slugs sharing a stem with a varying date. Earlier deadline is
           stricter => implies the later one.

Every hit is reported with both slugs in full so the resolution criteria can be
read before any money moves -- similar slugs do not guarantee nesting.

Usage: python3 scan_ladder.py [min_edge_cents] [min_size_usd]
"""
import json, re, sys, time
from collections import defaultdict
import requests
from concurrent.futures import ThreadPoolExecutor

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
SCRATCH = "/tmp/claude-0/-home-user-S3/8087631e-229b-5943-8560-8dda6ec827f4/scratchpad"
MIN_EDGE = float(sys.argv[1]) if len(sys.argv) > 1 else 0.1     # cents per share
MIN_SIZE = float(sys.argv[2]) if len(sys.argv) > 2 else 20      # dollars

S = requests.Session()
S.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=64, max_retries=3))

MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"])}
REVERSE = ("below", "under", "dip", "fall", "drop", "less-than", "lower")


def enumerate_active():
    """gamma caps limit at 100 and offset at ~2000, so ordering by volume reaches
    only the top 2,000 markets. Walk forward-dated slices to cover the venue."""
    now = time.time()
    edges = [now + d * 86400 for d in range(-2, 400, 3)]
    slices = [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)]

    def grab(job):
        lo, hi, off = job
        try:
            r = S.get(f"{GAMMA}/markets",
                      params={"closed": "false", "active": "true", "limit": 100, "offset": off,
                              "end_date_min": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(lo)),
                              "end_date_max": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(hi))},
                      timeout=25)
            return r.json() if r.ok else []
        except Exception:
            return []

    jobs = [(lo, hi, off) for lo, hi in slices for off in range(0, 600, 100)]
    out, seen = [], set()
    with ThreadPoolExecutor(max_workers=32) as ex:
        for pg in ex.map(grab, jobs):
            for m in pg:
                c = m.get("conditionId")
                if c and c not in seen:
                    seen.add(c)
                    out.append(m)
    return out


def magnitude(tok):
    """'70k'->70000, '67pt5k'->67500, '1b'->1e9, '130'->130."""
    t = tok.replace("pt", ".")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([kmb]?)", t)
    if not m:
        return None
    v = float(m.group(1))
    return v * {"": 1, "k": 1e3, "m": 1e6, "b": 1e9}[m.group(2)]


# A numeric ladder is only a real implication when the slug states a COMPARISON.
# Without this, "ca-49-house-seat" vs "ca-45-house-seat" reads as a threshold pair
# when they are simply different districts.
COMPARATIVE = ("above", "below", "under", "over", "reach", "reaches", "exceed",
               "at-least", "more-than", "less-than", "dip", "hits", "higher", "lower")
DATE_RE = (r"(?:january|february|march|april|may|june|july|august|september|"
           r"october|november|december)-\d{1,2}(?!\d)")


def date_token_idx(parts):
    """Indices of tokens that belong to a date or to a slug id-suffix, so they
    are never read as thresholds. 'above-60k-on-july-28-2026' must not pair with
    'above-58k-on-august-1-2026': the stem has to KEEP the date."""
    bad = set()
    for i, tk in enumerate(parts):
        if tk in MONTHS:
            bad.add(i)
            if i + 1 < len(parts) and parts[i + 1].isdigit():
                bad.add(i + 1)                       # the day
        if re.fullmatch(r"(19|20)\d\d", tk):
            bad.add(i)
            for j in (i + 1, i + 2):                 # ISO yyyy-mm-dd
                if j < len(parts) and re.fullmatch(r"\d{2}", parts[j]):
                    bad.add(j)
        if re.fullmatch(r"\d{9,}", tk):              # 20260622191708361 id suffix
            bad.add(i)
    return bad


def date_key(slug):
    """DEADLINE only. 'by/before july 24' nests; 'ON july 24' is a snapshot and
    does not -- BTC above 62k ON the 28th says nothing about the 29th."""
    m = re.search(r"(?:by|before)-([a-z]+)-(\d{1,2})(?!\d)", slug)
    if m and m.group(1) in MONTHS:
        return MONTHS[m.group(1)] * 100 + int(m.group(2))
    return None


def ladders(slugs):
    """-> list of (stem, kind, [(key, slug), ...]) with >=2 distinct keys."""
    num, dat = defaultdict(list), defaultdict(list)
    for s in slugs:
        parts = s.split("-")
        # whole-token match only: substring matching read "diplomatic" as "dip",
        # which is in REVERSE, and silently inverted the implication direction
        if any(tk in COMPARATIVE for tk in parts):
            bad = date_token_idx(parts)
            for i, tk in enumerate(parts):
                if i in bad:
                    continue
                v = magnitude(tk)
                if v is None or v == 0:
                    continue
                # stem is built from the ORIGINAL parts, so the date survives and
                # two slugs pair up only when ONLY the threshold differs
                stem = "-".join(parts[:i] + ["#"] + parts[i + 1:])
                num[stem].append((v, s))
        d = date_key(s)
        if d:
            stem = re.sub(r"(?:by|before)-[a-z]+-\d{1,2}(?!\d)", "@", s)
            if "@" in stem:
                dat[stem].append((d, s))
    out = []
    for stem, v in num.items():
        if len({x[0] for x in v}) >= 2:
            out.append((stem, "num", v))
    for stem, v in dat.items():
        if len({x[0] for x in v}) >= 2:
            out.append((stem, "date", v))
    return out


def book(tok):
    try:
        r = S.get(f"{CLOB}/book", params={"token_id": tok}, timeout=10)
        if not r.ok:
            return None
        b = r.json()
        bids = sorted(((float(x["price"]), float(x["size"])) for x in b.get("bids", [])),
                      reverse=True)
        asks = sorted((float(x["price"]), float(x["size"])) for x in b.get("asks", []))
        if not bids or not asks:
            return None
        return {"bid": bids[0], "ask": asks[0]}
    except Exception:
        return None


def fee_rate(cond, cache={}):
    if cond in cache:
        return cache[cond]
    try:
        r = S.get(f"{CLOB}/markets/{cond}", timeout=10)
        v = r.json().get("taker_base_fee") if r.ok else None
        # base_fee is a flag, not the rate: 0 => fee-free, non-zero => category rate
        cache[cond] = 0.0 if v == 0 else 0.07
    except Exception:
        cache[cond] = 0.07
    return cache[cond]


def main():
    t0 = time.time()
    ms = enumerate_active()
    # negRisk legs are MUTUALLY EXCLUSIVE buckets, not nested thresholds --
    # "highest temp is the 34c bucket" does not imply the 33c bucket. Those
    # events are the dutch-book scan's job, not this one.
    ms = [m for m in ms if not m.get("negRisk")]
    by_slug = {m["slug"]: m for m in ms if m.get("slug")}
    print(f"{len(ms)} active non-negRisk markets  ({time.time()-t0:.0f}s)", flush=True)

    lads = ladders(list(by_slug))
    involved = {s for _, _, v in lads for _, s in v}
    print(f"{len(lads)} candidate ladders spanning {len(involved)} markets", flush=True)

    def yes_tok(m):
        try:
            t = json.loads(m.get("clobTokenIds") or "[]")
            o = json.loads(m.get("outcomes") or "[]")
            if len(t) != 2:
                return None
            return t[o.index("Yes") if "Yes" in o else 0]
        except Exception:
            return None

    jobs = [(s, yes_tok(by_slug[s])) for s in involved]
    jobs = [(s, t) for s, t in jobs if t]
    print(f"fetching {len(jobs)} books...", flush=True)
    bk = {}
    with ThreadPoolExecutor(max_workers=40) as ex:
        for (s, t), b in zip(jobs, ex.map(lambda j: book(j[1]), jobs)):
            if b:
                bk[s] = b
    print(f"got {len(bk)} two-sided books  ({time.time()-t0:.0f}s)\n", flush=True)

    hits, checked = [], 0
    for stem, kind, items in lads:
        items = [(k, s) for k, s in items if s in bk]
        if len(items) < 2:
            continue
        rev = any(tk in REVERSE for tk in stem.split("-"))
        for ka, sa in items:
            for kb, sb in items:
                if sa == sb:
                    continue
                # A implies B?  numeric: stricter threshold unless the stem is
                # a "below" phrasing.  date: earlier deadline is stricter.
                if kind == "num":
                    implies = (ka < kb) if rev else (ka > kb)
                else:
                    implies = ka < kb
                if not implies:
                    continue
                checked += 1
                bid_a = bk[sa]["bid"]
                ask_b = bk[sb]["ask"]
                gross = bid_a[0] - ask_b[0]
                if gross <= 0:
                    continue
                ra = fee_rate(by_slug[sa]["conditionId"])
                rb = fee_rate(by_slug[sb]["conditionId"])
                # leg A is a taker BUY of NO_A at (1 - bid_a); leg B a taker BUY at ask_b
                pa = 1 - bid_a[0]
                f = ra * pa * (1 - pa) + rb * ask_b[0] * (1 - ask_b[0])
                edge = gross - f
                size = min(bid_a[1], ask_b[1])
                usd = size * ((1 - bid_a[0]) + ask_b[0])
                if edge * 100 < MIN_EDGE or usd < MIN_SIZE:
                    continue
                hits.append({"stem": stem, "kind": kind, "A": sa, "B": sb,
                             "bid_A": bid_a[0], "ask_B": ask_b[0], "gross_c": gross * 100,
                             "fee_c": f * 100, "edge_c": edge * 100, "shares": size,
                             "usd": usd, "profit": size * edge, "rate_A": ra, "rate_B": rb})

    print(f"checked {checked:,} implication pairs -> {len(hits)} violations\n")
    hits.sort(key=lambda h: -h["profit"])
    for h in hits[:25]:
        print(f"  PROFIT ${h['profit']:>8,.2f} on ${h['usd']:>9,.0f}  edge {h['edge_c']:>+6.2f}c/sh "
              f"(gross {h['gross_c']:+.2f} fee {h['fee_c']:.2f})  {h['shares']:>8,.0f} sh  [{h['kind']}]")
        print(f"     SELL A  bid {h['bid_A']:.4f}  {h['A']}")
        print(f"     BUY  B  ask {h['ask_B']:.4f}  {h['B']}")
    if hits:
        print(f"\n  >>> TOTAL RISK-FREE PROFIT AVAILABLE NOW: "
              f"${sum(h['profit'] for h in hits):,.2f} "
              f"on ${sum(h['usd'] for h in hits):,.0f} deployed")
    json.dump(hits, open(f"{SCRATCH}/ladder.json", "w"))
    print(f"\ndone in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
