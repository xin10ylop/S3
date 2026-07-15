#!/usr/bin/env python3
"""EFC-M bot — Early-Favorite Continuation (maker) on Polymarket btc-updown-5m.

Strategy (locked config, validated on 4 independent periods Feb-Jul 2026):
  At window open+60s, if the market's own mid prices one side in [0.85, 0.97],
  rest a maker limit BUY on that favorite at its current best bid.
  Cancel at open+90s if unfilled. Hold fills to settlement. No sell leg.

Modes:
  paper — identical decision path; fills simulated against the LIVE public trade
          tape with the same conservative queue model used in backtests
          (join the back of the displayed queue; strictly-below prints always fill us,
          at-price prints only after the displayed queue clears).
  live  — same decision path; real GTC orders via py-clob-client. Requires
          POLY_PRIVATE_KEY (+ optional POLY_FUNDER/POLY_SIG_TYPE) in the environment
          AND config "live_armed": "I-UNDERSTAND-REAL-MONEY".

Run: python3 efc_bot.py config.json
Journal: JSONL in data_dir/journal-YYYY-MM-DD.jsonl ; state in data_dir/state.json
"""
import json
import logging
import math
import os
import pathlib
import signal as os_signal
import sys
import threading
import time
from dataclasses import dataclass, field

import requests

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA_API = "https://data-api.polymarket.com"

log = logging.getLogger("efc")


# ----------------------------------------------------------------------------- config
@dataclass
class Config:
    mode: str = "paper"                  # paper | live
    live_armed: str = ""                 # must equal "I-UNDERSTAND-REAL-MONEY" for live
    data_dir: str = "./efc_data"

    # strategy (LOCKED — validated values; change only with a new validation cycle)
    band_lo: float = 0.85
    band_hi: float = 0.97
    t_signal: float = 60.0               # seconds after window open
    t_cancel: float = 90.0               # cancel unfilled order at open + t_cancel
    max_spread: float = 0.05             # skip if book spread wider than this

    # sizing
    bankroll: float = 100.0              # paper bankroll; live: informational floor
    kelly_cap_frac: float = 0.10         # clip <= 10% of bankroll (≈ half-Kelly)
    market_cap_usd: float = 100.0        # hard ceiling per clip; raise manually
    adaptive_sizing: bool = True         # scale with observed qualifying flow
    flow_frac: float = 0.30              # clip <= 30% of trailing median qualifying flow
    flow_window: int = 50                # signals in the trailing flow estimate
    min_clip_usd: float = 5.0

    # ops
    poll_book_ms: int = 400
    settle_grace_s: float = 240.0        # wait after close for resolution
    kill_trailing_n: int = 100
    kill_min_ev: float = 0.0             # pause if trailing mean pnl/share < this
    max_clock_skew_s: float = 1.5

    @staticmethod
    def load(path):
        cfg = Config()
        with open(path) as f:
            for k, v in json.load(f).items():
                if hasattr(cfg, k):
                    setattr(cfg, k, v)
        return cfg


# ----------------------------------------------------------------------------- io
def http():
    s = requests.Session()
    a = requests.adapters.HTTPAdapter(max_retries=requests.adapters.Retry(
        total=3, backoff_factor=0.3, status_forcelist=[429, 500, 502, 503, 504]))
    s.mount("https://", a)
    return s


class Journal:
    def __init__(self, data_dir):
        self.dir = pathlib.Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def write(self, rec):
        rec = dict(rec)
        rec.setdefault("ts", time.time())
        day = time.strftime("%Y-%m-%d", time.gmtime(rec["ts"]))
        with self.lock:
            with open(self.dir / f"journal-{day}.jsonl", "a") as f:
                f.write(json.dumps(rec, separators=(",", ":")) + "\n")

    def load_state(self):
        p = self.dir / "state.json"
        if p.exists():
            return json.loads(p.read_text())
        return {"bankroll": None, "trailing_pnl_sh": [], "trailing_flow": [], "paused": False}

    def save_state(self, st):
        p = self.dir / "state.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(st))
        tmp.replace(p)


