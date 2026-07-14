"""Build per-window master feature tables for each family.

One row per market window: quote/book/trade-flow snapshots at fixed times relative
to window open and close, Binance spot features, oracle strike/close, outcome.

Usage: python3 build_master.py 5m [--workers 3]
Output: data/master/{family}/{date}.parquet, then concat to data/master/{family}_master.parquet
"""
import sys, pathlib, warnings
import numpy as np
import pandas as pd
from concurrent.futures import ProcessPoolExecutor

warnings.filterwarnings("ignore")
ROOT = pathlib.Path(__file__).resolve().parent.parent / "data"
DAILY = ROOT / "data/processed/daily"
MASTER = ROOT / "master"

T_OPEN = [-300, -120, -60, -30, -20, -10, -5, -2, 0, 2, 5, 10, 20, 30, 60]
T_CLOSE = [-120, -60, -30, -10, -5, -2, 0]
BOOK_OPEN = [-30, -10, -2, 0, 10, 30]
BOOK_CLOSE = [-60, -30, -10, -2, 0]
FLOW_LOOKBACKS = [30, 60, 120]
BN_RETS = [5, 15, 30, 60, 120, 300]

DUR = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}


def day_paths(family, date):
    return {
        "quotes": DAILY / family / "quotes" / f"{date}.parquet",
        "trades": DAILY / family / "trades" / f"{date}.parquet",
        "books": DAILY / family / "bookcurves" / f"{date}.parquet",
    }


def load_family_day(family, date):
    """Return quotes, trades, books dataframes containing all rows for windows whose
    window-date == date. For 1h (obs-date grouped files), stitch date-1..date+1."""
    if family != "1h":
        p = day_paths(family, date)
        q = pd.read_parquet(p["quotes"]) if p["quotes"].exists() else None
        t = pd.read_parquet(p["trades"]) if p["trades"].exists() else None
        b = pd.read_parquet(p["books"]) if p["books"].exists() else None
        return q, t, b
    dt0 = pd.Timestamp(date)
    frames_q, frames_t = [], []
    for d in [dt0 - pd.Timedelta(days=1), dt0, dt0 + pd.Timedelta(days=1)]:
        ds = d.strftime("%Y-%m-%d")
        p = day_paths("1h", ds)
        if p["quotes"].exists():
            frames_q.append(pd.read_parquet(p["quotes"]))
        if p["trades"].exists():
            frames_t.append(pd.read_parquet(p["trades"]))
    if not frames_q:
        return None, None, None
    q = pd.concat(frames_q, ignore_index=True)
    t = pd.concat(frames_t, ignore_index=True) if frames_t else None
    wdate = pd.to_datetime(q.wts, unit="s").dt.strftime("%Y-%m-%d")
    q = q[wdate == date]
    if t is not None and len(t):
        t = t[pd.to_datetime(t.wts, unit="s").dt.strftime("%Y-%m-%d") == date]
    return q, t, None


