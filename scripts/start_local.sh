#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi

./.venv/bin/python -m pip install -r requirements.txt >/tmp/spot-delivery-pip.log 2>&1 || {
  echo "Dependency install failed. See /tmp/spot-delivery-pip.log"
  exit 1
}

if lsof -nP -iTCP:3040 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port 3040 already in use. Stop first with scripts/stop_local.sh"
  exit 1
fi

echo "Starting Spot Delivery in foreground on http://127.0.0.1:3040"
echo "Press Ctrl+C to stop."
exec ./.venv/bin/python app.py