# ----------------------------------------------------------------------------- market data
class Feed:
    def __init__(self, session):
        self.s = session

    def clock_skew(self):
        t0 = time.time()
        r = self.s.get(f"{CLOB}/time", timeout=5)
        t1 = time.time()
        server = float(r.text.strip().strip('"'))
        return server - (t0 + t1) / 2.0

    def discover(self, wts):
        slug = f"btc-updown-5m-{wts}"
        r = self.s.get(f"{GAMMA}/markets", params={"slug": slug}, timeout=10)
        r.raise_for_status()
        ms = r.json()
        if not ms:
            return None
        m = ms[0]
        toks = json.loads(m["clobTokenIds"])
        outs = json.loads(m["outcomes"])
        up_idx = outs.index("Up")
        return {
            "slug": slug, "wts": wts, "condition_id": m["conditionId"],
            "token_up": toks[up_idx], "token_dn": toks[1 - up_idx],
            "closed": m.get("closed", False),
        }

    def book(self, token_id):
        r = self.s.get(f"{CLOB}/book", params={"token_id": token_id}, timeout=5)
        r.raise_for_status()
        b = r.json()
        bids = [(float(x["price"]), float(x["size"])) for x in b.get("bids", [])]
        asks = [(float(x["price"]), float(x["size"])) for x in b.get("asks", [])]
        bb = max(bids, key=lambda x: x[0]) if bids else (None, 0.0)
        ba = min(asks, key=lambda x: x[0]) if asks else (None, 0.0)
        return {"bid": bb[0], "bid_sz": bb[1], "ask": ba[0], "ask_sz": ba[1],
                "tick": float(b.get("tick_size", 0.01)),
                "min_size": float(b.get("min_order_size", 5)),
                "raw_ts": b.get("timestamp")}

    def trades(self, condition_id, after_ts):
        r = self.s.get(f"{DATA_API}/trades",
                       params={"market": condition_id, "limit": 500}, timeout=8)
        r.raise_for_status()
        out = []
        for t in r.json():
            if t.get("timestamp", 0) >= after_ts - 1:
                out.append(t)
        return out

    def winner(self, condition_id):
        r = self.s.get(f"{CLOB}/markets/{condition_id}", timeout=8)
        r.raise_for_status()
        m = r.json()
        for tok in m.get("tokens", []):
            if tok.get("winner"):
                return tok["outcome"]
        return None


# ----------------------------------------------------------------------------- executors
class PaperExecutor:
    """Simulates the resting bid against the live public tape, using the SAME
    conservative rules as the research backtests."""

    def __init__(self, feed, journal):
        self.feed = feed
        self.journal = journal

    def run_order(self, mkt, fav, level, queue_shares, shares, cancel_at, cfg):
        """Poll the tape until cancel_at; return (filled_shares, qualifying_flow_shares)."""
        cond = mkt["condition_id"]
        placed_ts = time.time()
        seen = set()
        queue = queue_shares
        filled = 0.0
        qual_total = 0.0
        while time.time() < cancel_at:
            time.sleep(1.0)
            try:
                trades = self.feed.trades(cond, placed_ts)
            except Exception as e:
                log.warning("tape poll error: %s", e)
                continue
            for t in sorted(trades, key=lambda x: x.get("timestamp", 0)):
                key = (t.get("transactionHash"), t.get("timestamp"), t.get("price"),
                       t.get("size"), t.get("outcome"), t.get("side"))
                if key in seen:
                    continue
                seen.add(key)
                if t.get("timestamp", 0) < placed_ts:
                    continue
                px = float(t["price"])
                up_px = px if t.get("outcome") == "Up" else 1.0 - px
                fav_px = up_px if fav == "Up" else 1.0 - up_px
                sz = float(t["size"])
                if fav_px < level - 1e-9:
                    take = min(sz, shares - filled)
                    filled += take
                    qual_total += sz
                elif abs(fav_px - level) <= 1e-9:
                    if queue > 0:
                        eat = min(queue, sz)
                        queue -= eat
                        rest = sz - eat
                    else:
                        rest = sz
                    take = min(rest, shares - filled)
                    filled += take
                    qual_total += rest
                if filled >= shares - 1e-9:
                    self.journal.write({"type": "fill", "slug": mkt["slug"], "filled": shares,
                                        "level": level, "mode": "paper"})
                    return shares, qual_total
        return filled, qual_total


