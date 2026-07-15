#!/usr/bin/env python3
"""Daily performance report from the EFC bot journal.

Usage: python3 report.py /opt/efc-bot/data [days]
Compares live/paper telemetry against the backtest benchmark bands and prints
PASS/WARN per metric. Non-zero exit if the kill switch fired.
"""
import json
import pathlib
import sys
import time
from collections import defaultdict

BENCH = {
    "fill_rate": (0.80, 0.98),     # backtest: ~91% of signals filled
    "win_rate": (0.87, 0.97),      # backtest: 91.2-93.9%
    "ev_c_sh": (1.0, 8.0),         # backtest: +2.5 to +5.6 c/share
    "signals_per_day": (4, 18),    # backtest: 8-10
}


def main():
    data_dir = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "./efc_data")
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 7
    rows = []
    for f in sorted(data_dir.glob("journal-*.jsonl"))[-days:]:
        with open(f) as fh:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    if not rows:
        print("no journal data")
        return
    byday = defaultdict(lambda: {"signals": 0, "placed": 0, "fills": 0, "settles": 0,
                                 "wins": 0, "pnl": 0.0, "pnl_sh_sum": 0.0, "sh": 0.0})
    killed = False
    for r in rows:
        day = time.strftime("%Y-%m-%d", time.gmtime(r.get("ts", 0)))
        d = byday[day]
        t = r.get("type")
        if t == "signal_check":
            d["signals"] += 1
            if r.get("decision") == "place":
                d["placed"] += 1
        elif t == "settle":
            d["settles"] += 1
            d["pnl"] += r.get("pnl_usd", 0.0)
            filled = r.get("filled", 0.0)
            if r.get("win"):
                d["wins"] += 1
                d["pnl_sh_sum"] += (1 - r.get("level", 0)) * filled
            elif r.get("win") is False:
                d["pnl_sh_sum"] += -r.get("level", 0) * filled
            d["sh"] += filled
        elif t == "kill_switch":
            killed = True

    print(f"{'day':<12}{'placed':>7}{'fills':>7}{'fill%':>7}{'win%':>7}{'EV c/sh':>9}{'PnL $':>9}")
    tot = defaultdict(float)
    for day in sorted(byday):
        d = byday[day]
        fr = d["settles"] / d["placed"] if d["placed"] else 0
        wr = d["wins"] / d["settles"] if d["settles"] else 0
        ev = 100 * d["pnl_sh_sum"] / d["sh"] if d["sh"] else 0
        print(f"{day:<12}{d['placed']:>7}{d['settles']:>7}{fr*100:>6.0f}%{wr*100:>6.0f}%{ev:>9.2f}{d['pnl']:>9.2f}")
        for k in ["placed", "settles", "wins", "pnl", "pnl_sh_sum", "sh"]:
            tot[k] += d[k]
    n_days = max(len(byday), 1)
    fr = tot["settles"] / tot["placed"] if tot["placed"] else 0
    wr = tot["wins"] / tot["settles"] if tot["settles"] else 0
    ev = 100 * tot["pnl_sh_sum"] / tot["sh"] if tot["sh"] else 0
    print(f"{'TOTAL':<12}{int(tot['placed']):>7}{int(tot['settles']):>7}{fr*100:>6.0f}%{wr*100:>6.0f}%{ev:>9.2f}{tot['pnl']:>9.2f}")
    print("\nbenchmark check (backtest bands):")
    checks = [("fill_rate", fr), ("win_rate", wr), ("ev_c_sh", ev),
              ("signals_per_day", tot["placed"] / n_days)]
    for name, v in checks:
        lo, hi = BENCH[name]
        status = "PASS" if lo <= v <= hi else "WARN"
        print(f"  {name:<16} {v:>7.2f}   band [{lo}, {hi}]   {status}")
    if killed:
        print("\n*** KILL SWITCH FIRED — bot paused. Investigate before resuming. ***")
        sys.exit(1)


if __name__ == "__main__":
    main()
