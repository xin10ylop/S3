"""Build the unified windows table for all four families.

Sources: vault windows.parquet (authoritative through 2026-05-12, has chainlink+volume+fee_rate)
+ Telonex markets metadata (fills 1h family entirely, and 5m/15m/4h after 2026-05-12).
Output: data/master/windows_all.parquet
"""
import pandas as pd, numpy as np, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent / "data"
OUT = pathlib.Path(__file__).resolve().parent.parent / "data/master"
OUT.mkdir(parents=True, exist_ok=True)

DUR = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}

# Taker fee-rate schedule per family (from vault fee_rate history + official docs)
FEE_EPOCHS = {
    "5m":  [("2026-02-12", 0.0624), ("2026-03-30", 0.072), ("2026-05-07", 0.07)],
    "15m": [("2025-10-11", 0.0), ("2026-01-05", 0.0624), ("2026-03-30", 0.072), ("2026-05-07", 0.07)],
    "1h":  [("2025-10-11", 0.0), ("2026-01-05", 0.0624), ("2026-03-30", 0.072), ("2026-05-07", 0.07)],
    "4h":  [("2025-10-15", 0.0), ("2026-03-06", 0.0624), ("2026-03-30", 0.072), ("2026-05-07", 0.07)],
}

def fee_for(family, date):
    r = 0.0
    for start, rate in FEE_EPOCHS[family]:
        if date >= start:
            r = rate
    return r

def build_1h_slug_map():
    frames = []
    for f in sorted((ROOT / "data/processed/daily/1h/quotes").glob("*.parquet")):
        d = pd.read_parquet(f, columns=["slug", "wts"])
        frames.append(d.drop_duplicates())
    m = pd.concat(frames).drop_duplicates()
    m = m.groupby("slug", as_index=False).wts.min()
    m.to_parquet(OUT / "slug_wts_1h.parquet", index=False)
    return m

def main():
    w = pd.read_parquet(ROOT / "data/processed/windows.parquet")
    t = pd.read_parquet(ROOT / "data/processed/telonex_btc_markets.parquet")

    t = t[t.outcome_0.eq("Up")].copy()
    is_ud = t.slug.str.startswith("btc-updown-")
    t.loc[is_ud, "family"] = t.loc[is_ud, "slug"].str.extract(r"btc-updown-(\w+)-\d+$")[0].values
    t.loc[~is_ud, "family"] = "1h"
    t = t[t.family.isin(DUR)].copy()
    # wts: updown slugs embed it. 1h-et markets: take (slug, wts) pairs observed in the
    # vault's own 1h quote files (authoritative), join by slug; drop metadata-only slugs
    # (dailies, "-candle" variants etc. never appear in the 1h quote stream).
    slug_wts = pd.read_parquet(OUT / "slug_wts_1h.parquet") if (OUT / "slug_wts_1h.parquet").exists() else build_1h_slug_map()
    m1h = t.family.eq("1h")
    t.loc[m1h, "wts"] = t.loc[m1h, "slug"].map(slug_wts.set_index("slug").wts)
    m_ud = ~m1h
    t.loc[m_ud, "wts"] = t.loc[m_ud, "slug"].str.extract(r"(\d+)$")[0].astype("float").values
    t = t.dropna(subset=["wts"])
    t["wts"] = t.wts.astype("int64")
    t["duration"] = t.family.map(DUR).astype("int32")
    t["date"] = pd.to_datetime(t.wts, unit="s").dt.strftime("%Y-%m-%d")
    t["fee_rate"] = [fee_for(f, d) for f, d in zip(t.family, t.date)]
    tel = t[["slug", "wts", "market_id", "asset_id_0", "asset_id_1", "result_id",
             "status", "settled_at_us", "duration", "family", "date", "fee_rate"]].copy()

    w = w.copy()
    w["src"] = "vault"
    tel["src"] = "telonex"
    key = ["family", "wts"]
    tel_only = tel[~tel.set_index(key).index.isin(w.set_index(key).index)]
    allw = pd.concat([w, tel_only], ignore_index=True).sort_values(["family", "wts"])

    cp_files = sorted((ROOT / "data/processed/daily/crypto_prices").glob("*.parquet"))
    cp = pd.concat([pd.read_parquet(f, columns=["timestamp_us", "price"]) for f in cp_files])
    cp = cp.drop_duplicates("timestamp_us", keep="last").sort_values("timestamp_us")
    ts_arr, px_arr = cp.timestamp_us.values, cp.price.values

    def asof_px(ts_us):
        i = np.searchsorted(ts_arr, ts_us, "right") - 1
        out = np.where(i >= 0, px_arr[np.maximum(i, 0)], np.nan)
        # don't bridge feed outages: reject if last tick is >60s stale
        age = ts_us - ts_arr[np.maximum(i, 0)]
        return np.where((i >= 0) & (age <= 60_000_000), out, np.nan)

    # official resolution = last feed price at or before the boundary timestamp
    # (verified: fixes 9/11 sign mismatches vs the vault's next-tick reconstruction)
    ots = (allw.wts * 1_000_000).astype("int64").values
    cts = ((allw.wts + allw.duration) * 1_000_000).astype("int64").values
    allw["open_oracle"] = asof_px(ots)
    allw["close_oracle"] = asof_px(cts)

    allw["up_won"] = (allw.result_id.astype(str) == "0").astype("int8")
    allw.loc[~allw.status.eq("resolved"), "up_won"] = -1

    allw.to_parquet(OUT / "windows_all.parquet", index=False)
    print(allw.groupby("family").agg(n=("wts", "count"), d0=("date", "min"), d1=("date", "max")))
    print("oracle strike coverage:", allw.open_oracle.notna().mean().round(3))

if __name__ == "__main__":
    main()
