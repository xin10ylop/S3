"""Fetch per-market Telonex quotes+trades for btc-updown-5m windows in a date range.
Usage: python3 fetch_telonex_market_days.py 2026-07-08 2026-07-14
Writes vault-compatible daily files under data/data/telonex/5m/{quotes,trades}/DATE.parquet
"""
import os, sys, io, datetime as dt, pathlib, concurrent.futures
import requests
import pandas as pd

def load_env(path=os.path.join(os.path.dirname(__file__), "..", ".env")):
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v)

load_env()
KEY = os.environ["TELONEX_API_KEY"]
VERIFY = "/root/.ccr/ca-bundle.crt"
ROOT = pathlib.Path(os.path.join(os.path.dirname(__file__), "..", "data/data/telonex/5m")).resolve()

def fetch_market(args):
    channel, date, slug = args
    url = f"https://api.telonex.io/v1/downloads/polymarket/{channel}/{date}?slug={slug}&outcome=Up"
    try:
        r = requests.get(url, headers={"Authorization": f"Bearer {KEY}"}, verify=VERIFY,
                         allow_redirects=True, timeout=120)
        if r.status_code == 200:
            df = pd.read_parquet(io.BytesIO(r.content))
            return (slug, df)
        return (slug, None) if r.status_code == 404 else (slug, f"err{r.status_code}")
    except Exception as e:
        return (slug, f"exc {e}")

def main():
    start, end = sys.argv[1], sys.argv[2]
    wins = pd.read_parquet(pathlib.Path(__file__).parent.parent / "data/data/processed/telonex_btc_markets.parquet")
    wins = wins[wins.slug.str.startswith("btc-updown-5m-")].copy()
    wins["wts"] = wins.slug.str.extract(r"(\d+)$").astype(int)
    wins["date"] = pd.to_datetime(wins.wts, unit="s").dt.strftime("%Y-%m-%d")
    d = dt.date.fromisoformat(start)
    while d <= dt.date.fromisoformat(end):
        ds = d.isoformat()
        day = wins[wins.date == ds]
        for channel in ["quotes", "trades"]:
            outdir = ROOT / channel
            outdir.mkdir(parents=True, exist_ok=True)
            dest = outdir / f"{ds}.parquet"
            if dest.exists():
                print(f"skip {channel} {ds}", flush=True)
                continue
            jobs = [(channel, ds, s) for s in day.slug]
            frames, errs = [], 0
            with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:
                for slug, res in ex.map(fetch_market, jobs):
                    if isinstance(res, pd.DataFrame):
                        res["wts"] = int(slug.rsplit("-", 1)[1])
                        frames.append(res)
                    elif isinstance(res, str):
                        errs += 1
            if frames:
                df = pd.concat(frames, ignore_index=True)
                keep = ([c for c in ["timestamp_us", "local_timestamp_us", "bid_price", "bid_size",
                                     "ask_price", "ask_size", "wts"] if c in df]
                        if channel == "quotes" else
                        [c for c in ["timestamp_us", "local_timestamp_us", "price", "size", "side", "wts"] if c in df])
                df[keep].sort_values(["wts", "timestamp_us"]).to_parquet(dest, index=False)
            print(f"{channel} {ds}: {len(frames)} markets, {errs} errors", flush=True)
        d += dt.timedelta(days=1)
    print("done")

if __name__ == "__main__":
    main()
