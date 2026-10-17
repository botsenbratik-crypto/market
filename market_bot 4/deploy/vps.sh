#!/usr/bin/env bash
# Deploy market_bot to Vultr VPS. Run from Mac:
#   cd /Users/valentynpevchenko/Documents/market_bot/deploy
#   ./vps.sh
set -euo pipefail

VPS_HOST="${VPS_HOST:-root@207.148.111.8}"
REMOTE_ROOT="/root/Documents"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BOT_DIR="$(dirname "$SCRIPT_DIR")"
ROOT="$(dirname "$BOT_DIR")"

echo "📡 VPS: $VPS_HOST"
echo "📂 Local: $BOT_DIR"

if [[ ! -f "$BOT_DIR/.env" ]]; then
  echo "❌ Missing $BOT_DIR/.env (BOT_TOKEN and API keys)"
  exit 1
fi

echo "📤 Syncing market_bot..."
rsync -avz --delete \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude '.DS_Store' \
  "$BOT_DIR/" "$VPS_HOST:$REMOTE_ROOT/market_bot/"

echo "🔧 Remote setup..."
ssh "$VPS_HOST" bash -s <<REMOTE
set -euo pipefail
REMOTE_ROOT="$REMOTE_ROOT"
BOT_DIR="\$REMOTE_ROOT/market_bot"

cd "\$REMOTE_ROOT"

if [[ ! -d .venv ]]; then
  echo "📦 Creating venv..."
  python3 -m venv .venv
fi

echo "📥 Installing deps..."
.venv/bin/pip install -q -U pip setuptools wheel
.venv/bin/pip install -q -r "\$BOT_DIR/requirements.txt"

echo "✅ Config check..."
export PYTHONPATH="\$REMOTE_ROOT"
.venv/bin/python -c "
import sys
sys.path.insert(0, '\$REMOTE_ROOT')
from market_bot.config import get_settings
get_settings()
print('Config OK')
"

systemctl stop crypto-bot 2>/dev/null || true
systemctl disable crypto-bot 2>/dev/null || true
systemctl stop market-bot 2>/dev/null || true

cp "\$BOT_DIR/deploy/market-bot.service" /etc/systemd/system/market-bot.service
systemctl daemon-reload
systemctl enable market-bot
systemctl restart market-bot

sleep 2
systemctl --no-pager status market-bot
REMOTE

echo ""
echo "✅ Deployed. Bot: @info139_bot — test /get btc in Telegram"