class LiveExecutor:
    """Real orders via py-clob-client. Same decision path as paper."""

    def __init__(self, feed, journal, cfg):
        from py_clob_client.client import ClobClient          # noqa: import guarded by mode
        from py_clob_client.clob_types import OrderArgs, OrderType, PartialCreateOrderOptions
        self.OrderArgs, self.OrderType, self.Opts = OrderArgs, OrderType, PartialCreateOrderOptions
        key = os.environ["POLY_PRIVATE_KEY"]
        funder = os.environ.get("POLY_FUNDER")
        sig_type = int(os.environ.get("POLY_SIG_TYPE", "0"))
        self.client = ClobClient(CLOB, key=key, chain_id=137,
                                 signature_type=sig_type, funder=funder)
        self.client.set_api_creds(self.client.create_or_derive_api_creds())
        self.journal = journal
        self.feed = feed
        self._hb_stop = threading.Event()
        threading.Thread(target=self._heartbeat, daemon=True).start()

    def _heartbeat(self):
        # keeps resting orders alive per CLOB heartbeat requirement
        while not self._hb_stop.is_set():
            try:
                self.client.get_ok()
            except Exception:
                pass
            time.sleep(4)

    def run_order(self, mkt, fav, level, queue_shares, shares, cancel_at, cfg):
        token = mkt["token_up"] if fav == "Up" else mkt["token_dn"]
        book = self.feed.book(token)
        order = self.client.create_order(self.OrderArgs(
            token_id=token, price=round(level, 3), size=round(shares, 2), side="BUY"),
            options=self.Opts(tick_size=str(book["tick"]), neg_risk=False))
        resp = self.client.post_order(order, self.OrderType.GTC)
        oid = resp.get("orderID")
        self.journal.write({"type": "order", "slug": mkt["slug"], "order_id": oid,
                            "level": level, "shares": shares, "mode": "live", "resp": str(resp)[:300]})
        if not oid:
            return 0.0, 0.0
        filled = 0.0
        while time.time() < cancel_at:
            time.sleep(1.5)
            try:
                o = self.client.get_order(oid)
                filled = float(o.get("size_matched", 0) or 0)
                if o.get("status") in ("MATCHED", "FILLED") or filled >= shares - 1e-6:
                    break
            except Exception as e:
                log.warning("order poll error: %s", e)
        try:
            self.client.cancel(oid)
        except Exception:
            pass
        self.journal.write({"type": "fill" if filled > 0 else "cancel", "slug": mkt["slug"],
                            "filled": filled, "level": level, "mode": "live"})
        return filled, float("nan")


