"""Fetch Telonex daily files. Usage:
  python3 fetch_telonex.py crypto_prices 2026-07-08 2026-07-14
Saves under data/data/telonex/<channel>/<date>.parquet
"""
import os, sys, datetime as dt, pathlib, time
import requests

def load_env(path=os.path.join(os.path.dirname(__file__), "..", ".env")):
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v)

load_env()
KEY = os.environ["TELONEX_API_KEY"]
BASE = "https://api.telonex.io/v1/downloads/polymarket"
VERIFY = "/root/.ccr/ca-bundle.crt"

CHANNEL_MAP = {
    "onchain_fills": ("all_onchain_fills", ""),
    "crypto_prices": ("crypto_prices", "asset_id=btcusd"),
}

def fetch_day(channel, day, outdir):
    dest = outdir / f"{day}.parquet"
    if dest.exists() and dest.stat().st_size > 0:
        return "skip"
    ch, qs = CHANNEL_MAP[channel]
    url = f"{BASE}/{ch}/{day}" + (f"?{qs}" if qs else "")
    for attempt in range(5):
        r = requests.get(url, headers={"Authorization": f"Bearer {KEY}"}, verify=VERIFY,
                         allow_redirects=True, timeout=300)
        if r.status_code == 200:
            tmp = dest.with_suffix(".part")
            tmp.write_bytes(r.content)
            tmp.rename(dest)
            rem = r.headers.get("X-Downloads-Remaining", "?")
            return f"ok {len(r.content)/1e6:.1f}MB rem={rem}"
        if r.status_code == 404:
            return "404"
        if r.status_code == 403:
            return f"403 {r.text[:100]}"
        time.sleep(2 ** attempt)
    return f"fail {r.status_code}"

if __name__ == "__main__":
    channel, start, end = sys.argv[1], sys.argv[2], sys.argv[3]
    outdir = pathlib.Path(os.path.join(os.path.dirname(__file__), "..", f"data/data/telonex/{channel}")).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    d = dt.date.fromisoformat(start)
    end_d = dt.date.fromisoformat(end)
    while d <= end_d:
        res = fetch_day(channel, d.isoformat(), outdir)
        print(f"{d} {res}", flush=True)
        if res.startswith("403"):
            print("quota hit, stopping")
            break
        d += dt.timedelta(days=1)
    print("done")
