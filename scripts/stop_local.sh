#!/usr/bin/env bash
set -euo pipefail

PID_FILE="/tmp/spot-delivery.pid"

if [ -f "$PID_FILE" ]; then
  PID=$(cat "$PID_FILE")
  if kill -0 "$PID" >/dev/null 2>&1; then
    kill "$PID" || true
  fi
  rm -f "$PID_FILE"
fi

lsof -nP -iTCP:3040 -sTCP:LISTEN | awk 'NR>1 {print $2}' | xargs -r kill || true

echo "Spot Delivery stopped."