# ----------------------------------------------------------------------------- bot
class Bot:
    def __init__(self, cfg):
        self.cfg = cfg
        self.s = http()
        self.feed = Feed(self.s)
        self.journal = Journal(cfg.data_dir)
        self.state = self.journal.load_state()
        if self.state.get("bankroll") is None:
            self.state["bankroll"] = cfg.bankroll
        if cfg.mode == "live":
            assert cfg.live_armed == "I-UNDERSTAND-REAL-MONEY", \
                "live mode requires live_armed confirmation in config"
            self.executor = LiveExecutor(self.feed, self.journal, cfg)
        else:
            self.executor = PaperExecutor(self.feed, self.journal)
        self.stop = threading.Event()

    # -- sizing ---------------------------------------------------------------
    def clip_usd(self):
        c = self.cfg
        clip = min(c.market_cap_usd, c.kelly_cap_frac * self.state["bankroll"])
        if c.adaptive_sizing and self.state["trailing_flow"]:
            flows = sorted(self.state["trailing_flow"])[-c.flow_window:]
            med_flow = flows[len(flows) // 2]
            clip = min(clip, max(c.min_clip_usd, c.flow_frac * med_flow))
        return max(c.min_clip_usd, clip)

    # -- one window -----------------------------------------------------------
    def handle_window(self, wts):
        cfg = self.cfg
        if self.state.get("paused"):
            self.journal.write({"type": "skip", "wts": wts, "reason": "kill_switch_paused"})
            return
        # discover (retry small)
        mkt = None
        for _ in range(3):
            try:
                mkt = self.feed.discover(wts)
                if mkt:
                    break
            except Exception as e:
                log.warning("discover error: %s", e)
            time.sleep(1.0)
        if not mkt:
            self.journal.write({"type": "skip", "wts": wts, "reason": "no_market"})
            return
        # wait for signal time
        sig_t = wts + cfg.t_signal
        while time.time() < sig_t and not self.stop.is_set():
            time.sleep(min(0.05, max(0.001, sig_t - time.time())))
        if self.stop.is_set():
            return
        try:
            up = self.feed.book(mkt["token_up"])
        except Exception as e:
            self.journal.write({"type": "skip", "wts": wts, "reason": f"book_error {e}"})
            return
        if up["bid"] is None or up["ask"] is None:
            self.journal.write({"type": "skip", "wts": wts, "reason": "one_sided_book"})
            return
        mid = (up["bid"] + up["ask"]) / 2.0
        spread = up["ask"] - up["bid"]
        rec = {"type": "signal_check", "wts": wts, "slug": mkt["slug"], "mid": mid,
               "bid": up["bid"], "ask": up["ask"], "spread": spread,
               "signal_lag_s": round(time.time() - sig_t, 3)}
        if spread > cfg.max_spread:
            rec["decision"] = "skip_wide_spread"
            self.journal.write(rec)
            return
        if cfg.band_lo <= mid <= cfg.band_hi:
            fav = "Up"
            level, queue = up["bid"], up["bid_sz"]
        elif cfg.band_lo <= 1 - mid <= cfg.band_hi:
            fav = "Down"
            try:
                dn = self.feed.book(mkt["token_dn"])
            except Exception as e:
                rec["decision"] = f"skip_dn_book_error"
                self.journal.write(rec)
                return
            if dn["bid"] is None:
                rec["decision"] = "skip_dn_no_bid"
                self.journal.write(rec)
                return
            level, queue = dn["bid"], dn["bid_sz"]
        else:
            rec["decision"] = "no_signal"
            self.journal.write(rec)
            return

        clip = self.clip_usd()
        shares = clip / level
        min_sh = max(up["min_size"], 5.0)
        if shares < min_sh:
            shares = min_sh
            clip = shares * level
        rec.update({"decision": "place", "fav": fav, "level": level,
                    "queue": queue, "clip_usd": round(clip, 2), "shares": round(shares, 2)})
        self.journal.write(rec)

        cancel_at = wts + cfg.t_cancel
        filled, qual_flow = self.executor.run_order(mkt, fav, level, queue, shares, cancel_at, cfg)
        if isinstance(qual_flow, float) and not math.isnan(qual_flow):
            self.state["trailing_flow"] = (self.state["trailing_flow"] + [qual_flow * level])[-200:]
        if filled <= 0:
            self.journal.write({"type": "no_fill", "wts": wts, "slug": mkt["slug"]})
            self.journal.save_state(self.state)
            return

        # settle
        close_t = wts + 300
        deadline = close_t + cfg.settle_grace_s
        outcome = None
        while time.time() < deadline:
            time.sleep(5.0)
            if time.time() < close_t:
                continue
            try:
                outcome = self.feed.winner(mkt["condition_id"])
            except Exception:
                outcome = None
            if outcome:
                break
        win = (outcome == fav) if outcome else None
        pnl_sh = (1.0 - level) if win else (-level) if win is not None else 0.0
        pnl = filled * pnl_sh if win is not None else 0.0
        self.state["bankroll"] += pnl
        if win is not None:
            self.state["trailing_pnl_sh"] = (self.state["trailing_pnl_sh"] + [pnl_sh])[-500:]
        self.journal.write({"type": "settle", "wts": wts, "slug": mkt["slug"], "fav": fav,
                            "level": level, "filled": filled, "outcome": outcome,
                            "win": win, "pnl_usd": round(pnl, 4),
                            "bankroll": round(self.state["bankroll"], 2)})
        # kill switch
        tp = self.state["trailing_pnl_sh"][-self.cfg.kill_trailing_n:]
        if len(tp) >= self.cfg.kill_trailing_n and sum(tp) / len(tp) < self.cfg.kill_min_ev:
            self.state["paused"] = True
            self.journal.write({"type": "kill_switch", "trailing_mean_pnl_sh": sum(tp) / len(tp),
                                "n": len(tp)})
            log.error("KILL SWITCH: trailing EV negative — paused. Reset state.json to resume.")
        self.journal.save_state(self.state)

    # -- main loop --------------------------------------------------------------
    def run(self):
        cfg = self.cfg
        skew = None
        try:
            skew = self.feed.clock_skew()
        except Exception as e:
            log.warning("clock check failed: %s", e)
        self.journal.write({"type": "start", "mode": cfg.mode, "clock_skew_s": skew,
                            "config": {k: getattr(cfg, k) for k in vars(cfg)}})
        if skew is not None and abs(skew) > cfg.max_clock_skew_s:
            log.error("clock skew %.2fs exceeds %.1fs — fix NTP first", skew, cfg.max_clock_skew_s)
            sys.exit(2)
        log.info("EFC bot started, mode=%s bankroll=%.2f", cfg.mode, self.state["bankroll"])
        while not self.stop.is_set():
            now = time.time()
            wts = int(now - now % 300 + 300)          # next window open
            wake = wts + 30                            # discover at +30s, well before signal
            while time.time() < wake and not self.stop.is_set():
                time.sleep(0.5)
            if self.stop.is_set():
                break
            t = threading.Thread(target=self._safe_handle, args=(wts,), daemon=True)
            t.start()
            # loop continues; next iteration waits for the following window

    def _safe_handle(self, wts):
        try:
            self.handle_window(wts)
        except Exception as e:
            log.exception("window %s failed", wts)
            self.journal.write({"type": "error", "wts": wts, "error": repr(e)})


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    cfg = Config.load(sys.argv[1] if len(sys.argv) > 1 else "config.json")
    bot = Bot(cfg)

    def _stop(*_):
        log.info("stopping...")
        bot.stop.set()
    os_signal.signal(os_signal.SIGINT, _stop)
    os_signal.signal(os_signal.SIGTERM, _stop)
    bot.run()


if __name__ == "__main__":
    main()
