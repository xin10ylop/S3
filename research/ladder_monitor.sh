#!/bin/bash
for i in $(seq 1 20); do
  echo "=== scan $i  $(date -u +%H:%M:%S) ==="
  python3 scan_ladder.py 0.05 5 2>&1 | grep -E "violations|TOTAL RISK-FREE|SELL A|BUY  B"
  sleep 150
done
