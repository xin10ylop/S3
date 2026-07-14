"""Fetch Binance BTCUSDT 1s klines daily zips from data.binance.vision for a date range,
convert to parquet matching the vault's klines_1s schema."""
import io, os, sys, zipfile, datetime as dt, concurrent.futures, pathlib
import requests
import pandas as pd

OUT = pathlib.Path(os.path.join(os.path.dirname(__file__), "..", "data/data/processed/binance/klines_1s")).resolve()
OUT.mkdir(parents=True, exist_ok=True)
COLS = ["open_time","open","high","low","close","volume","close_time","quote_volume",
        "n_trades","taker_buy_base","taker_buy_quote","ignore"]

def fetch_day(day):
    dest = OUT / f"{day}.parquet"
    if dest.exists():
        return f"skip {day}"
    url = f"https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1s/BTCUSDT-1s-{day}.zip"
    r = requests.get(url, timeout=120)
    if r.status_code == 404:
        return f"404 {day}"
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        name = z.namelist()[0]
        df = pd.read_csv(z.open(name), header=None, names=COLS)
    if df.open_time.iloc[0] < 1e14:  # ms -> us
        df["open_time"] = df["open_time"] * 1000
        df["close_time"] = df["close_time"] * 1000
    df = df.drop(columns=["ignore"]).astype({
        "open_time":"int64","open":"float64","high":"float64","low":"float64","close":"float64",
        "volume":"float64","close_time":"int64","quote_volume":"float64","n_trades":"int32",
        "taker_buy_base":"float64","taker_buy_quote":"float64"})
    df.to_parquet(dest, index=False)
    return f"ok {day}"

if __name__ == "__main__":
    start = dt.date.fromisoformat(sys.argv[1])
    end = dt.date.fromisoformat(sys.argv[2])
    days = []
    d = start
    while d <= end:
        days.append(d.isoformat())
        d += dt.timedelta(days=1)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for i, res in enumerate(ex.map(fetch_day, days)):
            if "ok" not in res and "skip" not in res:
                print(res, flush=True)
            if (i+1) % 25 == 0:
                print(f"{i+1}/{len(days)}", flush=True)
    print("done")