def load_binance(date):
    d0 = pd.Timestamp(date)
    frames = []
    for d in [d0 - pd.Timedelta(days=1), d0]:
        f = ROOT / "data/processed/binance/klines_1s" / (d.strftime("%Y-%m-%d") + ".parquet")
        if f.exists():
            frames.append(pd.read_parquet(f, columns=[
                "open_time", "close", "volume", "quote_volume", "taker_buy_quote"]))
    if not frames:
        return None
    k = pd.concat(frames, ignore_index=True)
    k["sec"] = (k.open_time // 1_000_000).astype("int64")
    k = k.drop_duplicates("sec").set_index("sec")
    full = np.arange(k.index.min(), k.index.max() + 1)
    k = k.reindex(full)
    k["close"] = k["close"].ffill()
    for c in ["volume", "quote_volume", "taker_buy_quote"]:
        k[c] = k[c].fillna(0.0)
    k["logc"] = np.log(k["close"])
    return k


def binance_feats(k, at_secs, prefix):
    out = {}
    idx = np.asarray(at_secs, dtype="int64")
    base = k.index[0]
    pos = np.clip(idx - base, 0, len(k) - 1)
    close = k["close"].values
    logc = k["logc"].values
    out[f"{prefix}_spot"] = close[pos]
    for r in BN_RETS:
        p2 = np.clip(pos - r, 0, len(k) - 1)
        out[f"{prefix}_ret{r}"] = logc[pos] - logc[p2]
    lr = np.diff(logc, prepend=logc[0])
    lr2 = lr * lr
    c2 = np.cumsum(lr2)
    for w in [60, 300]:
        p2 = np.clip(pos - w, 0, len(k) - 1)
        out[f"{prefix}_rv{w}"] = np.sqrt(np.maximum(c2[pos] - c2[p2], 0))
    qv = np.cumsum(k["quote_volume"].values)
    tb = np.cumsum(k["taker_buy_quote"].values)
    for w in [60]:
        p2 = np.clip(pos - w, 0, len(k) - 1)
        dv = qv[pos] - qv[p2]
        out[f"{prefix}_tbs{w}"] = np.where(dv > 0, (tb[pos] - tb[p2]) / np.maximum(dv, 1e-9), 0.5)
        out[f"{prefix}_vol{w}"] = dv
    return out


BOOK_COLS = ["bid_p0", "bid_s0", "ask_p0", "ask_s0",
             "buy_avgpx_200", "buy_avgpx_1000", "sell_avgpx_200", "sell_avgpx_1000",
             "bid_depth_5c", "ask_depth_5c"]


def process_day(family, date):
    outdir = MASTER / family
    outdir.mkdir(parents=True, exist_ok=True)
    outfile = outdir / f"{date}.parquet"
    if outfile.exists():
        return f"skip {date}"
    q, t, b = load_family_day(family, date)
    if q is None or not len(q):
        return f"nodata {date}"
    wins = pd.read_parquet(MASTER / "windows_all.parquet")
    wins = wins[(wins.family == family) & (wins.date == date)]
    if not len(wins):
        return f"nowins {date}"
    k = load_binance(date)
    dur = DUR[family]

    q = q.sort_values(["wts", "timestamp_us"])
    t = t.sort_values(["wts", "timestamp_us"]) if t is not None and len(t) else None
    b = b.sort_values(["wts", "timestamp_us"]) if b is not None and len(b) else None

    q_wts = q.wts.values
    q_ts = q.timestamp_us.values
    q_bid, q_ask = q.bid_price.values, q.ask_price.values
    q_bs, q_as = q.bid_size.values, q.ask_size.values

    rows = []
    for w in wins.itertuples():
        wts = int(w.wts)
        row = {"wts": wts, "date": date, "up_won": w.up_won, "fee_rate": w.fee_rate,
               "strike_oracle": w.open_oracle, "close_oracle": w.close_oracle,
               "volume_usdc": getattr(w, "volume_usdc", np.nan)}
        lo, hi = np.searchsorted(q_wts, wts, "left"), np.searchsorted(q_wts, wts, "right")
        if hi - lo < 2:
            continue
        ts = q_ts[lo:hi]
        open_us, close_us = wts * 1_000_000, (wts + dur) * 1_000_000

        for rel, base_us, tag in (
            [(x, open_us, f"o{x}") for x in T_OPEN] + [(x, close_us, f"c{x}") for x in T_CLOSE]
        ):
            i = np.searchsorted(ts, base_us + rel * 1_000_000, "right") - 1
            if i < 0:
                continue
            j = lo + i
            row[f"bid_{tag}"] = q_bid[j]
            row[f"ask_{tag}"] = q_ask[j]
            row[f"bsz_{tag}"] = q_bs[j]
            row[f"asz_{tag}"] = q_as[j]

        if t is not None:
            tlo, thi = np.searchsorted(t.wts.values, wts, "left"), np.searchsorted(t.wts.values, wts, "right")
            tt = t.iloc[tlo:thi]
            tts = tt.timestamp_us.values
            sgn = np.where(tt["side"].values == "buy", 1.0, -1.0)
            sz = tt["size"].values
            px = tt.price.values
            csf = np.cumsum(sgn * sz)
            cn = np.cumsum(np.ones_like(sz))
            cv = np.cumsum(sz * px)
            for base_us, tag in [(open_us, "o0"), (close_us, "c0")]:
                i1 = np.searchsorted(tts, base_us, "right") - 1
                for lb in FLOW_LOOKBACKS:
                    i0 = np.searchsorted(tts, base_us - lb * 1_000_000, "right") - 1
                    if i1 >= 0:
                        row[f"flow_{tag}_{lb}"] = csf[i1] - (csf[i0] if i0 >= 0 else 0)
                        row[f"ntr_{tag}_{lb}"] = cn[i1] - (cn[i0] if i0 >= 0 else 0)
                        row[f"ntl_{tag}_{lb}"] = cv[i1] - (cv[i0] if i0 >= 0 else 0)
            row["win_volume_sh"] = sz[(tts >= open_us) & (tts < close_us)].sum()
            row["win_ntrades"] = ((tts >= open_us) & (tts < close_us)).sum()

        if b is not None:
            blo, bhi = np.searchsorted(b.wts.values, wts, "left"), np.searchsorted(b.wts.values, wts, "right")
            bts = b.timestamp_us.values[blo:bhi]
            if len(bts):
                for rel, base_us, tag in (
                    [(x, open_us, f"o{x}") for x in BOOK_OPEN] + [(x, close_us, f"c{x}") for x in BOOK_CLOSE]
                ):
                    i = np.searchsorted(bts, base_us + rel * 1_000_000, "right") - 1
                    if i < 0:
                        continue
                    j = blo + i
                    for c in BOOK_COLS:
                        row[f"{c}_{tag}"] = b[c].values[j]

        if k is not None:
            secs_o = [wts + x for x in [-300, -120, -60, -30, -10, -5, -2, 0]]
            f = binance_feats(k, secs_o, "bn")
            for kk, vv in f.items():
                for xi, x in enumerate([-300, -120, -60, -30, -10, -5, -2, 0]):
                    row[f"{kk}_o{x}"] = vv[xi]
            secs_c = [wts + dur + x for x in [-120, -60, -30, -10, -5, -2, 0]]
            f = binance_feats(k, secs_c, "bn")
            for kk, vv in f.items():
                for xi, x in enumerate([-120, -60, -30, -10, -5, -2, 0]):
                    row[f"{kk}_c{x}"] = vv[xi]
        rows.append(row)

    if not rows:
        return f"empty {date}"
    df = pd.DataFrame(rows)
    df.to_parquet(outfile, index=False)
    return f"ok {date} {len(df)}"


def main():
    family = sys.argv[1]
    workers = 3
    dates = sorted(p.stem for p in (DAILY / family / "quotes").glob("*.parquet"))
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for res in ex.map(process_day, [family] * len(dates), dates):
            print(res, flush=True)
    files = sorted((MASTER / family).glob("*.parquet"))
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df = df.sort_values("wts")
    df.to_parquet(MASTER / f"{family}_master.parquet", index=False)
    print("master:", df.shape)


if __name__ == "__main__":
    main()
