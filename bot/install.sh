#!/usr/bin/env bash
# EFC bot installer — run as root on a fresh Ubuntu/Debian server.
#   bash install.sh
# Installs to /opt/efc-bot, creates a systemd service (paper mode), starts it.
set -euo pipefail

DIR=/opt/efc-bot
mkdir -p "$DIR/data"
cp efc_bot.py report.py "$DIR/"
[ -f "$DIR/config.json" ] || cp config.json "$DIR/"

apt-get update -q && apt-get install -y -q python3-venv python3-pip chrony
systemctl enable --now chrony || true   # NTP: the bot refuses to run with >1.5s skew

python3 -m venv "$DIR/venv"
"$DIR/venv/bin/pip" -q install requests
# py-clob-client only needed for live mode; pre-install so the switch is config-only
"$DIR/venv/bin/pip" -q install py-clob-client || echo "WARN: py-clob-client install failed (only needed for live mode)"

cat > /etc/systemd/system/efc-bot.service << 'EOF'
[Unit]
Description=EFC Polymarket paper-trading bot
After=network-online.target chrony.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/efc-bot
ExecStart=/opt/efc-bot/venv/bin/python3 /opt/efc-bot/efc_bot.py /opt/efc-bot/config.json
Restart=always
RestartSec=10
# For LIVE mode later: put POLY_PRIVATE_KEY=... in /opt/efc-bot/env (chmod 600)
EnvironmentFile=-/opt/efc-bot/env

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now efc-bot
sleep 3
systemctl --no-pager status efc-bot | head -12
echo
echo "Installed. Useful commands:"
echo "  journalctl -u efc-bot -f                      # live logs"
echo "  /opt/efc-bot/venv/bin/python3 /opt/efc-bot/report.py /opt/efc-bot/data   # daily report"
echo "  tail -f /opt/efc-bot/data/journal-\$(date -u +%F).jsonl                   # raw journal"
