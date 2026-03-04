#!/usr/bin/env bash
set -euo pipefail

if lsof -nP -iTCP:3040 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Spot Delivery is running on 3040"
  lsof -nP -iTCP:3040 -sTCP:LISTEN
  curl -s -o /dev/null -w 'HTTP %{http_code}\n' http://127.0.0.1:3040/
else
  echo "Spot Delivery is not running"
fi
