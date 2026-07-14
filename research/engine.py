"""Tick-replay execution engine.

Honest fill simulation against raw quotes/trades/bookcurves:

- Taker buys: cost from the bookcurve row at (signal_time + latency) — the first grid
  row at-or-after that instant (curves are a 250ms grid); falls back to the as-of BBO
  only if fresh and displaying enough size. Fee = fee_rate * p * (1-p), taker only.
- Maker orders: resting limit modeled against the subsequent trade tape. A resting
  buy at price p fills when trades print strictly below p (always count) or at p
  (count after the displayed queue at placement clears; we join the back).
  A maker SELL at price p mirrors on trades at >= p. Maker fee = 0.
- Down-side orders are mirrored onto the Up book: buy Down @ q == sell Up @ 1-q.
- Settlement: winning side pays 1.0, no fee.

All prices/sizes are for the UP token; `side` arguments are 'up'/'dn'.
"""
import numpy as np
import pandas as pd
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent / "data"
DAILY = ROOT / "data/processed/daily"


class DayData:
    """Raw tick data for one family-day, indexed for fast per-window slicing."""

    def __init__(self, family, date):
        self.family, self.date = family, date
        q = pd.read_parquet(DAILY / family / "quotes" / f"{date}.parquet")
        self.q = q.sort_values(["wts", "timestamp_us"]).reset_index(drop=True)
        t_path = DAILY / family / "trades" / f"{date}.parquet"
        t = pd.read_parquet(t_path) if t_path.exists() else None
        self.t = t.sort_values(["wts", "timestamp_us"]).reset_index(drop=True) if t is not None else None
        b_path = DAILY / family / "bookcurves" / f"{date}.parquet"
        b = pd.read_parquet(b_path) if b_path.exists() else None
        self.b = b.sort_values(["wts", "timestamp_us"]).reset_index(drop=True) if b is not None else None

    def _slice(self, df, wts):
        if df is None:
            return None
        lo = np.searchsorted(df.wts.values, wts, "left")
        hi = np.searchsorted(df.wts.values, wts, "right")
        return df.iloc[lo:hi]

    def window(self, wts):
        return (self._slice(self.q, wts), self._slice(self.t, wts), self._slice(self.b, wts))


def taker_buy(book_slice, quote_slice, ts_us, side, notional,
              book_window_us=1_500_000, quote_stale_us=2_000_000):
    """Cost/share to take `notional` USDC of `side` at first book state at-or-after ts_us.

    Uses the first bookcurve grid row in [ts_us, ts_us + book_window_us]. Falls back to
    the as-of BBO only if that quote is fresh (within quote_stale_us) and displays
    enough size. Returns (avg_price, ok, exec_ts) with price in the side's own terms.
    """
    if book_slice is not None and len(book_slice):
        bts = book_slice.timestamp_us.values
        i = np.searchsorted(bts, ts_us, "left")
        if i < len(bts) and bts[i] - ts_us <= book_window_us:
            row = book_slice.iloc[i]
            tier = 50 if notional <= 50 else (200 if notional <= 200 else 1000)
            if side == "up":
                px, ex = row[f"buy_avgpx_{tier}"], row[f"buy_exhaust_{tier}"]
                if np.isfinite(px) and not ex:
                    return float(px), True, int(row.timestamp_us)
            else:
                px, ex = row[f"sell_avgpx_{tier}"], row[f"sell_exhaust_{tier}"]
                if np.isfinite(px) and not ex:
                    return 1.0 - float(px), True, int(row.timestamp_us)
    if quote_slice is not None and len(quote_slice):
        qts = quote_slice.timestamp_us.values
        i = np.searchsorted(qts, ts_us, "right") - 1
        if i >= 0 and ts_us - qts[i] <= quote_stale_us:
            row = quote_slice.iloc[i]
            if side == "up" and np.isfinite(row.ask_price) and row.ask_price * row.ask_size >= notional:
                return float(row.ask_price), True, int(row.timestamp_us)
            if side == "dn" and np.isfinite(row.bid_price) and (1 - row.bid_price) * row.bid_size >= notional:
                return 1.0 - float(row.bid_price), True, int(row.timestamp_us)
    return np.nan, False, 0


def maker_fill(trade_slice, quote_slice, place_ts_us, side, price, size_shares,
               until_ts_us, queue_mode="join_back"):
    """Simulate a resting limit order for `side` at `price` (side's own terms).
    Fills from trades strictly after place_ts_us and at-or-before until_ts_us.
    Returns (filled_shares, fill_ts)."""
    if trade_slice is None or not len(trade_slice):
        return 0.0, None
    up_price = price if side == "up" else 1.0 - price
    tts = trade_slice.timestamp_us.values
    lo = np.searchsorted(tts, place_ts_us, "right")
    hi = np.searchsorted(tts, until_ts_us, "right")
    if lo >= hi:
        return 0.0, None
    px = trade_slice.price.values[lo:hi]
    sz = trade_slice["size"].values[lo:hi]
    ts = tts[lo:hi]
    if side == "up":
        better = px < up_price - 1e-9
        at = np.abs(px - up_price) <= 1e-9
    else:  # maker sell of Up at 1-price: fills on trades at >= that level
        better = px > up_price + 1e-9
        at = np.abs(px - up_price) <= 1e-9
    queue = 0.0
    if queue_mode == "join_back" and quote_slice is not None and len(quote_slice):
        qts = quote_slice.timestamp_us.values
        i = np.searchsorted(qts, place_ts_us, "right") - 1
        if i >= 0:
            row = quote_slice.iloc[i]
            if side == "up" and np.isfinite(row.bid_price) and abs(row.bid_price - up_price) < 1e-9:
                queue = float(row.bid_size)
            elif side == "dn" and np.isfinite(row.ask_price) and abs(row.ask_price - up_price) < 1e-9:
                queue = float(row.ask_size)
    filled = 0.0
    for i in range(len(px)):
        if better[i]:
            take = min(sz[i], size_shares - filled)
            filled += take
        elif at[i]:
            if queue > 0:
                eat = min(queue, sz[i])
                queue -= eat
                rest = sz[i] - eat
            else:
                rest = sz[i]
            take = min(rest, size_shares - filled)
            filled += take
        if filled >= size_shares - 1e-9:
            return size_shares, int(ts[i])
    return filled, (int(ts[-1]) if filled > 0 else None)


def taker_fee(price, fee_rate):
    return fee_rate * price * (1.0 - price)
