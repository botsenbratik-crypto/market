#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$DIR")"
cd "$DIR"

export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"

# --- python / venv ---
if [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python" ]]; then
  PYTHON="${VIRTUAL_ENV}/bin/python"
elif [[ -x "${ROOT}/.venv/bin/python" ]]; then
  PYTHON="${ROOT}/.venv/bin/python"
else
  echo "📦 Creating venv at ${ROOT}/.venv ..."
  python3 -m venv "${ROOT}/.venv"
  PYTHON="${ROOT}/.venv/bin/python"
  "$PYTHON" -m pip install -U pip setuptools wheel
  "$PYTHON" -m pip install -r requirements.txt
fi

echo "🐍 Python: $PYTHON"

# --- deps ---
if ! "$PYTHON" -c "from aiogram import Bot" 2>/dev/null; then
  echo "📥 Installing dependencies..."
  "$PYTHON" -m pip install -r requirements.txt
fi

# --- config ---
if [[ ! -f ".env" ]]; then
  echo "❌ Missing .env — copy .env.example and set BOT_TOKEN"
  exit 1
fi

if ! "$PYTHON" -c "
import sys
sys.path.insert(0, '${ROOT}')
from market_bot.config import get_settings
get_settings()
print('✅ Config OK')
" 2>&1; then
  echo "❌ Config error — check BOT_TOKEN in .env"
  exit 1
fi

echo "🚀 Starting bot..."
exec "$PYTHON" main.py
